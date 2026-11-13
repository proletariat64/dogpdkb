"""Public mdcompare help and diagnostic contracts, exercised as subprocesses."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"


def invoke(tmp_path, *arguments):
    environment = os.environ.copy()
    environment.update(
        PYTHONPATH=str(SOURCE),
        PYTHONDONTWRITEBYTECODE="1",
        HOME=str(tmp_path / "home"),
    )
    return subprocess.run(
        [sys.executable, "-m", "mdcompare", *arguments],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_argument_error_identifies_missing_flags_and_scoped_help(tmp_path):
    result = invoke(tmp_path, "compare")
    assert result.returncode == 1
    assert not result.stdout
    assert "INVALID_ARGUMENTS" in result.stderr
    assert "--baseline" in result.stderr
    assert "--incoming" in result.stderr
    assert "mdcompare compare --help" in result.stderr


@pytest.mark.parametrize("command", [None, "catalog", "compare", "knowledge"])
def test_full_help_is_local_scoped_and_available_with_both_flags(tmp_path, command):
    prefix = [command] if command else []
    short = invoke(tmp_path, *prefix, "-h", "--full")
    long = invoke(tmp_path, *prefix, "--help", "--full")
    assert short.returncode == long.returncode == 0
    assert short.stdout == long.stdout
    assert not short.stderr and not long.stderr
    assert "Complete local contract" in long.stdout
    assert "schema_version" in long.stdout
    assert "Exit codes" in long.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["catalog", "compare", "knowledge"])
def test_minimal_json_in_leaf_help_runs_against_real_local_markdown(tmp_path, command):
    help_result = invoke(tmp_path, command, "--help")
    assert help_result.returncode == 0
    assert "JSON" in help_result.stdout
    assert "Exit codes" in help_result.stdout
    assert "stderr" in help_result.stdout
    manifest, _ = json.JSONDecoder().raw_decode(
        help_result.stdout[help_result.stdout.index("{") :]
    )
    (tmp_path / "document.md").write_text("# Limits\n\nThe daily limit is 10.\n")
    (tmp_path / "old.json").write_text(json.dumps(manifest))
    (tmp_path / "new.json").write_text(json.dumps(manifest))
    args = (
        ["--input", "new.json"]
        if command == "catalog"
        else ["--baseline", "old.json", "--incoming", "new.json"]
    )
    result = invoke(tmp_path, command, *args, "--output", "result.json")
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["schema_version"] == (
        "knowledge-1.0" if command == "knowledge" else "1.0"
    )


def test_full_without_help_is_rejected_before_output_creation(tmp_path):
    result = invoke(tmp_path, "catalog", "--full")
    assert result.returncode == 1
    assert "--full requires -h or --help" in result.stderr
    assert not result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("arguments", "detail"),
    [
        (["unknown"], "invalid choice"),
        (["catalog", "--input", "x", "--output", "y", "--bogus"], "--bogus"),
        (
            ["compare", "--max-file-bytes", "0"],
            "File limit must be positive",
        ),
    ],
)
def test_usage_errors_keep_detail_without_creating_artifacts(
    tmp_path, arguments, detail
):
    result = invoke(tmp_path, *arguments)
    assert result.returncode == 1
    assert "INVALID_ARGUMENTS" in result.stderr
    assert detail in result.stderr
    assert "--help" in result.stderr
    assert not result.stdout
    assert not list(tmp_path.iterdir())
