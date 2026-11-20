import os
import subprocess
import sys
from pathlib import Path


def cli(cwd, *args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    return subprocess.run(
        [sys.executable, "-m", "mdocr.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_assemble_rejects_existing_destination_and_needs_no_auth(tmp_path):
    result = cli(
        tmp_path,
        "assemble",
        "--plan",
        "missing",
        "--journal",
        "missing",
        "--execution-version",
        "0.3.4",
        "--output-dir",
        "out",
    )
    assert result.returncode == 1
    assert "assemble" in result.stderr
    (tmp_path / "out").mkdir()
    result = cli(
        tmp_path,
        "assemble",
        "--plan",
        "missing",
        "--journal",
        "missing",
        "--execution-version",
        "0.3.4",
        "--output-dir",
        "out",
    )
    assert result.returncode == 1
    assert "must be new" in result.stderr
    assert list((tmp_path / "out").iterdir()) == []


def test_assemble_help_is_offline():
    result = cli(Path.cwd(), "assemble", "--help")
    assert result.returncode == 0
    assert "--execution-version" in result.stdout
