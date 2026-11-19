"""Historical-input acceptance through the standalone CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path


def document(path, **updates):
    result = {
        "knowledge_base": "book",
        "document_id": "1",
        "content_kind": "raw",
        "path": str(path),
        "metadata": {
            "name": "Name",
            "logical_path": "folder",
            "source_ref": "source:1",
        },
        "resources": [],
    }
    result.update(updates)
    return result


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
    assert output.is_file(), result.stderr
    return result, json.loads(output.read_text())


def test_unmarked_historical_transcription_is_unknown_with_asymmetric_diff(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("Allow 100k\nUnmarked image transcription\n")
    new.write_text("Allow 300k\n")
    result, report = invoke(
        tmp_path,
        [document(old, content_kind="historical_enriched")],
        [document(new)],
        "--include-evidence",
    )
    row = report["results"][0]
    assert result.returncode == 2
    assert row["action"] == "UNKNOWN"
    assert row["text"]["status"] == "UNKNOWN"
    assert row["text"]["comparison_scope"] == "HISTORICAL_ENRICHED_TO_RAW"
    assert "HISTORICAL_RAW_UNAVAILABLE" in row["reason_codes"]
    assert "HISTORICAL_UNMARKED_CONTENT" in row["reason_codes"]
    assert row["text"]["observed_status"] == "CHANGED"
    evidence = row["text"]["differences"]
    assert any("100k" in d["removed"] and "300k" in d["added"] for d in evidence)
    assert "source deletion" not in result.stdout + result.stderr


def test_marked_block_without_source_map_stays_unknown(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(
        'Rule A\n<!-- mdocr:ocr-begin source="prepared.jpg" -->\n'
        "Transcribed rule B\n<!-- mdocr:ocr-end -->\n"
    )
    new.write_text("Rule A\n")
    result, report = invoke(
        tmp_path,
        [document(old, content_kind="historical_enriched")],
        [document(new)],
        "--include-evidence",
    )
    row = report["results"][0]
    assert result.returncode == 2
    assert "HISTORICAL_RAW_UNAVAILABLE" in row["reason_codes"]
    assert row["text"]["provenance_verified"] is False
    assert any("Transcribed rule B" in d["removed"] for d in row["text"]["differences"])


def test_equal_enriched_bytes_and_new_historical_documents_are_not_raw(tmp_path):
    path = tmp_path / "historical.md"
    path.write_text("Unmarked transcription\n")
    historical = document(path, content_kind="historical_enriched")
    for baseline in ([], [historical]):
        result, report = invoke(tmp_path, baseline, [historical])
        row = report["results"][0]
        assert result.returncode == 2
        assert row["action"] == "UNKNOWN"
        assert row["text"]["provenance_verified"] is False
        assert row["text"]["differences"] == []


def test_historical_explanation_respects_limits_and_remains_deterministic(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("100k\nUnmarked transcription\n")
    new.write_text("300k\n")
    baseline = [document(old, content_kind="historical_enriched")]
    incoming = [document(new)]
    _, first = invoke(
        tmp_path, baseline, incoming, "--include-evidence", "--max-evidence-chars", "8"
    )
    _, second = invoke(
        tmp_path, baseline, incoming, "--include-evidence", "--max-evidence-chars", "8"
    )
    assert first == second
    text = first["results"][0]["text"]
    assert text["status"] == "UNKNOWN"
    assert text["evidence_truncated"] is True
    assert (
        sum(
            len(d["removed"])
            + len(d["added"])
            + sum(len(c["text"]) for c in d["characters"])
            for d in text["differences"]
        )
        <= 8
    )
    _, report = invoke(
        tmp_path, baseline, incoming, "--include-evidence", "--max-diff-work", "0"
    )
    text = report["results"][0]["text"]
    assert text["comparison_complete"] is False
    assert "DIFF_BUDGET_EXCEEDED" in text["reason_codes"]
