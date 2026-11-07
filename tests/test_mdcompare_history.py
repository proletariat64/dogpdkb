"""Historical-input acceptance through the standalone CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


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
    assert "HISTORICAL_BLOCK_PROVENANCE_INCOMPLETE" in row["reason_codes"]
    assert row["text"]["provenance_verified"] is False
    assert any("Transcribed rule B" in d["removed"] for d in row["text"]["differences"])


def mapped_history(tmp_path, *, new_text=None):
    from PIL import Image

    raw = tmp_path / "old-raw.md"
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    prepared = tmp_path / "prepared.jpg"
    artifact = tmp_path / "ocr.md"
    Image.new("RGB", (2, 2), "white").save(prepared, "JPEG")
    artifact.write_text("OCR transcription")
    image = "![limit](image.png)"
    raw_text = "Rule A\n" + image + "\nKeep rule B\n"
    raw.write_text(raw_text)
    block = (
        '<!-- mdocr:ocr-begin source="prepared.jpg" -->\n\n'
        "OCR transcription\n\n> Original image: [prepared.jpg](prepared.jpg)\n\n"
        "<!-- mdocr:ocr-end -->"
    )
    old.write_text(raw_text.replace(image, block))
    new.write_text(raw_text if new_text is None else new_text)
    provenance = {
        "contract": "mdocr-raw-snapshot-v1",
        "raw_path": "old-raw.md",
        "blocks": [
            {
                "resource_id": "image-1",
                "raw_start_byte": len(b"Rule A\n"),
                "raw_end_byte": len(b"Rule A\n") + len(image.encode()),
                "prepared_image": "prepared.jpg",
                "source_reference": "prepared.jpg",
                "artifact_path": "ocr.md",
            }
        ],
    }
    resources = [{"resource_id": "image-1", "kind": "image", "url": "image.png"}]
    return (
        document(
            old,
            content_kind="historical_enriched",
            provenance=provenance,
            resources=resources,
        ),
        document(new, resources=resources),
    )


def test_complete_block_map_compares_saved_raw_and_preserves_rule_deletion(tmp_path):
    old, new = mapped_history(tmp_path, new_text="Rule A\n![limit](image.png)\n")
    result, report = invoke(tmp_path, [old], [new], "--include-evidence")
    text = report["results"][0]["text"]
    assert result.returncode == 2  # Resource/OCR evidence is intentionally absent.
    assert text["status"] == "CHANGED"
    assert text["provenance_verified"] is True
    assert text["comparison_scope"] == "VERIFIED_RAW_SNAPSHOTS"
    assert "HISTORICAL_PROVENANCE_VERIFIED" in text["reason_codes"]
    assert any(d["removed"] == "Keep rule B\n" for d in text["differences"])
    assert all("OCR transcription" not in d["removed"] for d in text["differences"])


def test_complete_map_without_business_change_compares_raw_equal(tmp_path):
    old, new = mapped_history(tmp_path)
    _, report = invoke(tmp_path, [old], [new], "--include-evidence")
    text = report["results"][0]["text"]
    assert text["status"] == "UNCHANGED"
    assert text["byte_equal"] is True
    assert text["provenance_verified"] is True
    assert text["differences"] == []


def test_unmapped_transcription_is_not_removed_even_with_raw_snapshot(tmp_path):
    old, new = mapped_history(tmp_path)
    path = Path(old["path"])
    path.write_text(path.read_text() + "Unmapped explanation\n")
    result, report = invoke(tmp_path, [old], [new], "--include-evidence")
    text = report["results"][0]["text"]
    assert result.returncode == 2
    assert text["status"] == "UNKNOWN"
    assert "HISTORICAL_RECONSTRUCTION_MISMATCH" in text["reason_codes"]
    assert any("Unmapped explanation" in d["removed"] for d in text["differences"])


def test_missing_raw_snapshot_keeps_observed_difference_and_unknown_reason(tmp_path):
    old, new = mapped_history(tmp_path)
    (tmp_path / "old-raw.md").unlink()
    result, report = invoke(tmp_path, [old], [new], "--include-evidence")
    text = report["results"][0]["text"]
    assert result.returncode == 2
    assert text["status"] == "UNKNOWN"
    assert "INPUT_READ_FAILED" in text["reason_codes"]
    assert text["differences"]


def test_malformed_provenance_path_is_unknown_without_traceback(tmp_path):
    old, new = mapped_history(tmp_path)
    old["provenance"]["blocks"][0]["prepared_image"] = "bad\x00path.jpg"
    result, report = invoke(tmp_path, [old], [new], "--include-evidence")
    assert result.returncode == 2
    assert report["results"][0]["action"] == "UNKNOWN"
    assert "HISTORICAL_BLOCK_PROVENANCE_INVALID" in report["results"][0]["reason_codes"]
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "failure", ["offset", "resource", "prepared", "artifact", "marker"]
)
def test_incomplete_or_mismatched_map_cannot_verify_source(tmp_path, failure):
    old, new = mapped_history(tmp_path)
    block = old["provenance"]["blocks"][0]
    if failure == "offset":
        block["raw_start_byte"] = 0
    elif failure == "resource":
        block["resource_id"] = "other-resource"
    elif failure == "prepared":
        (tmp_path / "prepared.jpg").write_bytes(b"not a JPEG")
    elif failure == "artifact":
        (tmp_path / "ocr.md").write_text("A different transcription")
    else:
        path = Path(old["path"])
        path.write_text(path.read_text().replace("mdocr:ocr-end", "mdocr:other-end"))
    result, report = invoke(tmp_path, [old], [new], "--include-evidence")
    text = report["results"][0]["text"]
    assert result.returncode == 2
    assert report["results"][0]["action"] == "UNKNOWN"
    assert text["provenance_verified"] is False
    assert "HISTORICAL_PROVENANCE_VERIFIED" not in text["reason_codes"]


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
