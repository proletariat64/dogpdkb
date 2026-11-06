"""Run the dependency-free browser-component contracts when Node is available."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js required for site tests"
)
@pytest.mark.parametrize("component", ["release", "catalog"])
def test_site_component(component):
    subprocess.run(
        ["node", "--test", str(ROOT / f"tests/site_{component}.test.cjs")],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
