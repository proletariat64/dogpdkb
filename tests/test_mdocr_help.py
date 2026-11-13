"""Public mdocr subprocess contracts; no gateway or model calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path, *arguments):
    env = os.environ.copy()
    env.pop("PADDLEOCR_TOKEN", None)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    return subprocess.run(
        [sys.executable, "-m", "mdocr.cli", *arguments],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def test_root_help_has_local_full_contract(tmp_path):
    result = run_cli(tmp_path, "--help", "--full")
    assert result.returncode == 0, result.stderr
    assert "Full contract" in result.stdout
    assert "mdocr batch --help --full" in result.stdout
    assert "Prepared image" in result.stdout
    assert "OCR block" in result.stdout
    assert not result.stderr


@pytest.mark.parametrize("command", ["prepare", "ocr"])
def test_markdown_command_help_explains_first_invocation_and_effects(tmp_path, command):
    result = run_cli(tmp_path, command, "--help")
    assert result.returncode == 0, result.stderr
    text = result.stdout
    for required in (
        f"mdocr {command} -f doc.md -o out",
        "Prepared image",
        "stdout",
        "stderr",
        "Exit codes",
        "Next steps",
        "--help --full",
    ):
        assert required in text
    assert "re-OCR" not in text if command == "prepare" else "PADDLEOCR_TOKEN" in text
    assert not result.stderr


def test_batch_help_contains_a_valid_manifest_and_partial_recovery(tmp_path):
    result = run_cli(tmp_path, "batch", "--help")
    assert result.returncode == 0, result.stderr
    text = " ".join(result.stdout.split())
    for required in (
        '"schema_version":"mdocr-batch-input-1.0"',
        '"document_id":"doc-1"',
        '"path":"doc.md"',
        "relative to the manifest",
        "900",
        "PADDLEOCR_TOKEN",
        "partial",
        "reused",
        "Exit codes",
        "Next steps",
    ):
        assert required in text
    assert not result.stderr


@pytest.mark.parametrize("command", [None, "prepare", "ocr", "batch"])
def test_short_and_long_scoped_help_match_without_writes(tmp_path, command):
    prefix = [command] if command else []
    ordinary = run_cli(tmp_path, *prefix, "--help")
    short = run_cli(tmp_path, *prefix, "-h")
    full = run_cli(tmp_path, *prefix, "--help", "--full")
    full_short = run_cli(tmp_path, *prefix, "-h", "--full")
    for result in (ordinary, short, full, full_short):
        assert result.returncode == 0, result.stderr
        assert not result.stderr
    assert ordinary.stdout == short.stdout
    assert full.stdout == full_short.stdout
    assert "Full contract" in full.stdout
    assert len(full.stdout) > len(ordinary.stdout)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", [None, "prepare", "ocr", "batch"])
def test_full_requires_help_before_processing(tmp_path, command):
    prefix = [command] if command else []
    result = run_cli(tmp_path, *prefix, "--full")
    assert result.returncode == 2
    assert not result.stdout
    assert "--full requires -h or --help" in result.stderr
    assert not list(tmp_path.iterdir())


def test_batch_help_example_runs_with_manifest_relative_paths(tmp_path):
    help_result = run_cli(tmp_path, "batch", "--help")
    start = help_result.stdout.index('{"schema_version"')
    manifest, _ = json.JSONDecoder().raw_decode(help_result.stdout[start:])
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "doc.md").write_text("# Offline example\n", encoding="utf-8")
    (inputs / "input.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = run_cli(
        tmp_path,
        "batch",
        "--manifest",
        "inputs/input.json",
        "--output-dir",
        "out",
        "--report",
        "report.json",
    )
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["schema_version"] == "mdocr-batch-1.0"
    assert report["status"] == "completed"
    assert report["documents"][0]["path"] == str(inputs / "doc.md")
    assert Path(report["documents"][0]["output"]).read_text() == "# Offline example\n"


def test_batch_full_help_explains_complete_report_and_limit_boundaries(tmp_path):
    result = run_cli(tmp_path, "batch", "--help", "--full")
    text = " ".join(result.stdout.split())
    for required in (
        "mdocr-batch-1.0",
        "prepared-jpeg-ocr-v1",
        "ocr.preprocessing",
        "ocr.configuration",
        "ocr.images",
        "unattempted",
        "unselected",
        "RESOURCE_NOT_FOUND",
        "BATCH_DEADLINE_EXCEEDED",
        "DOCUMENT_READ_FAILED",
        "OUTPUT_DIR/.cache",
        "null",
        "NaN",
        "not as a hard kill",
        "an older report may remain",
    ):
        assert required in text


def test_version_works_without_configuration_or_operational_files(tmp_path):
    result = run_cli(tmp_path, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("mdocr ")
    assert not result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["prepare", "ocr", "batch"])
def test_argument_errors_identify_scope_and_missing_input(tmp_path, command):
    result = run_cli(tmp_path, command)
    assert result.returncode == 2
    assert not result.stdout
    assert "the following arguments are required" in result.stderr
    assert f"Try '{'mdocr ' + command} --help'" in result.stderr
    assert not list(tmp_path.iterdir())


def test_batch_partial_and_usage_both_exit_two_with_distinct_evidence(tmp_path):
    (tmp_path / "doc.md").write_text("![missing](absent.png)\n", encoding="utf-8")
    (tmp_path / "input.json").write_text(
        json.dumps(
            {
                "schema_version": "mdocr-batch-input-1.0",
                "documents": [{"document_id": "doc-1", "path": "doc.md"}],
            }
        ),
        encoding="utf-8",
    )
    partial = run_cli(
        tmp_path,
        "batch",
        "--manifest",
        "input.json",
        "--output-dir",
        "out",
        "--report",
        "report.json",
    )
    assert partial.returncode == 2
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "partial"
    document = report["documents"][0]
    assert document["resources"][0]["status"] == "failed"
    assert "absent.png" in Path(document["output"]).read_text()
    usage = run_cli(
        tmp_path, "batch", "--output-dir", "elsewhere", "--report", "new.json"
    )
    assert usage.returncode == 2
    assert "--manifest" in usage.stderr
    assert not (tmp_path / "new.json").exists()
    assert not (tmp_path / "elsewhere").exists()
