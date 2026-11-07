"""CLI seam acceptance; fixtures are offline inputs, never production data."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def document(path, document_id="1", knowledge_base="book", **updates):
    value = {
        "knowledge_base": knowledge_base,
        "document_id": document_id,
        "content_kind": "raw",
        "path": str(path),
        "metadata": {
            "name": "Name",
            "logical_path": "folder",
            "source_ref": "source:1",
        },
        "resources": [],
    }
    value.update(updates)
    return value


def invoke(tmp_path, baseline, incoming, *extra):
    old = tmp_path / "baseline.json"
    new = tmp_path / "incoming.json"
    out = tmp_path / "report.json"
    old.write_text(json.dumps({"schema_version": "1.0", "documents": baseline}))
    new.write_text(json.dumps({"schema_version": "1.0", "documents": incoming}))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "compare",
            "--baseline",
            str(old),
            "--incoming",
            str(new),
            "--output",
            str(out),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return result, json.loads(out.read_text()) if out.is_file() else None


def test_same_raw_without_resources_skips_and_preserves_inputs(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_bytes(b"Funds 100k\n")
    original = raw.read_bytes()
    result, report = invoke(tmp_path, [document(raw)], [document(raw)])
    assert result.returncode == 0, result.stderr
    assert report["results"][0]["action"] == "SKIP_VERIFIED"
    assert report["results"][0]["ocr"]["status"] == "NOT_APPLICABLE"
    assert report["summary"]["total"] == 1
    assert raw.read_bytes() == original
    assert (tmp_path / "report.json").stat().st_mode & 0o777 == 0o600


def test_pairs_by_scoped_id_reports_new_absent_and_metadata(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("A")
    b.write_text("B")
    renamed = document(
        a,
        "1",
        metadata={"name": "Renamed", "logical_path": "other", "source_ref": "source:1"},
    )
    old = [document(a, "1"), document(b, "gone"), document(b, "1", "another")]
    new = [document(b, "1", "another"), document(a, "new"), renamed]
    result, report = invoke(tmp_path, old, new)
    assert result.returncode == 0, result.stderr
    rows = {(r["knowledge_base"], r["document_id"]): r for r in report["results"]}
    assert rows[("book", "1")]["action"] == "UPDATE_METADATA_ONLY"
    assert rows[("another", "1")]["action"] == "SKIP_VERIFIED"
    assert rows[("book", "new")]["text"]["status"] == "NEW"
    assert rows[("book", "new")]["action"] == "PROCESS_REQUIRED"
    assert report["baseline_only"] == [
        {"knowledge_base": "book", "document_id": "gone"}
    ]
    assert report["summary"]["total"] == 3


def test_rejects_duplicate_identity_and_never_replaces_prior_report(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text("same")
    out = tmp_path / "report.json"
    out.write_text('{"previous": true}')
    result, report = invoke(tmp_path, [], [document(raw), document(raw)])
    assert result.returncode == 1
    assert "DUPLICATE_DOCUMENT_ID" in result.stderr
    assert report == {"previous": True}


def test_timestamp_only_changes_remain_skippable_and_empty_input_is_complete(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text("same")
    old = document(raw)
    new = document(raw)
    old["metadata"]["sync_timestamp"] = "yesterday"
    new["metadata"]["sync_timestamp"] = "today"
    result, report = invoke(tmp_path, [old], [new])
    assert result.returncode == 0
    assert report["results"][0]["action"] == "SKIP_VERIFIED"
    assert report["results"][0]["metadata"]["sync_timestamp_changed"] is True
    result, report = invoke(tmp_path, [], [])
    assert result.returncode == 0
    assert report["results"] == []
    assert sum(report["summary"]["actions"].values()) == report["summary"]["total"] == 0


def test_unknown_dependency_and_historical_input_cannot_skip(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text("![unverified](https://example.invalid/a.png)")
    result, report = invoke(tmp_path, [document(raw)], [document(raw)])
    assert result.returncode == 2
    assert report["results"][0]["action"] == "UNKNOWN"
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"
    raw.write_text("no image")
    result, report = invoke(
        tmp_path, [document(raw, content_kind="historical_enriched")], [document(raw)]
    )
    assert result.returncode == 2
    assert "HISTORICAL_RAW_UNAVAILABLE" in report["results"][0]["reason_codes"]


def test_item_read_failure_is_reported_without_leaking_path_or_body(tmp_path):
    raw = tmp_path / "secret-token-raw.md"
    result, report = invoke(tmp_path, [], [document(raw)])
    assert result.returncode == 2
    assert report["results"][0]["action"] == "UNKNOWN"
    assert report["errors"][0]["reason_code"] == "INPUT_READ_FAILED"
    assert "secret-token" not in result.stdout + result.stderr


def test_text_changes_without_resources_remain_changes(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("100k")
    new.write_text("300k")
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 0
    assert report["results"][0]["text"]["status"] == "CHANGED"
    assert report["results"][0]["action"] == "UPDATE_TEXT_REUSE_OCR"
    assert report["results"][0]["ocr"]["status"] == "NOT_APPLICABLE"


def test_relative_document_paths_resolve_against_manifest_directory(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text("same")
    result, report = invoke(tmp_path, [document("raw.md")], [document("raw.md")])
    assert result.returncode == 0
    assert report["results"][0]["action"] == "SKIP_VERIFIED"


def test_report_cannot_overwrite_document_or_manifest(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text("must remain")
    result, _ = invoke(tmp_path, [document(raw)], [document(raw)], "--output", str(raw))
    assert result.returncode == 1
    assert raw.read_text() == "must remain"


def test_unwritable_report_target_is_global_failure(tmp_path):
    target = tmp_path / "missing-parent" / "report.json"
    result, report = invoke(tmp_path, [], [], "--output", str(target))
    assert result.returncode == 1
    assert report is None
    assert not target.exists()


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "[]",
        '{"schema_version": "99", "documents": []}',
        '{"schema_version": "1.0", "documents": null}',
        '{"schema_version": "1.0", "documents": [{}]}',
        '{"schema_version": "1.0", "documents": [], "documents": []}',
    ],
)
def test_invalid_or_ambiguous_manifest_is_global_failure(tmp_path, payload):
    baseline = tmp_path / "baseline.json"
    incoming = tmp_path / "incoming.json"
    output = tmp_path / "report.json"
    baseline.write_text('{"schema_version": "1.0", "documents": []}')
    incoming.write_text(payload)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "compare",
            "--baseline",
            str(baseline),
            "--incoming",
            str(incoming),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1
    assert not output.exists()
    assert "Traceback" not in result.stderr
