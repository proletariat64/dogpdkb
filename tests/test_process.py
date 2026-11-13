"""Process timeouts must stop descendants, not just their shell."""

import subprocess
import sys
import time

import pytest

from scripts.process import run


def test_timeout_stops_background_child(tmp_path):
    marker = tmp_path / "escaped"
    child = (
        f"import time,pathlib; time.sleep(0.6); pathlib.Path({str(marker)!r}).touch()"
    )
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", parent], timeout=0.2)
    time.sleep(0.7)
    assert not marker.exists()


def test_timeout_stops_descendant_in_separate_session(tmp_path):
    marker = tmp_path / "escaped-session"
    child = f"import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(.6);pathlib.Path({str(marker)!r}).touch();time.sleep(30)"
    parent = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True);time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", parent], timeout=0.2)
    time.sleep(0.7)
    assert not marker.exists()


def test_failure_keeps_diagnostics():
    with pytest.raises(subprocess.CalledProcessError) as error:
        run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('missing cache'); sys.exit(2)",
            ],
            timeout=2,
            check=True,
            stderr=subprocess.PIPE,
            text=True,
        )
    assert error.value.stderr == "missing cache"
