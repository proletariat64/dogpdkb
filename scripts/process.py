"""Run bounded commands and reap their process group on cancellation."""

import os
import signal
import subprocess
from pathlib import Path


def descendant_groups(pid):
    groups, pending = {pid}, [pid]
    while pending:
        parent = pending.pop()
        try:
            children = (
                Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
            )
        except OSError:
            continue
        for child in map(int, children):
            try:
                group = os.getpgid(child)
            except ProcessLookupError:
                continue
            if group != os.getpgrp():
                groups.add(group)
            pending.append(child)
    return groups


def run(arguments, *, timeout, check=False, **kwargs):
    with subprocess.Popen(arguments, start_new_session=True, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            groups = descendant_groups(process.pid)
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                for group in groups:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.communicate()
            raise
        result = subprocess.CompletedProcess(
            arguments, process.returncode, stdout, stderr
        )
        if check:
            result.check_returncode()
        return result
