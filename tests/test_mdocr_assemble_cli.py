import os
import subprocess
import sys
from pathlib import Path

import pytest

from mdocr.assemble import _validate_attempts


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


def test_attempt_history_accepts_known_failed_retry_but_not_clean_repair():
    settings = {"request_budget": 2, "mermaid_repair_rounds": 1}
    patch = {"kind": "text", "markdown": "OCR text", "uncertainty": ""}
    first = {"key": "key", "status": "failed", "reason": "response_json_invalid"}
    success = {"key": "key", "status": "success", "patches": [patch]}
    journal = {"requests": [first, success], "budget": {"requests": 2}, "documents": []}
    _validate_attempts(journal, settings)
    journal["requests"] = [success, {**success, "repair_round": 1}]
    with pytest.raises(ValueError, match="invalid journal attempt history"):
        _validate_attempts(journal, settings)
    warning = ["response_mermaid_syntax_invalid"]
    journal["requests"][0] = {
        **success,
        "warnings": warning,
        "patches": [{**patch, "warnings": warning}],
    }
    _validate_attempts(journal, settings)
    journal["requests"][0] = {"key": "key", "status": "unknown"}
    journal["requests"][1] = success
    with pytest.raises(ValueError, match="invalid journal attempt history"):
        _validate_attempts(journal, settings)


def test_attempt_history_preserves_fix_cap_across_ordinary_retry():
    settings = {"request_budget": 4, "fix": 2}
    failed = {"key": "key", "status": "failed", "reason": "response_json_invalid"}
    fix = {**failed, "fix_round": 1}
    journal = {
        "requests": [failed, fix, failed, {**fix, "fix_round": 2}],
        "budget": {"requests": 4},
        "documents": [],
    }
    _validate_attempts(journal, settings)
    journal["requests"][-1] = fix
    with pytest.raises(ValueError, match="invalid journal attempt history"):
        _validate_attempts(journal, settings)


def test_failed_repair_does_not_enable_ordinary_retry():
    settings = {"request_budget": 3, "mermaid_repair_rounds": 1}
    warning = ["response_mermaid_syntax_invalid"]
    patch = {
        "kind": "text",
        "markdown": "OCR text",
        "uncertainty": "",
        "warnings": warning,
    }
    success = {
        "key": "key",
        "status": "success",
        "warnings": warning,
        "patches": [patch],
    }
    failed = {
        "key": "key",
        "status": "failed",
        "reason": "response_json_invalid",
        "repair_round": 1,
    }
    journal = {
        "requests": [success, failed, success],
        "budget": {"requests": 3},
        "documents": [],
    }
    with pytest.raises(ValueError, match="invalid journal attempt history"):
        _validate_attempts(journal, settings)
