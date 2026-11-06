"""Run bounded commands and reap their process group on cancellation."""

import os
import signal
import subprocess


def run(arguments, *, timeout, check=False, **kwargs):
    with subprocess.Popen(arguments, start_new_session=True, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
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
