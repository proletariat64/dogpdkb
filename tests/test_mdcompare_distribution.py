"""Distribution acceptance through the installed-smoke command boundary."""

import io
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installation_gate_rejects_distribution_without_mdcompare(tmp_path):
    """A packaging omission must fail clearly before an installation can be accepted."""
    version_match = re.search(
        r'^version = "([^"\n]+)"', (ROOT / "pyproject.toml").read_text(), re.MULTILINE
    )
    assert version_match is not None
    version = version_match.group(1)
    wheel = tmp_path / f"dogpdkb-{version}-py3-none-any.whl"
    sdist = tmp_path / f"dogpdkb-{version}.tar.gz"
    with zipfile.ZipFile(wheel, "w") as binary, tarfile.open(sdist, "w:gz") as source:
        for path in (ROOT / "src").rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT / "src")
            if relative.parts[0] == "mdcompare":
                continue
            data = path.read_bytes()
            binary.writestr(str(relative), data)
            member = tarfile.TarInfo(f"dogpdkb-{version}/src/{relative}")
            member.size = len(data)
            source.addfile(member, io.BytesIO(data))
    env = {**os.environ, "UV_OFFLINE": "1", "UV_CACHE_DIR": str(tmp_path / "cache")}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/smoke_install.py"),
            "--dist",
            str(tmp_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "distribution is missing mdcompare modules" in result.stderr
