from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def cli(cwd: Path, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env.pop("PADDLEOCR_TOKEN", None)
    return subprocess.run(
        [PYTHON, "-m", "mdocr.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_plan_freezes_occurrences_and_does_not_publish_output(tmp_path):
    source = tmp_path / "source.md"
    source.write_text(
        "before ![a](pic.png) `![literal](x.png)`\n```md\n![code](y.png)\n```\n![again](pic.png)\n",
        encoding="utf-8",
    )
    (tmp_path / "pic.png").write_bytes(b"image bytes")
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "paddle",
        "--input",
        str(source),
        "--output",
        str(tmp_path / "out.md"),
        "--plan",
        str(tmp_path / "plan.json"),
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["schema_version"] == "mdocr-plan-2"
    assert len(plan["documents"][0]["images"]) == 2
    first, second = plan["documents"][0]["images"]
    text = source.read_text()
    assert text[first["start"] : first["end"]] == "![a](pic.png)"
    assert text[second["start"] : second["end"]] == "![again](pic.png)"
    assert first["resource_id"] == "pic.png"
    assert not (tmp_path / "out.md").exists()


def test_public_help_exposes_plan_and_run_without_provider_setup(tmp_path):
    result = cli(tmp_path, "--help")
    assert result.returncode == 0
    assert "plan" in result.stdout and "run" in result.stdout
    assert not result.stderr
    assert not list(tmp_path.iterdir())


def test_plan_records_remote_reference_without_network_or_a_witness(tmp_path):
    source = tmp_path / "raw.md"
    source.write_bytes(b"Body\r\n![picture](https://example.invalid/a.png)\r\n")
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "llm",
        "--input",
        "raw.md",
        "--output",
        "out.md",
        "--plan",
        "plan.json",
    )
    assert result.returncode == 0, result.stderr
    (doc,) = json.loads((tmp_path / "plan.json").read_bytes())["documents"]
    import hashlib

    assert doc["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    (image,) = doc["images"]
    assert image["start"] == 6
    assert image["source_path"] is None
    assert image["error"] == "REMOTE_IMAGE_NOT_DOWNLOADED"


def test_plan_cannot_overwrite_its_list_input(tmp_path):
    source = tmp_path / "raw.md"
    source.write_text("# Body\n")
    listing = tmp_path / "list.json"
    listing.write_text('["raw.md"]')
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "paddle",
        "--list",
        "list.json",
        "--output-dir",
        "out",
        "--plan",
        "list.json",
    )
    assert result.returncode == 1
    assert "overlaps" in result.stderr
    assert listing.read_text() == '["raw.md"]'
