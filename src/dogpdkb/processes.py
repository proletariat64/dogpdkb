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
                env={**os.environ, **(env or {})},
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
