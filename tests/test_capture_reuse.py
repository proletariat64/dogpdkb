"""Capture download reuse and historical import evidence (ticket 02).

CLI black-box seam: ``dogpdkb discover --snapshot`` with temp corpora and the
existing local listing/details doubles. No real Yuque/OCR/Graphify calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tests.test_knowledge_capture import discover, listing, write_json

URL = "https://example.invalid/book"
OLD = "2026-11-07T00:00:00Z"
NEW = "2026-11-09T00:00:00Z"
NEW_UTC8 = "2026-11-09T08:00:00+08:00"


def version_record(
    value: str = NEW, field: str = "content_updated_at"
) -> dict[str, Any]:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return {
        "field": field,
        "value": value,
        "utc": parsed.astimezone(timezone(timedelta(hours=8))).isoformat(),
        "default_timezone": False,
    }


def observe_baseline(tmp_path: Path, documents: list[dict[str, Any]]) -> Path:
    baseline = tmp_path / "baseline.json"
    result = discover(
        "--snapshot",
        str(baseline),
        "--staging",
        str(tmp_path / "baseline-staging"),
        "--listing",
        str(
            write_json(tmp_path / "baseline-listing.json", listing(documents))
            or tmp_path / "baseline-listing.json"
        ),
        "-s",
        URL,
    )
    assert result.returncode == 0, result.stderr
    return baseline


def write_export_progress(
    path: Path,
    rows: dict[str, dict[str, Any]],
    *,
    source_url: str = URL,
) -> Path:
    write_json(
        path,
        {
            "source": {"path": str(path.parent / "sync.json"), "mtime_ns": 0},
            "configuration": {
                "input_dir": str(path.parent / "corpus"),
                "source_url": source_url,
                "cutoff": "2026-11-01",
                "settings": {},
            },
            "documents": rows,
            "errors": [],
        },
    )
    return path


def exported_row(
    file_name: str,
    *,
    value: str = NEW,
    with_versions: bool = True,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": "exported",
        "file": file_name,
        "title": "One",
        "remote_content_updated_at": value,
        "stamp": "2026-11-09",
    }
    if with_versions:
        row["source_version"] = version_record(value)
        row["listing_version"] = version_record(value)
        row["anomalies"] = []
    return row


def test_imported_legacy_download_with_version_evidence_avoids_redownload(
    tmp_path: Path,
):
    observe_baseline(tmp_path, [{"id": 1, "title": "One", "content_updated_at": OLD}])
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("# One\n\nHistorical raw body.\n", encoding="utf-8")
    progress = write_export_progress(
        history / "export-progress.json",
        {"1": exported_row(raw.name)},
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    details.mkdir()  # Empty: any real download attempt would fail.
    snapshot = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(tmp_path / "staging"),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["status"] == "captured"
    assert capture["anomalies"] == []
    row = capture["exports"]["1"]
    assert row["status"] == "captured"
    assert row["reused"] is True
    assert row["detail_version"]["utc"] == NEW_UTC8
    assert row["listing_version"]["utc"] == NEW_UTC8
    reused_raw = snapshot.parent / row["raw"]["path"]
    assert reused_raw.read_text(encoding="utf-8") == "# One\n\nHistorical raw body.\n"
    assert raw.read_text(encoding="utf-8") == "# One\n\nHistorical raw body.\n"


def test_imported_uat_capture_snapshot_reuses_raw_without_download(tmp_path: Path):
    baseline = observe_baseline(
        tmp_path, [{"id": 1, "title": "One", "content_updated_at": OLD}]
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    uat_details = tmp_path / "uat-details"
    write_json(
        uat_details / "1.json",
        {"id": 1, "body": "# One\n\nUAT raw body.\n", "content_updated_at": NEW},
    )
    uat_snapshot = tmp_path / "uat" / "snapshot.json"
    uat = discover(
        "--snapshot",
        str(uat_snapshot),
        "--baseline-snapshot",
        str(baseline),
        "--staging",
        str(tmp_path / "uat-staging"),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(uat_details),
        "-s",
        URL,
    )
    assert uat.returncode == 0, uat.stderr
    uat_raw_bytes = (
        uat_snapshot.parent
        / json.loads(uat_snapshot.read_text())["exports"]["1"]["raw"]["path"]
    ).read_bytes()

    # A fresh batch with a fresh staging and no detail adapter input at all:
    # the UAT snapshot is the only download evidence.
    details = tmp_path / "details"
    details.mkdir()
    snapshot = tmp_path / "current.json"
    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(baseline),
        "--staging",
        str(tmp_path / "staging"),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "--import-downloads",
        str(uat_snapshot),
        "-s",
        URL,
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["status"] == "captured"
    row = capture["exports"]["1"]
    assert row["reused"] is True
    assert row["raw"]["adapter"] == "imported_capture_snapshot"
    assert row["imported_from"] == str(uat_snapshot)
    assert row["detail_version"]["utc"] == NEW_UTC8
    assert (snapshot.parent / row["raw"]["path"]).read_bytes() == uat_raw_bytes
    assert capture["imports"] == [
        {
            "path": str(uat_snapshot),
            "format": "knowledge-capture-1.0",
            "candidates": 1,
            "rejected": [],
            "reused": [1],
        }
    ]


def test_import_without_equivalent_evidence_redownloads(tmp_path: Path):
    ids = [1, 2, 3, 4, 5]
    observe_baseline(
        tmp_path,
        [{"id": i, "title": f"Doc {i}", "content_updated_at": OLD} for i in ids],
    )
    history = tmp_path / "history"
    raw_dir = history / "raw"
    raw_dir.mkdir(parents=True)
    rows: dict[str, dict[str, Any]] = {}
    for i in ids:
        file_name = f"Doc-2026-11-09-[{i}].md"
        rows[str(i)] = exported_row(file_name)
        (raw_dir / file_name).write_text(f"historical body {i}\n", encoding="utf-8")
    # 1: filename/date/existence only, no version evidence at all.
    rows["1"] = exported_row(rows["1"]["file"], with_versions=False)
    del rows["1"]["remote_content_updated_at"]
    # 2: version evidence points at the older remote version.
    rows["2"]["source_version"] = version_record(OLD)
    rows["2"]["listing_version"] = version_record(OLD)
    rows["2"]["remote_content_updated_at"] = OLD
    # 3: recorded raw file is missing.
    (raw_dir / rows["3"]["file"]).unlink()
    # 4: recorded raw file is not valid UTF-8 text.
    (raw_dir / rows["4"]["file"]).write_bytes(b"\xff\xfe\x00broken")
    # 5: recorded file is OCR-enriched Markdown, never a raw export.
    (raw_dir / rows["5"]["file"]).write_text(
        'body\n\n<!-- mdocr:ocr-begin source="assets/x.jpg" -->\n\nOCR text\n\n'
        "<!-- mdocr:ocr-end -->\n",
        encoding="utf-8",
    )
    progress = write_export_progress(history / "export-progress.json", rows)
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing(
                [{"id": i, "title": f"Doc {i}", "content_updated_at": NEW} for i in ids]
            ),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    for i in ids:
        write_json(
            details / f"{i}.json",
            {"id": i, "body": f"fresh body {i}\n", "content_updated_at": NEW},
        )
    snapshot = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(tmp_path / "staging"),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["status"] == "captured"
    for i in ids:
        row = capture["exports"][str(i)]
        assert row["status"] == "captured", row
        assert row["reused"] is False, row
        assert "imported_from" not in row
        raw = snapshot.parent / row["raw"]["path"]
        assert raw.read_text(encoding="utf-8") == f"fresh body {i}\n"
    assert capture["imports"][0]["reused"] == []
    assert (
        capture["imports"][0]["candidates"] == 1
    )  # only doc 2 loaded, then mismatched
    assert capture["imports"][0]["rejected"] == [
        {"id": 1, "reason": "version_missing"},
        {"id": 3, "reason": "raw_missing"},
        {"id": 4, "reason": "raw_invalid"},
        {"id": 5, "reason": "raw_enriched"},
    ]


def test_corrupt_checkpoint_raw_is_refetched(tmp_path: Path):
    observe_baseline(
        tmp_path,
        [
            {"id": 1, "title": "One", "content_updated_at": OLD},
            {"id": 2, "title": "Two", "content_updated_at": OLD},
        ],
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing(
                [
                    {"id": 1, "title": "One", "content_updated_at": NEW},
                    {"id": 2, "title": "Two", "content_updated_at": NEW},
                ]
            ),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json", {"id": 1, "body": "one fresh\n", "content_updated_at": NEW}
    )
    snapshot = tmp_path / "current.json"
    staging = tmp_path / "staging"
    arguments = [
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        URL,
    ]
    failed = discover(*arguments)
    assert failed.returncode != 0
    first = json.loads(snapshot.read_text())
    assert first["exports"]["1"]["status"] == "captured"
    raw_path = snapshot.parent / first["exports"]["1"]["raw"]["path"]
    raw_path.write_bytes(b"\xff\xfe\x00corrupted")
    write_json(
        details / "2.json", {"id": 2, "body": "two fresh\n", "content_updated_at": NEW}
    )

    resumed = discover(*arguments)

    assert resumed.returncode == 0, resumed.stderr
    second = json.loads(snapshot.read_text())
    assert second["status"] == "captured"
    assert second["exports"]["1"]["reused"] is False
    assert second["exports"]["2"]["reused"] is False
    assert raw_path.read_text(encoding="utf-8") == "one fresh\n"


def test_checkpoint_reuse_matches_version_instants_not_string_forms(tmp_path: Path):
    observe_baseline(
        tmp_path,
        [
            {"id": 1, "title": "One", "content_updated_at": OLD},
            {"id": 2, "title": "Two", "content_updated_at": OLD},
        ],
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing(
                [
                    {"id": 1, "title": "One", "content_updated_at": NEW},
                    {"id": 2, "title": "Two", "content_updated_at": NEW},
                ]
            ),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json", {"id": 1, "body": "one raw\n", "content_updated_at": NEW}
    )
    snapshot = tmp_path / "current.json"
    staging = tmp_path / "staging"
    arguments = [
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        URL,
    ]
    failed = discover(*arguments)
    assert failed.returncode != 0
    checkpoint_path = staging / "capture-checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    # Same instant as the current listing version, different string form.
    checkpoint["documents"]["1"]["listing_version"] = {
        "field": "content_updated_at",
        "value": NEW_UTC8,
        "utc": NEW_UTC8,
        "default_timezone": False,
    }
    write_json(checkpoint_path, checkpoint)
    raw_path = staging / "raw" / checkpoint["documents"]["1"]["raw_file"]
    raw_bytes = raw_path.read_bytes()
    write_json(
        details / "2.json", {"id": 2, "body": "two raw\n", "content_updated_at": NEW}
    )
    (details / "1.json").unlink()  # A re-download of doc 1 would now fail.

    resumed = discover(*arguments)

    assert resumed.returncode == 0, resumed.stderr
    second = json.loads(snapshot.read_text())
    assert second["status"] == "captured"
    assert second["exports"]["1"]["reused"] is True
    assert second["exports"]["2"]["reused"] is False
    assert raw_path.read_bytes() == raw_bytes


def test_imported_pre_baseline_legacy_record_uses_detail_timestamp_evidence(
    tmp_path: Path,
):
    observe_baseline(tmp_path, [{"id": 1, "title": "One", "content_updated_at": OLD}])
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("# One\n\nOld-format raw body.\n", encoding="utf-8")
    # Pre-state-baseline export-progress rows carried no structured versions.
    row = exported_row(raw.name, with_versions=False)
    progress = write_export_progress(history / "export-progress.json", {"1": row})
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    details.mkdir()
    snapshot = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(tmp_path / "staging"),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    row = capture["exports"]["1"]
    assert row["reused"] is True
    assert row["raw"]["adapter"] == "imported_export_progress"
    assert row["detail_version"] == {
        "field": "content_updated_at",
        "value": NEW,
        "utc": NEW_UTC8,
        "default_timezone": False,
    }
    assert (snapshot.parent / row["raw"]["path"]).read_text(
        encoding="utf-8"
    ) == "# One\n\nOld-format raw body.\n"
    checkpoint = json.loads(
        (tmp_path / "staging" / "capture-checkpoint.json").read_text()
    )
    assert checkpoint["documents"]["1"]["imported_from"] == str(progress)


def test_import_from_another_book_is_rejected_without_snapshot(tmp_path: Path):
    observe_baseline(tmp_path, [{"id": 1, "title": "One", "content_updated_at": OLD}])
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("foreign body\n", encoding="utf-8")
    progress = write_export_progress(
        history / "export-progress.json",
        {"1": exported_row(raw.name)},
        source_url="https://example.invalid/other-book",
    )
    foreign_snapshot = tmp_path / "foreign-snapshot.json"
    write_json(
        foreign_snapshot,
        {
            "schema_version": "knowledge-capture-1.0",
            "book": {
                "book_id": 999,
                "slug": "other",
                "name": "Other",
                "items_count": 1,
            },
            "exports": {
                "1": {
                    "status": "captured",
                    "raw": {
                        "path": str(raw),
                        "stage": "raw_export",
                        "captured_at": NEW_UTC8,
                    },
                    "listing_version": version_record(),
                    "detail_version": version_record(),
                }
            },
        },
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json", {"id": 1, "body": "fresh\n", "content_updated_at": NEW}
    )
    for record in (progress, foreign_snapshot):
        snapshot = tmp_path / f"current-{record.name}.json"
        result = discover(
            "--snapshot",
            str(snapshot),
            "--baseline-snapshot",
            str(tmp_path / "baseline.json"),
            "--staging",
            str(tmp_path / f"staging-{record.name}"),
            "--listing",
            str(current_listing),
            "--details-dir",
            str(details),
            "--import-downloads",
            str(record),
            "-s",
            URL,
        )
        assert result.returncode == 1
        assert "different book" in result.stderr
        assert not snapshot.exists()


def test_import_never_backfills_unselected_historical_documents(tmp_path: Path):
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("historical body\n", encoding="utf-8")
    progress = write_export_progress(
        history / "export-progress.json", {"1": exported_row(raw.name)}
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    snapshot = tmp_path / "current.json"
    staging = tmp_path / "staging"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["status"] == "initialized"
    assert capture["selected"] == []
    assert capture["exports"] == {}
    assert capture["imports"][0]["reused"] == []
    assert not list(staging.glob("raw/*.md"))


def test_import_reuse_keeps_observed_download_and_published_versions_distinct(
    tmp_path: Path,
):
    from tests.test_sync_progress_cli import initialized

    corpus, config = initialized(tmp_path)
    state_file = corpus / ".sync-state.json"
    before = json.loads(state_file.read_text())
    graph_before = before["books"]["fund"]["documents"]["1"].get("graph")
    enriched_file = corpus / "One-2026-11-10-[1].md"
    enriched_bytes = enriched_file.read_bytes()
    current = "2026-11-11T00:00:00Z"
    current_utc8 = "2026-11-11T08:00:00+08:00"
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-11-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("# One\n\nImported raw body.\n", encoding="utf-8")
    progress = write_export_progress(
        history / "export-progress.json",
        {"1": exported_row(raw.name, value=current)},
        source_url="https://example.invalid/team/uat",
    )
    listing_path = (
        write_json(
            tmp_path / "listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": current}]),
        )
        or tmp_path / "listing.json"
    )
    snapshot = tmp_path / "current.json"

    result = discover(
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(snapshot),
        "--staging",
        str(tmp_path / "capture"),
        "--listing",
        str(listing_path),
        "--import-downloads",
        str(progress),
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["exports"]["1"]["reused"] is True
    state = json.loads(state_file.read_text())
    doc = state["books"]["fund"]["documents"]["1"]
    assert doc["observed"]["version"]["utc"] == current_utc8
    assert doc["steps"]["download"]["status"] == "completed"
    assert doc["steps"]["download"]["version"]["utc"] == current_utc8
    assert doc["corpus"]["status"] == "captured"
    assert doc["corpus"]["version"]["utc"] == current_utc8
    # A successful download never claims a graph publication, and the
    # historical enriched baseline stays historical_enriched.
    assert doc.get("graph") == graph_before
    assert doc["baseline"]["content_kind"] == "historical_enriched"
    assert enriched_file.read_bytes() == enriched_bytes


def test_resume_after_import_reuses_checkpoint_without_import_flag(tmp_path: Path):
    observe_baseline(
        tmp_path,
        [
            {"id": 1, "title": "One", "content_updated_at": OLD},
            {"id": 2, "title": "Two", "content_updated_at": OLD},
        ],
    )
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("imported one\n", encoding="utf-8")
    progress = write_export_progress(
        history / "export-progress.json", {"1": exported_row(raw.name)}
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing(
                [
                    {"id": 1, "title": "One", "content_updated_at": NEW},
                    {"id": 2, "title": "Two", "content_updated_at": NEW},
                ]
            ),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    details.mkdir()  # doc 2 export fails; doc 1 must come from the import.
    snapshot = tmp_path / "current.json"
    staging = tmp_path / "staging"
    arguments = [
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        URL,
    ]
    failed = discover(*arguments, "--import-downloads", str(progress))
    assert failed.returncode != 0
    first = json.loads(snapshot.read_text())
    assert first["exports"]["1"]["reused"] is True
    write_json(
        details / "2.json", {"id": 2, "body": "two raw\n", "content_updated_at": NEW}
    )

    resumed = discover(*arguments)  # No import flag: checkpoint carries the evidence.

    assert resumed.returncode == 0, resumed.stderr
    second = json.loads(snapshot.read_text())
    assert second["status"] == "captured"
    assert second["exports"]["1"]["reused"] is True
    assert second["exports"]["1"]["raw"]["adapter"] == "imported_export_progress"
    assert second["imports"] == []
    assert (snapshot.parent / second["exports"]["1"]["raw"]["path"]).read_text(
        encoding="utf-8"
    ) == "imported one\n"


def test_overlapping_labels_reuse_imported_raw_once(tmp_path: Path):
    observe_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Old name",
                "content_updated_at": OLD,
                "toc_path": ["Old"],
            }
        ],
    )
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("imported body\n", encoding="utf-8")
    progress = write_export_progress(
        history / "export-progress.json", {"1": exported_row(raw.name)}
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing(
                [
                    {
                        "id": 1,
                        "title": "New name",
                        "content_updated_at": NEW,
                        "toc_path": ["New"],
                    }
                ]
            ),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    details.mkdir()
    snapshot = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(tmp_path / "staging"),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["changes"]["updated"] == [1]
    assert capture["changes"]["renamed"] == [1]
    assert capture["changes"]["moved"] == [1]
    assert capture["selected"] == [1]
    assert capture["counts"]["captured"] == 1
    row = capture["exports"]["1"]
    assert row["reused"] is True
    assert row["labels"] == ["moved", "renamed", "updated"]
    assert capture["imports"][0]["reused"] == [1]


def test_corrupt_canonical_download_raw_is_refetched(tmp_path: Path):
    from tests.test_sync_progress_cli import initialized

    corpus, config = initialized(tmp_path)
    current = "2026-11-11T00:00:00Z"
    documents = [{"id": 1, "title": "One", "content_updated_at": current}]
    listing_path = write_json(tmp_path / "listing.json", listing(documents)) or (
        tmp_path / "listing.json"
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json",
        {"id": 1, "body": "fresh canonical body\n", "content_updated_at": current},
    )
    snapshot = tmp_path / "current.json"
    staging = tmp_path / "capture"
    arguments = [
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(snapshot),
        "--staging",
        str(staging),
        "--listing",
        str(listing_path),
        "--details-dir",
        str(details),
    ]
    first = discover(*arguments)
    assert first.returncode == 0, first.stderr
    state = json.loads((corpus / ".sync-state.json").read_text())
    saved_raw = state["books"]["fund"]["documents"]["1"]["steps"]["download"]["raw"]
    raw_path = corpus / saved_raw["path"]
    assert raw_path.is_file()
    raw_path.write_bytes(b"\xff\xfe\x00corrupted")
    (details / "1.json").unlink()
    write_json(
        details / "1.json",
        {"id": 1, "body": "fresh canonical body\n", "content_updated_at": current},
    )
    snapshot.unlink()  # A fresh observation of the same unchanged version.

    second = discover(*arguments)

    assert second.returncode == 0, second.stderr
    capture = json.loads(snapshot.read_text())
    row = capture["exports"]["1"]
    assert row["reused"] is False
    assert (snapshot.parent / row["raw"]["path"]).read_text(
        encoding="utf-8"
    ) == "fresh canonical body\n"


def test_import_conflict_never_overwrites_valid_raw(tmp_path: Path):
    observe_baseline(tmp_path, [{"id": 1, "title": "One", "content_updated_at": OLD}])
    history = tmp_path / "history"
    raw = history / "raw" / "1-20261109T080000+0800.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("imported body\n", encoding="utf-8")
    progress = write_export_progress(
        history / "export-progress.json", {"1": exported_row(raw.name)}
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json", {"id": 1, "body": "fresh body\n", "content_updated_at": NEW}
    )
    staging = tmp_path / "staging"
    # A valid raw with different bytes already occupies the target name.
    conflicting = staging / "raw" / "1-20261109T080000+0800.md"
    conflicting.parent.mkdir(parents=True)
    conflicting.write_text("different valid body\n", encoding="utf-8")
    snapshot = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert result.returncode != 0
    capture = json.loads(snapshot.read_text())
    assert capture["status"] == "incomplete"
    assert capture["exports"]["1"]["status"] == "failed"
    assert capture["anomalies"] == [{"id": 1, "code": "export_failed"}]
    assert conflicting.read_text(encoding="utf-8") == "different valid body\n"


def test_import_oserror_archives_one_document_and_keeps_the_other(tmp_path: Path):
    # [17] ticket-02: _save_raw can raise OSError (here the destination raw name
    # is a non-empty directory, so os.replace fails). The import-reuse call sits
    # OUTSIDE the per-document export try/except, so an OSError used to escape
    # and discard the whole observation (no snapshot). It must degrade to
    # export_failed for that one document while the other document's import
    # still succeeds and the snapshot is written (spec:84, issue-02 #5).
    observe_baseline(
        tmp_path,
        [
            {"id": 1, "title": "One", "content_updated_at": OLD},
            {"id": 2, "title": "Two", "content_updated_at": OLD},
        ],
    )
    history = tmp_path / "history"
    raw_dir = history / "raw"
    raw_dir.mkdir(parents=True)
    rows: dict[str, dict[str, Any]] = {}
    for i in (1, 2):
        name = f"{i}-20261109T080000+0800.md"
        (raw_dir / name).write_text(f"imported body {i}\n", encoding="utf-8")
        rows[str(i)] = exported_row(name)
    progress = write_export_progress(history / "export-progress.json", rows)
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing(
                [
                    {"id": 1, "title": "One", "content_updated_at": NEW},
                    {"id": 2, "title": "Two", "content_updated_at": NEW},
                ]
            ),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    details.mkdir()  # Empty: doc 1's fallback fresh export must fail too.
    staging = tmp_path / "staging"
    # doc 1's destination raw name is a NON-EMPTY DIRECTORY -> os.replace OSError.
    blocked = staging / "raw" / "1-20261109T080000+0800.md"
    blocked.mkdir(parents=True)
    (blocked / "occupant.txt").write_text("not a raw\n", encoding="utf-8")
    snapshot = tmp_path / "current.json"

    discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert snapshot.is_file(), "OSError discarded the whole observation"
    capture = json.loads(snapshot.read_text())
    assert capture["status"] == "incomplete"
    assert capture["exports"]["1"]["status"] == "failed"
    assert {"id": 1, "code": "export_failed"} in capture["anomalies"]
    row2 = capture["exports"]["2"]
    assert row2["status"] == "captured"
    assert row2["reused"] is True
    assert (snapshot.parent / row2["raw"]["path"]).read_text(
        encoding="utf-8"
    ) == "imported body 2\n"


def test_duplicate_import_sources_and_unsupported_format(tmp_path: Path):
    observe_baseline(tmp_path, [{"id": 1, "title": "One", "content_updated_at": OLD}])
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("imported body\n", encoding="utf-8")
    first = write_export_progress(
        history / "export-progress.json", {"1": exported_row(raw.name)}
    )
    second = write_export_progress(
        history / "export-progress-2.json", {"1": exported_row(raw.name)}
    )
    unsupported = tmp_path / "unsupported.json"
    write_json(unsupported, {"hello": "world"})
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    details.mkdir()
    snapshot = tmp_path / "current.json"
    arguments = [
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(tmp_path / "staging"),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        URL,
    ]

    duplicated = discover(*arguments, "--import-downloads", str(first), str(second))
    assert duplicated.returncode == 0, duplicated.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["imports"][0]["reused"] == [1]
    assert capture["imports"][1]["candidates"] == 0
    assert capture["imports"][1]["rejected"] == [
        {"id": 1, "reason": "duplicate_document"}
    ]

    snapshot.unlink()
    bad = discover(*arguments, "--import-downloads", str(unsupported))
    assert bad.returncode == 1
    assert "unsupported format" in bad.stderr


def test_malformed_version_record_is_rejected_not_crashed(tmp_path: Path):
    observe_baseline(tmp_path, [{"id": 1, "title": "One", "content_updated_at": OLD}])
    history = tmp_path / "history"
    raw = history / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("imported body\n", encoding="utf-8")
    row = exported_row(raw.name)
    row["source_version"] = {"field": ["content_updated_at"], "value": NEW, "utc": NEW}
    progress = write_export_progress(history / "export-progress.json", {"1": row})
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json", {"id": 1, "body": "fresh\n", "content_updated_at": NEW}
    )
    snapshot = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(tmp_path / "staging"),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["exports"]["1"]["reused"] is False
    assert capture["imports"][0]["rejected"] == [{"id": 1, "reason": "version_missing"}]


def test_import_cannot_alias_the_capture_raw_output(tmp_path: Path):
    observe_baseline(tmp_path, [{"id": 1, "title": "One", "content_updated_at": OLD}])
    staging = tmp_path / "staging"
    raw = staging / "raw" / "One-2026-11-09-[1].md"
    raw.parent.mkdir(parents=True)
    raw.write_text("self import body\n", encoding="utf-8")
    progress = write_export_progress(
        staging / "export-progress.json", {"1": exported_row(raw.name)}
    )
    current_listing = (
        write_json(
            tmp_path / "current-listing.json",
            listing([{"id": 1, "title": "One", "content_updated_at": NEW}]),
        )
        or tmp_path / "current-listing.json"
    )
    snapshot = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(tmp_path / "baseline.json"),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--import-downloads",
        str(progress),
        "-s",
        URL,
    )

    assert result.returncode == 1
    assert "REPORT_INPUT_COLLISION" in result.stderr
    assert not snapshot.exists()
