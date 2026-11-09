"""Observable text comparison and bounded explanation through the CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def document(path):
    return {
        "knowledge_base": "book",
        "document_id": "1",
        "content_kind": "raw",
        "path": str(path),
        "resources": [],
        "metadata": {
            "name": "Name",
            "logical_path": "folder",
            "source_ref": "source:1",
        },
    }


def invoke(tmp_path, baseline, incoming, *extra):
    for name, docs in (("baseline", baseline), ("incoming", incoming)):
        (tmp_path / (name + ".json")).write_text(
            json.dumps({"schema_version": "1.0", "documents": docs})
        )
    output = tmp_path / "decisions.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "compare",
            "--baseline",
            str(tmp_path / "baseline.json"),
            "--incoming",
            str(tmp_path / "incoming.json"),
            "--output",
            str(output),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src")),
    )
    return result, json.loads(output.read_text()) if output.exists() else None


def test_changed_money_has_located_line_and_character_evidence(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("Limit\n100k TRY\n")
    new.write_text("Limit\n300k TRY\n")
    result, report = invoke(
        tmp_path, [document(old)], [document(new)], "--include-evidence"
    )
    assert result.returncode == 0, result.stderr
    assert report is not None
    text = report["results"][0]["text"]
    assert text["status"] == "CHANGED"
    assert text["comparison_complete"] is True
    region = text["differences"][0]
    assert region["old_start_line"] == region["new_start_line"] == 2
    assert region["removed"] == "100k TRY\n"
    assert region["added"] == "300k TRY\n"
    assert [(e["operation"], e["text"]) for e in region["characters"]] == [
        ("delete", "1"),
        ("insert", "3"),
    ]
    assert "100k" not in result.stdout + result.stderr


def test_newline_normalization_is_explicit_and_versioned(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_bytes(b"first\r\nsecond\rthird\n")
    new.write_bytes(b"first\nsecond\nthird\n")
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 0
    assert report is not None
    assert report["results"][0]["text"]["status"] == "CHANGED"
    result, report = invoke(
        tmp_path, [document(old)], [document(new)], "--normalize-line-endings"
    )
    assert result.returncode == 0, result.stderr
    assert report is not None
    row = report["results"][0]
    assert row["action"] == "SKIP_VERIFIED"
    assert row["text"]["byte_equal"] is False
    assert "TEXT_NORMALIZED_EQUAL" in row["text"]["reason_codes"]
    assert report["rules"] == [
        {
            "name": "line-endings-lf",
            "version": "1",
            "contract": "CRLF and lone CR become LF; all other bytes are preserved",
        }
    ]
    assert old.read_bytes() == b"first\r\nsecond\rthird\n"


def test_budget_exhaustion_is_unknown_but_exact_bytes_bypass_diff(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("100k\n")
    new.write_text("300k\n")
    result, report = invoke(
        tmp_path, [document(old)], [document(new)], "--max-diff-work", "0"
    )
    assert result.returncode == 2, result.stderr
    assert report is not None
    row = report["results"][0]
    assert row["action"] == "UNKNOWN"
    assert row["text"]["byte_equal"] is False
    assert row["text"]["comparison_complete"] is False
    assert "DIFF_BUDGET_EXCEEDED" in row["reason_codes"]
    result, report = invoke(
        tmp_path, [document(old)], [document(old)], "--max-diff-work", "0"
    )
    assert result.returncode == 0
    assert report is not None
    assert report["results"][0]["action"] == "SKIP_VERIFIED"


def test_evidence_truncation_does_not_truncate_the_comparison(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("a" * 100 + "\n")
    new.write_text("b" * 100 + "\n")
    result, report = invoke(
        tmp_path,
        [document(old)],
        [document(new)],
        "--include-evidence",
        "--max-evidence-chars",
        "12",
        "--max-char-region",
        "0",
    )
    assert result.returncode == 0, result.stderr
    assert report is not None
    text = report["results"][0]["text"]
    assert text["status"] == "CHANGED"
    assert text["comparison_complete"] is True
    assert text["evidence_truncated"] is True
    assert (
        sum(
            len(r["removed"])
            + len(r["added"])
            + sum(len(c["text"]) for c in r["characters"])
            for r in text["differences"]
        )
        <= 12
    )
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 0
    assert report is not None
    assert report["results"][0]["text"]["differences"] == []


@pytest.mark.parametrize(
    "old_text,new_text",
    [
        ("Rate 0.1%\n", "Rate 0.2%\n"),
        ("Limit 100k TRY\n", "Limit 300k TRY\n"),
        ("允许付款\n", "不允许付款\n"),
        ("amount ≤ 100\n", "amount < 100\n"),
        ("rule one\nrule two\n", "rule one\n"),
        ("|limit|100|\n", "|limit|300|\n"),
        ("hard break  \nnext\n", "hard break\nnext\n"),
        ("    code\n", "  code\n"),
        ("~~inactive~~\n", "inactive\n"),
        (
            "<table><tr><td>100</td></tr></table>",
            "<table><tr><td>300</td></tr></table>",
        ),
        ("[link](https://example.invalid/old)", "[link](https://example.invalid/new)"),
        (":::custom old\n", ":::custom new\n"),
        ("USD 100", "CNY 100"),
        ("CAPITAL", "capital"),
        ("é", "e\u0301"),
        ("paragraph\n\nnext", "paragraph\nnext"),
    ],
)
@pytest.mark.parametrize("normalization", [(), ("--normalize-line-endings",)])
def test_small_financial_and_markdown_changes_are_never_cleaned_away(
    tmp_path, old_text, new_text, normalization
):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(old_text)
    new.write_text(new_text)
    result, report = invoke(
        tmp_path, [document(old)], [document(new)], "--include-evidence", *normalization
    )
    assert result.returncode in (0, 2), result.stderr
    assert report is not None
    row = report["results"][0]
    assert row["text"]["status"] == "CHANGED"
    assert row["action"] not in ("SKIP_VERIFIED", "UPDATE_METADATA_ONLY")
    assert row["text"]["differences"]
    assert old.read_text() == old_text
    assert new.read_text() == new_text


@pytest.mark.parametrize(
    "old_text,new_text,old_line,new_line,removed,added",
    [
        ("a\na\nb\n", "a\nb\na\n", 2, 2, "a\n", ""),
        ("a\n", "x\na\n", 1, 1, "", "x\n"),
        ("a\nx\n", "a\n", 2, 2, "x\n", ""),
        ("a\n", "a", 1, 1, "a\n", "a"),
        ("", "new", 1, 1, "", "new"),
        ("old", "", 1, 1, "old", ""),
    ],
)
def test_myers_boundaries_have_expected_first_change(
    tmp_path, old_text, new_text, old_line, new_line, removed, added
):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(old_text)
    new.write_text(new_text)
    result, report = invoke(
        tmp_path, [document(old)], [document(new)], "--include-evidence"
    )
    assert result.returncode == 0
    assert report is not None
    region = report["results"][0]["text"]["differences"][0]
    assert (region["old_start_line"], region["new_start_line"]) == (old_line, new_line)
    assert (region["removed"], region["added"]) == (removed, added)


@pytest.mark.parametrize(
    "option", ["--max-diff-work", "--max-evidence-chars", "--max-char-region"]
)
def test_negative_limits_are_global_argument_errors(tmp_path, option):
    result, report = invoke(tmp_path, [], [], option, "-1")
    assert result.returncode == 1
    assert report is None
    assert "INVALID_ARGUMENTS" in result.stderr


def test_zero_evidence_and_character_region_limits_preserve_decisions(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("old")
    new.write_text("new")
    result, report = invoke(
        tmp_path,
        [document(old)],
        [document(new)],
        "--include-evidence",
        "--max-evidence-chars",
        "0",
    )
    assert result.returncode == 0
    assert report is not None
    text = report["results"][0]["text"]
    assert text["status"] == "CHANGED"
    assert text["comparison_complete"] is True
    assert text["differences"] == []
    assert text["evidence_truncated"] is True
    result, report = invoke(
        tmp_path,
        [document(old)],
        [document(new)],
        "--include-evidence",
        "--max-char-region",
        "0",
    )
    assert result.returncode == 0
    assert report is not None
    region = report["results"][0]["text"]["differences"][0]
    assert region["characters"] == []
    assert region["characters_omitted"] is True
    assert (region["removed"], region["added"]) == ("old", "new")
