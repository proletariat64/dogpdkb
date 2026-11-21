"""Public CLI execution with bounded memory and ordinary live diagnostics."""

from __future__ import annotations

import codecs
import os
import re
import selectors
import shlex
import signal
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from dogpdkb.lifecycle import runtime_lock_fds
from dogpdkb.records import lock_fds


def diagnostic_argv(argv: list[str]) -> list[str]:
    result, redact = [], False
    secret = re.compile(
        r"(?:token|password|secret|api[-_]?key|authorization)", re.IGNORECASE
    )
    for value in argv:
        if redact:
            result.append("[redacted]")
            redact = False
        elif value.startswith("-") and secret.search(value):
            name, separator, _ = value.partition("=")
            result.append(name + "=[redacted]" if separator else name)
            redact = not separator
        else:
            result.append(
                re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", value)
            )
    return result


def run_command(
    argv: list[str],
    *,
    log: Path | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    inherit_env: bool = True,
) -> int:
    """Stream both child streams to stderr; return 124 timeout, 130/143 signals."""
    if not argv or any(not isinstance(arg, str) for arg in argv):
        raise ValueError("command must be a nonempty argv list")
    if log is not None:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") if log else nullcontext(None) as stream:
        if stream:
            stream.write(f"command: {shlex.join(diagnostic_argv(argv))}\n")
            stream.flush()
        try:
            child = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=lock_fds() + runtime_lock_fds(),
                env={**os.environ, **(env or {})} if inherit_env else env or {},
            )
        except OSError as error:
            message = f"cannot start {argv[0]}: {error}\n"
            sys.stderr.write(message)
            if stream:
                stream.write(message)
            return 127
        received = 0
        terminating = None
        previous = {}

        def stop(signum, _frame):
            nonlocal received, terminating
            received = received or signum
            terminating = terminating or time.monotonic()
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, stop)
            started, expired = time.monotonic(), False
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map() or child.poll() is None:
                    elapsed = time.monotonic() - started
                    if (
                        timeout is not None
                        and elapsed >= timeout
                        and terminating is None
                    ):
                        expired = True
                        stop(signal.SIGTERM, None)
                    if terminating is not None and time.monotonic() - terminating >= 2:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    for key, _ in selector.select(0.1):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        text = decoder.decode(chunk, final=not chunk)
                        sys.stderr.write(text)
                        sys.stderr.flush()
                        if stream:
                            stream.write(text)
                            stream.flush()
                code = child.wait()
            return (
                124
                if expired
                else 128 + received
                if received
                else 128 - code
                if code < 0
                else code
            )
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            child.stdout.close()
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def run_commands(commands, *, concurrency, log=None, env=None):
    """Yield settled (index, exit code) pairs; coordinate children on one thread.

    The command iterator may checkpoint an attempt before yielding its argv.
    Close this iterator when abandoning its results to stop every active group.
    """
    if concurrency not in {1, 2, 3}:
        raise ValueError("download concurrency must be between 1 and 3")
    if log is not None:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
    active, previous = {}, {}
    received, terminating = 0, None

    def stop(signum, _frame):
        nonlocal received, terminating
        received = received or signum
        terminating = terminating or time.monotonic()
        for job in active.values():
            job["interrupted"] = job["interrupted"] or job["child"].poll() is None
            try:
                os.killpg(job["child"].pid, signum)
            except ProcessLookupError:
                pass

    with (
        log.open("a", encoding="utf-8") if log else nullcontext(None) as stream,
        selectors.DefaultSelector() as selector,
    ):
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, stop)
            pending = enumerate(commands)
            exhausted = False
            while active or not exhausted:
                while not received and not exhausted and len(active) < concurrency:
                    try:
                        index, argv = next(pending)
                    except StopIteration:
                        exhausted = True
                        break
                    if received:
                        break
                    if not argv or any(not isinstance(arg, str) for arg in argv):
                        raise ValueError("command must be a nonempty argv list")
                    if stream:
                        stream.write(f"command: {shlex.join(diagnostic_argv(argv))}\n")
                        stream.flush()
                    try:
                        child = subprocess.Popen(
                            argv,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                            pass_fds=lock_fds() + runtime_lock_fds(),
                            env={**os.environ, **(env or {})},
                        )
                    except OSError as error:
                        message = f"cannot start {argv[0]}: {error}\n"
                        sys.stderr.write(message)
                        if stream:
                            stream.write(message)
                            stream.flush()
                        yield index, 127
                        continue
                    active[index] = {
                        "child": child,
                        "decoder": codecs.getincrementaldecoder("utf-8")("replace"),
                        "interrupted": False,
                    }
                    selector.register(child.stdout, selectors.EVENT_READ, index)
                    if received:
                        stop(received, None)
                if received:
                    exhausted = True
                if terminating is not None and time.monotonic() - terminating >= 2:
                    for job in active.values():
                        try:
                            os.killpg(job["child"].pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                for key, _ in selector.select(0.1) if active else []:
                    job = active[key.data]
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    text = job["decoder"].decode(chunk, final=not chunk)
                    sys.stderr.write(text)
                    sys.stderr.flush()
                    if stream:
                        stream.write(text)
                        stream.flush()
                for index, job in list(active.items()):
                    child = job["child"]
                    if (
                        child.poll() is None
                        or child.stdout.fileno() in selector.get_map()
                    ):
                        continue
                    code = child.wait()
                    child.stdout.close()
                    del active[index]
                    if not job["interrupted"]:
                        yield index, 128 - code if code < 0 else code
            if received:
                raise InterruptedError(128 + received)
        finally:
            for job in active.values():
                child = job["child"]
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
                child.stdout.close()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
