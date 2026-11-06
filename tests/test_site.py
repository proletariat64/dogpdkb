"""Run the dependency-free browser-component contracts when Node is available."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js required for site tests"
)
def test_release_component():
    subprocess.run(
        ["node", "--test", str(ROOT / "tests/site_release.test.cjs")],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
