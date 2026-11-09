from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def listing(
    documents: list[dict[str, Any]], *, complete: bool = True
) -> dict[str, Any]:
    return {
        "book": {
            "book_id": 123,
            "slug": "uat",
            "name": "UAT",
            "items_count": len(documents),
        },
        "listing": {
            "complete": complete,
            "document_count": len(documents),
            "pages": [{"offset": 0, "limit": 50, "item_count": len(documents)}],
        },
        "documents": documents,
    }


def discover(*arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", "discover", *arguments],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def write_remote_adapter(tmp_path: Path, documents: list[dict[str, Any]]) -> Path:
    fixture = tmp_path / "remote.json"
    write_json(
        fixture,
        {
            "documents": documents,
            "details": {
                str(document["id"]): {
                    "id": document["id"],
                    "body": f"body {document['id']}",
                    "content_updated_at": document["content_updated_at"],
                }
                for document in documents
            },
        },
    )
    proxy = tmp_path / "fake-proxy.py"
    proxy.write_text(
        f"#!{sys.executable}\n"
        + """import json
import sys
from pathlib import Path

fixture = json.loads(Path(__file__).with_name("remote.json").read_text())
for line in sys.stdin:
    request = json.loads(line)
    request_id = request.get("id")
    if request_id is None:
        continue
    if request.get("method") == "initialize":
        result = {}
    else:
        name = request["params"]["name"]
        args = request["params"]["arguments"]
        if name == "skylark_resolve_url":
            data = {"type": "book", "id": 123, "slug": "uat", "name": "UAT"}
        elif name == "skylark_book_detail":
            data = {"name": "UAT", "items_count": len(fixture["documents"])}
        elif name == "skylark_doc_list":
            offset = args["offset"]
            data = fixture.get("page_overrides", {}).get(
                str(offset), fixture["documents"][offset:offset + args["limit"]]
            )
        elif name == "skylark_book_toc":
            data = [
                {"type": "DOC", "doc_id": doc["id"], "title": doc["title"], "children": []}
                for doc in fixture["documents"]
            ]
        elif name == "skylark_doc_detail":
            data = fixture["details"][str(args["doc_id"])]
        else:
            raise AssertionError(name)
        result = {"content": [{"text": json.dumps({"ok": True, "data": data})}]}
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[skylark]\nmcp_url="http://127.0.0.1:9/mcp"\nproxy_bin="{proxy}"\n'
        "timeout_seconds=2\nmax_retries=0\nconcurrency=1\n",
        encoding="utf-8",
    )
    return config


def test_initial_snapshot_observes_complete_listing_without_exporting(
    tmp_path: Path,
):
    listing_path = tmp_path / "listing.json"
    snapshot_path = tmp_path / "private" / "snapshot.json"
    staging = tmp_path / "private" / "staging"
    write_json(
        listing_path,
        listing(
            [
                {
                    "id": 1,
                    "title": "One",
                    "slug": "one",
                    "content_updated_at": "2026-11-07T01:00:00.000Z",
                    "toc_path": ["Parent"],
                },
                {
                    "id": 2,
                    "title": "Two",
                    "slug": "two",
                    "updated_at": "2026-11-07T02:00:00+00:00",
                },
            ]
        ),
    )

    result = discover(
        "--snapshot",
        str(snapshot_path),
        "--staging",
        str(staging),
        "--listing",
        str(listing_path),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode == 0, result.stderr
    snapshot = json.loads(snapshot_path.read_text())
    assert snapshot["schema_version"] == "knowledge-capture-1.0"
    assert snapshot["status"] == "initialized"
    assert snapshot["book"]["book_id"] == 123
    assert snapshot["observation"]["timezone"] == "UTC"
    assert snapshot["observation"]["started_at"].endswith("Z")
    assert snapshot["observation"]["ended_at"].endswith("Z")
    assert snapshot["listing"]["complete"] is True
    assert snapshot["listing"]["document_ids"] == [1, 2]
    assert snapshot["listing"]["pages"] == [{"offset": 0, "limit": 50, "item_count": 2}]
    assert snapshot["changes"] == {
        "new": [],
        "updated": [],
        "renamed": [],
        "moved": [],
        "absent": [],
    }
    assert snapshot["selected"] == []
    assert snapshot["exports"] == {}
    assert snapshot["documents"]["1"]["version"] == {
        "field": "content_updated_at",
        "value": "2026-11-07T01:00:00.000Z",
        "utc": "2026-11-07T01:00:00Z",
    }
    assert snapshot["documents"]["2"]["version"]["field"] == "updated_at"
    assert not list(staging.glob("raw/*.md"))


def test_incremental_snapshot_classifies_and_exports_each_selected_id_once(
    tmp_path: Path,
):
    baseline_path = tmp_path / "baseline.json"
    snapshot_path = tmp_path / "next" / "snapshot.json"
    staging = tmp_path / "next" / "staging"
    listing_path = tmp_path / "next" / "listing.json"
    details = tmp_path / "next" / "details"
    old = "2026-11-07T01:00:00+00:00"
    new = "2026-11-09T03:00:00+08:00"
    discover(
        "--snapshot",
        str(baseline_path),
        "--staging",
        str(tmp_path / "baseline-staging"),
        "--listing",
        str(
            write_json(
                tmp_path / "baseline-listing.json",
                listing(
                    [
                        {"id": 1, "title": "Same", "content_updated_at": old},
                        {
                            "id": 2,
                            "title": "Old",
                            "content_updated_at": old,
                            "toc_path": ["Old parent"],
                        },
                        {"id": 3, "title": "Absent", "content_updated_at": old},
                    ]
                ),
            )
            or tmp_path / "baseline-listing.json"
        ),
        "-s",
        "https://example.invalid/book",
    )
    current_documents = [
        {"id": 1, "title": "Same", "content_updated_at": "2026-11-07T01:00:00.000Z"},
        {
            "id": 2,
            "title": "New title",
            "content_updated_at": new,
            "toc_path": ["New parent"],
        },
        {"id": 4, "title": "New", "updated_at": new},
    ]
    write_json(listing_path, listing(current_documents))
    for document in current_documents:
        if document["id"] in {2, 4}:
            field = (
                "content_updated_at"
                if "content_updated_at" in document
                else "updated_at"
            )
            write_json(
                details / f"{document['id']}.json",
                {
                    "id": document["id"],
                    "body": f"body {document['id']}",
                    field: document[field],
                },
            )

    result = discover(
        "--snapshot",
        str(snapshot_path),
        "--baseline-snapshot",
        str(baseline_path),
        "--staging",
        str(staging),
        "--listing",
        str(listing_path),
        "--details-dir",
        str(details),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode == 0, result.stderr
    snapshot = json.loads(snapshot_path.read_text())
    assert snapshot["status"] == "captured"
    assert snapshot["changes"] == {
        "new": [4],
        "updated": [2],
        "renamed": [2],
        "moved": [2],
        "absent": [3],
    }
    assert snapshot["selected"] == [2, 4]
    assert snapshot["counts"] == {
        "documents": 3,
        "new": 1,
        "updated": 1,
        "renamed": 1,
        "moved": 1,
        "absent": 1,
        "selected": 2,
        "captured": 2,
        "failed": 0,
        "pending": 1,
        "anomalies": 0,
    }
    assert sorted(snapshot["exports"]) == ["2", "4"]
    assert len(list(staging.glob("raw/*.md"))) == 2
    assert snapshot["exports"]["2"]["status"] == "captured"
    assert snapshot["exports"]["2"]["detail_version"]["utc"] == "2026-11-08T19:00:00Z"
    assert snapshot["exports"]["2"]["original"]["status"] == "missing"
    assert snapshot["exports"]["4"]["original"]["status"] == "not_applicable"
    assert snapshot["documents"]["1"]["id"] == 1


def test_timestamp_regression_fails_without_overwriting_baseline(tmp_path: Path):
    baseline_path = tmp_path / "baseline.json"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing(
            [{"id": 1, "title": "One", "content_updated_at": "2026-11-09T00:00:00Z"}]
        ),
    )
    assert (
        discover(
            "--snapshot",
            str(baseline_path),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    before = baseline_path.read_bytes()
    current_listing = tmp_path / "current-listing.json"
    write_json(
        current_listing,
        listing(
            [{"id": 1, "title": "One", "content_updated_at": "2026-11-08T00:00:00Z"}]
        ),
    )
    output = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline_path),
        "--listing",
        str(current_listing),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode != 0
    assert "timestamp regression" in result.stderr
    assert not output.exists()
    assert baseline_path.read_bytes() == before


def test_detail_newer_than_listing_is_visible_and_not_a_success(tmp_path: Path):
    baseline_path = tmp_path / "baseline.json"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing(
            [{"id": 1, "title": "One", "content_updated_at": "2026-11-07T00:00:00Z"}]
        ),
    )
    assert (
        discover(
            "--snapshot",
            str(baseline_path),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    current_listing = tmp_path / "current-listing.json"
    write_json(
        current_listing,
        listing(
            [{"id": 1, "title": "One", "content_updated_at": "2026-11-09T00:00:00Z"}]
        ),
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json",
        {
            "id": 1,
            "body": "new body",
            "content_updated_at": "2026-11-09T00:01:00Z",
        },
    )
    output = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline_path),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode != 0
    snapshot = json.loads(output.read_text())
    assert snapshot["status"] == "incomplete"
    assert snapshot["anomalies"] == [{"id": 1, "code": "detail_newer_than_listing"}]
    assert snapshot["exports"]["1"]["raw"]["stage"] == "raw_export"
    assert snapshot["pending"] == [
        {"id": 1, "reasons": ["baseline_raw_missing", "detail_newer_than_listing"]}
    ]


def test_explicit_pending_state_is_retained_when_remote_is_unchanged(tmp_path: Path):
    stamp = "2026-11-09T00:00:00Z"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": stamp}]),
    )
    baseline = tmp_path / "baseline.json"
    assert (
        discover(
            "--snapshot",
            str(baseline),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    current_listing = tmp_path / "current-listing.json"
    write_json(
        current_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": stamp}]),
    )
    pending_state = tmp_path / "pending.json"
    write_json(
        pending_state,
        {
            "schema_version": "knowledge-capture-1.0",
            "book": {"book_id": 123},
            "pending": [{"id": 1, "reasons": ["review_pending"]}],
        },
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json",
        {"id": 1, "body": "unchanged body", "content_updated_at": stamp},
    )
    output = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline),
        "--pending-state",
        str(pending_state),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode == 0, result.stderr
    snapshot = json.loads(output.read_text())
    assert snapshot["selected"] == [1]
    assert snapshot["exports"]["1"]["labels"] == []
    assert snapshot["pending"] == [
        {"id": 1, "reasons": ["baseline_raw_missing", "review_pending"]}
    ]


def test_export_failure_resumes_from_private_checkpoint(tmp_path: Path):
    old = "2026-11-07T00:00:00Z"
    new = "2026-11-09T00:00:00Z"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing(
            [
                {"id": 1, "title": "One", "content_updated_at": old},
                {"id": 2, "title": "Two", "content_updated_at": old},
            ]
        ),
    )
    baseline = tmp_path / "baseline.json"
    assert (
        discover(
            "--snapshot",
            str(baseline),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    current_listing = tmp_path / "current-listing.json"
    write_json(
        current_listing,
        listing(
            [
                {"id": 1, "title": "One", "content_updated_at": new},
                {"id": 2, "title": "Two", "content_updated_at": new},
            ]
        ),
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json",
        {"id": 1, "body": "one", "content_updated_at": new},
    )
    output = tmp_path / "current.json"
    staging = tmp_path / "staging"
    arguments = [
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        "https://example.invalid/book",
    ]

    failed = discover(*arguments)

    assert failed.returncode != 0
    first = json.loads(output.read_text())
    assert first["status"] == "incomplete"
    assert first["exports"]["1"]["status"] == "captured"
    assert first["exports"]["2"]["status"] == "failed"
    checkpoint_path = staging / "capture-checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    assert sorted(checkpoint["documents"]) == ["1"]
    first_raw = staging / "raw" / Path(first["exports"]["1"]["raw"]["path"]).name
    first_bytes = first_raw.read_bytes()
    write_json(
        details / "2.json",
        {"id": 2, "body": "two", "content_updated_at": new},
    )

    resumed = discover(*arguments)

    assert resumed.returncode == 0, resumed.stderr
    second = json.loads(output.read_text())
    assert second["status"] == "captured"
    assert second["exports"]["1"]["reused"] is True
    assert second["exports"]["2"]["reused"] is False
    assert first_raw.read_bytes() == first_bytes
    assert checkpoint_path.parent.stat().st_mode & 0o777 == 0o700
    assert checkpoint_path.stat().st_mode & 0o777 == 0o600


def _assert_remote_retry_refetches_mismatched_detail_and_reuses_matching_checkpoint(
    tmp_path: Path, stale_timestamp: str, anomaly: str
):
    old = "2026-11-07T00:00:00Z"
    new = "2026-11-09T00:00:00Z"
    documents = [
        {"id": doc_id, "title": f"Document {doc_id}", "content_updated_at": old}
        for doc_id in (1, 2)
    ]
    config = write_remote_adapter(tmp_path, documents)
    baseline = tmp_path / "baseline" / "snapshot.json"
    initial = discover(
        "--config",
        str(config),
        "--snapshot",
        str(baseline),
        "-s",
        "https://example.invalid/book",
    )
    assert initial.returncode == 0, initial.stderr
    baseline_bytes = baseline.read_bytes()

    for document in documents:
        document["content_updated_at"] = new
    write_remote_adapter(tmp_path, documents)
    fixture_path = tmp_path / "remote.json"
    fixture = json.loads(fixture_path.read_text())
    fixture["details"]["1"].update(
        {"body": "stale body", "content_updated_at": stale_timestamp}
    )
    fixture["details"]["2"].update(
        {"body": "matching body", "content_updated_at": "2026-11-09T08:00:00+08:00"}
    )
    write_json(fixture_path, fixture)
    output = tmp_path / "next" / "snapshot.json"
    staging = tmp_path / "next" / "staging"
    arguments = [
        "--config",
        str(config),
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline),
        "--staging",
        str(staging),
        "-s",
        "https://example.invalid/book",
    ]

    failed = discover(*arguments)

    assert failed.returncode != 0
    first = json.loads(output.read_text())
    assert first["status"] == "incomplete"
    assert first["anomalies"] == [{"id": 1, "code": anomaly}]
    stale_raw = output.parent / first["exports"]["1"]["raw"]["path"]
    matching_raw = output.parent / first["exports"]["2"]["raw"]["path"]
    assert stale_raw.read_text() == "stale body"
    assert matching_raw.read_text() == "matching body"
    assert first["exports"]["2"]["detail_version"]["value"] == (
        "2026-11-09T08:00:00+08:00"
    )
    fixture["details"]["1"].update(
        {"body": "fresh body", "content_updated_at": "2026-11-09T01:00:00+01:00"}
    )
    del fixture["details"]["2"]  # A second fetch would fail instead of reusing it.
    write_json(fixture_path, fixture)

    retried = discover(*arguments)

    assert retried.returncode == 0, retried.stderr
    second = json.loads(output.read_text())
    assert second["status"] == "captured"
    assert second["anomalies"] == []
    assert second["exports"]["1"]["reused"] is False
    assert second["exports"]["2"]["reused"] is True
    fresh_raw = output.parent / second["exports"]["1"]["raw"]["path"]
    assert fresh_raw.read_text() == "fresh body"
    assert stale_raw.read_text() == "stale body"
    assert matching_raw.read_text() == "matching body"
    assert baseline.read_bytes() == baseline_bytes


def test_remote_retry_refetches_mismatched_detail_and_reuses_matching_checkpoint(
    tmp_path: Path,
):
    for timestamp, anomaly in [
        ("2026-11-08T00:00:00Z", "detail_older_than_listing"),
        ("2026-11-10T00:00:00Z", "detail_newer_than_listing"),
    ]:
        _assert_remote_retry_refetches_mismatched_detail_and_reuses_matching_checkpoint(
            tmp_path / anomaly, timestamp, anomaly
        )


def test_original_raw_reference_is_resolved_from_its_baseline(tmp_path: Path):
    baseline_dir = tmp_path / "baseline"
    baseline_path = baseline_dir / "snapshot.json"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing(
            [{"id": 1, "title": "One", "content_updated_at": "2026-11-07T00:00:00Z"}]
        ),
    )
    assert (
        discover(
            "--snapshot",
            str(baseline_path),
            "--staging",
            str(baseline_dir / "staging"),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    old_raw = baseline_dir / "staging" / "raw" / "old.md"
    old_raw.parent.mkdir(parents=True)
    old_raw.write_text("old body", encoding="utf-8")
    baseline = json.loads(baseline_path.read_text())
    baseline["exports"]["1"] = {
        "status": "captured",
        "raw": {
            "path": "staging/raw/old.md",
            "stage": "raw_export",
            "captured_at": "2026-11-07T00:00:00Z",
        },
        "detail_version": baseline["documents"]["1"]["version"],
    }
    write_json(baseline_path, baseline)
    current_listing = tmp_path / "current-listing.json"
    write_json(
        current_listing,
        listing(
            [{"id": 1, "title": "One", "content_updated_at": "2026-11-09T00:00:00Z"}]
        ),
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json",
        {"id": 1, "body": "new body", "content_updated_at": "2026-11-09T00:00:00Z"},
    )
    output = tmp_path / "next" / "snapshot.json"

    result = discover(
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline_path),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode == 0, result.stderr
    snapshot = json.loads(output.read_text())
    original = snapshot["exports"]["1"]["original"]
    assert original["status"] == "available"
    original_path = output.parent / original["raw"]["path"]
    assert original_path.resolve().read_text() == "old body"
    current_path = output.parent / snapshot["exports"]["1"]["raw"]["path"]
    assert current_path.resolve().read_text() == "new body"


def test_page_coverage_gap_cannot_produce_a_complete_snapshot(tmp_path: Path):
    documents = [
        {
            "id": doc_id,
            "title": str(doc_id),
            "content_updated_at": "2026-11-09T00:00:00Z",
        }
        for doc_id in range(51)
    ]
    payload = listing(documents)
    payload["listing"]["pages"] = [
        {"offset": 0, "limit": 50, "item_count": 50},
        {"offset": 100, "limit": 50, "item_count": 1},
    ]
    listing_path = tmp_path / "listing.json"
    write_json(listing_path, payload)
    output = tmp_path / "snapshot.json"

    result = discover(
        "--snapshot",
        str(output),
        "--listing",
        str(listing_path),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode != 0
    assert "page coverage" in result.stderr
    assert not output.exists()


def test_completed_snapshot_is_not_overwritten_by_resume(tmp_path: Path):
    listing_path = tmp_path / "listing.json"
    write_json(
        listing_path,
        listing(
            [{"id": 1, "title": "One", "content_updated_at": "2026-11-09T00:00:00Z"}]
        ),
    )
    output = tmp_path / "snapshot.json"
    arguments = [
        "--snapshot",
        str(output),
        "--listing",
        str(listing_path),
        "-s",
        "https://example.invalid/book",
    ]
    assert discover(*arguments).returncode == 0
    before = output.read_bytes()

    result = discover(*arguments)

    assert result.returncode != 0
    assert "already complete" in result.stderr
    assert output.read_bytes() == before


def test_remote_mode_reuses_book_listing_toc_and_detail_adapters(tmp_path: Path):
    documents = [
        {
            "id": 1,
            "title": "One",
            "slug": "one",
            "content_updated_at": "2026-11-07T00:00:00Z",
        }
    ]
    config = write_remote_adapter(tmp_path, documents)
    baseline = tmp_path / "baseline" / "snapshot.json"
    baseline_staging = tmp_path / "baseline" / "staging"
    initial = discover(
        "--config",
        str(config),
        "--snapshot",
        str(baseline),
        "--staging",
        str(baseline_staging),
        "-s",
        "https://example.invalid/book",
    )
    assert initial.returncode == 0, initial.stderr
    assert json.loads(baseline.read_text())["documents"]["1"]["toc_path"] == []
    assert not list(baseline_staging.glob("raw/*.md"))
    documents[0]["content_updated_at"] = "2026-11-09T00:00:00Z"
    write_remote_adapter(tmp_path, documents)
    output = tmp_path / "next" / "snapshot.json"
    staging = tmp_path / "next" / "staging"

    result = discover(
        "--config",
        str(config),
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline),
        "--staging",
        str(staging),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode == 0, result.stderr
    snapshot = json.loads(output.read_text())
    assert snapshot["changes"]["updated"] == [1]
    raw_path = output.parent / snapshot["exports"]["1"]["raw"]["path"]
    assert raw_path.read_text() == "body 1"
    assert snapshot["listing"]["pages"] == [{"offset": 0, "limit": 50, "item_count": 1}]


def _assert_remote_retry_excludes_unused_pages_from_older_larger_listing(
    tmp_path: Path, new_count: int, expected_pages: list[dict[str, int]]
):
    documents = [
        {
            "id": doc_id,
            "title": f"Document {doc_id}",
            "content_updated_at": "2026-11-09T00:00:00Z",
        }
        for doc_id in range(120)
    ]
    documents[-1]["content_updated_at"] = ""
    config = write_remote_adapter(tmp_path, documents)
    snapshot_path = tmp_path / "snapshot.json"
    staging = tmp_path / "staging"
    arguments = [
        "--config",
        str(config),
        "--snapshot",
        str(snapshot_path),
        "--staging",
        str(staging),
        "-s",
        "https://example.invalid/book",
    ]

    failed = discover(*arguments)

    assert failed.returncode != 0
    assert "no usable update timestamp" in failed.stderr
    assert not snapshot_path.exists()
    stale_page = staging / "pages" / "page-123-0000100.json"
    stale_bytes = stale_page.read_bytes()
    write_remote_adapter(tmp_path, documents[:new_count])

    retried = discover(*arguments)

    assert retried.returncode == 0, retried.stderr
    snapshot = json.loads(snapshot_path.read_text())
    assert snapshot["status"] == "initialized"
    assert snapshot["listing"]["document_ids"] == list(range(new_count))
    assert snapshot["listing"]["pages"] == expected_pages
    assert stale_page.read_bytes() == stale_bytes
    assert stale_page.stat().st_mode & 0o777 == 0o600


def test_remote_retry_excludes_unused_pages_from_older_larger_listing(tmp_path: Path):
    cases = [
        (0, [{"offset": 0, "limit": 50, "item_count": 0}]),
        (49, [{"offset": 0, "limit": 50, "item_count": 49}]),
        (
            50,
            [
                {"offset": 0, "limit": 50, "item_count": 50},
                {"offset": 50, "limit": 50, "item_count": 0},
            ],
        ),
        (
            51,
            [
                {"offset": 0, "limit": 50, "item_count": 50},
                {"offset": 50, "limit": 50, "item_count": 1},
            ],
        ),
    ]
    for count, pages in cases:
        _assert_remote_retry_excludes_unused_pages_from_older_larger_listing(
            tmp_path / str(count), count, pages
        )


def test_remote_short_nonfinal_page_cannot_claim_complete_coverage(tmp_path: Path):
    documents = [
        {
            "id": doc_id,
            "title": f"Document {doc_id}",
            "content_updated_at": "2026-11-09T00:00:00Z",
        }
        for doc_id in range(51)
    ]
    config = write_remote_adapter(tmp_path, documents)
    fixture_path = tmp_path / "remote.json"
    fixture = json.loads(fixture_path.read_text())
    fixture["page_overrides"] = {"0": documents[:49], "50": documents[49:]}
    write_json(fixture_path, fixture)
    output = tmp_path / "snapshot.json"

    result = discover(
        "--config",
        str(config),
        "--snapshot",
        str(output),
        "--staging",
        str(tmp_path / "staging"),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode != 0
    assert "short non-final page" in result.stderr
    assert not output.exists()


def test_existing_raw_reference_survives_an_unchanged_observation(tmp_path: Path):
    old = "2026-11-07T00:00:00Z"
    new = "2026-11-09T00:00:00Z"
    baseline_path = tmp_path / "baseline" / "snapshot.json"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": old}]),
    )
    assert (
        discover(
            "--snapshot",
            str(baseline_path),
            "--staging",
            str(tmp_path / "baseline" / "staging"),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    raw = tmp_path / "baseline" / "staging" / "raw" / "old.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("old body", encoding="utf-8")
    baseline = json.loads(baseline_path.read_text())
    baseline["documents"]["1"]["raw"] = {
        "path": "staging/raw/old.md",
        "stage": "raw_export",
        "captured_at": old,
    }
    write_json(baseline_path, baseline)
    unchanged_listing = tmp_path / "unchanged-listing.json"
    write_json(
        unchanged_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": old}]),
    )
    unchanged_path = tmp_path / "unchanged" / "snapshot.json"
    assert (
        discover(
            "--snapshot",
            str(unchanged_path),
            "--baseline-snapshot",
            str(baseline_path),
            "--listing",
            str(unchanged_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    unchanged = json.loads(unchanged_path.read_text())
    unchanged_raw = unchanged_path.parent / unchanged["documents"]["1"]["raw"]["path"]
    assert unchanged_raw.resolve().read_text() == "old body"
    updated_listing = tmp_path / "updated-listing.json"
    write_json(
        updated_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": new}]),
    )
    details = tmp_path / "details"
    write_json(
        details / "1.json",
        {"id": 1, "body": "new body", "content_updated_at": new},
    )
    updated_path = tmp_path / "updated" / "snapshot.json"

    result = discover(
        "--snapshot",
        str(updated_path),
        "--baseline-snapshot",
        str(unchanged_path),
        "--listing",
        str(updated_listing),
        "--details-dir",
        str(details),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode == 0, result.stderr
    updated = json.loads(updated_path.read_text())
    original = updated["exports"]["1"]["original"]
    assert original["status"] == "available"
    assert (
        updated_path.parent / original["raw"]["path"]
    ).resolve().read_text() == "old body"


def test_baseline_document_ids_must_match_its_complete_listing(tmp_path: Path):
    stamp = "2026-11-09T00:00:00Z"
    baseline_path = tmp_path / "baseline.json"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": stamp}]),
    )
    assert (
        discover(
            "--snapshot",
            str(baseline_path),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    baseline = json.loads(baseline_path.read_text())
    baseline["documents"] = {}
    write_json(baseline_path, baseline)
    output = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline_path),
        "--listing",
        str(baseline_listing),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode != 0
    assert "baseline document IDs" in result.stderr
    assert not output.exists()


def test_invalid_observation_cannot_claim_success(tmp_path: Path):
    cases = [
        ({"id": 1, "title": "Missing"}, True, "no usable update timestamp"),
        (
            {"id": 1, "title": "Naive", "content_updated_at": "2026-11-09T00:00:00"},
            True,
            "no timezone",
        ),
        (
            {
                "id": 1,
                "title": "Incomplete",
                "content_updated_at": "2026-11-09T00:00:00Z",
            },
            False,
            "not complete",
        ),
    ]
    for index, (document, complete, message) in enumerate(cases):
        listing_path = tmp_path / f"listing-{index}.json"
        write_json(listing_path, listing([document], complete=complete))
        output = tmp_path / f"snapshot-{index}.json"

        result = discover(
            "--snapshot",
            str(output),
            "--listing",
            str(listing_path),
            "-s",
            "https://example.invalid/book",
        )

        assert result.returncode != 0
        assert message in result.stderr
        assert not output.exists()


def test_pending_absent_identity_is_retained_without_claiming_deletion(tmp_path: Path):
    stamp = "2026-11-09T00:00:00Z"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": stamp}]),
    )
    baseline = tmp_path / "baseline.json"
    assert (
        discover(
            "--snapshot",
            str(baseline),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    current_listing = tmp_path / "current-listing.json"
    write_json(current_listing, listing([]))
    pending_state = tmp_path / "pending.json"
    write_json(
        pending_state,
        {
            "schema_version": "knowledge-capture-1.0",
            "book": {"book_id": 123},
            "pending": [{"id": 1, "reasons": ["review_pending"]}],
        },
    )
    output = tmp_path / "current.json"

    result = discover(
        "--snapshot",
        str(output),
        "--baseline-snapshot",
        str(baseline),
        "--pending-state",
        str(pending_state),
        "--listing",
        str(current_listing),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode == 0, result.stderr
    snapshot = json.loads(output.read_text())
    assert snapshot["changes"]["absent"] == [1]
    assert "deleted" not in snapshot["changes"]
    assert snapshot["selected"] == []
    assert snapshot["pending"] == [
        {"id": 1, "reasons": ["absent_from_current_listing", "review_pending"]}
    ]


def test_remote_pages_cannot_alias_or_contain_baseline_input(tmp_path: Path):
    documents = [
        {
            "id": 1,
            "title": "One",
            "slug": "one",
            "content_updated_at": "2026-11-09T00:00:00Z",
        }
    ]
    config = write_remote_adapter(tmp_path, documents)
    staging = tmp_path / "staging"
    baseline = staging / "pages" / "page-123-0000000.json"
    write_json(baseline, {"private": "baseline bytes"})
    before = baseline.read_bytes()

    result = discover(
        "--config",
        str(config),
        "--snapshot",
        str(tmp_path / "snapshot.json"),
        "--baseline-snapshot",
        str(baseline),
        "--staging",
        str(staging),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode != 0
    assert "REPORT_INPUT_COLLISION" in result.stderr
    assert baseline.read_bytes() == before


def _assert_existing_page_hardlink_to_baseline_is_rejected_before_remote_write(
    tmp_path: Path, offset: int
):
    documents = [
        {
            "id": 1,
            "title": "One",
            "slug": "one",
            "content_updated_at": "2026-11-09T00:00:00Z",
        }
    ]
    config = write_remote_adapter(tmp_path, documents)
    baseline = tmp_path / "baseline.json"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(baseline_listing, listing(documents))
    assert (
        discover(
            "--config",
            str(config),
            "--snapshot",
            str(baseline),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    staging = tmp_path / "staging"
    page = staging / "pages" / f"page-123-{offset:07}.json"
    page.parent.mkdir(parents=True)
    os.link(baseline, page)
    before = baseline.read_bytes()

    result = discover(
        "--config",
        str(config),
        "--snapshot",
        str(tmp_path / "next.json"),
        "--baseline-snapshot",
        str(baseline),
        "--staging",
        str(staging),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode != 0
    assert "REPORT_INPUT_COLLISION" in result.stderr
    assert baseline.read_bytes() == before


def test_existing_page_hardlink_to_baseline_is_rejected_before_remote_write(
    tmp_path: Path,
):
    for offset in (0, 100):
        _assert_existing_page_hardlink_to_baseline_is_rejected_before_remote_write(
            tmp_path / str(offset), offset
        )


def test_snapshot_cannot_collide_with_selected_raw_output(tmp_path: Path):
    old = "2026-11-07T00:00:00Z"
    new = "2026-11-09T00:00:00Z"
    baseline = tmp_path / "baseline.json"
    baseline_listing = tmp_path / "baseline-listing.json"
    write_json(
        baseline_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": old}]),
    )
    assert (
        discover(
            "--snapshot",
            str(baseline),
            "--listing",
            str(baseline_listing),
            "-s",
            "https://example.invalid/book",
        ).returncode
        == 0
    )
    current_listing = tmp_path / "current-listing.json"
    write_json(
        current_listing,
        listing([{"id": 1, "title": "One", "content_updated_at": new}]),
    )
    details = tmp_path / "details"
    detail = details / "1.json"
    write_json(detail, {"id": 1, "body": "new body", "content_updated_at": new})
    before = detail.read_bytes()
    staging = tmp_path / "staging"
    snapshot = staging / "raw" / "1-20261109T000000Z.md"

    result = discover(
        "--snapshot",
        str(snapshot),
        "--baseline-snapshot",
        str(baseline),
        "--staging",
        str(staging),
        "--listing",
        str(current_listing),
        "--details-dir",
        str(details),
        "-s",
        "https://example.invalid/book",
    )

    assert result.returncode != 0
    assert "REPORT_INPUT_COLLISION" in result.stderr
    assert not snapshot.exists()
    assert detail.read_bytes() == before


def test_failed_remote_capture_still_creates_private_pages(tmp_path: Path):
    config = write_remote_adapter(
        tmp_path,
        [
            {
                "id": 1,
                "title": "SYNTHETIC PRIVATE TITLE",
                "slug": "one",
                "content_updated_at": "",
            }
        ],
    )
    staging = tmp_path / "staging"
    previous_umask = os.umask(0o022)
    try:
        result = discover(
            "--config",
            str(config),
            "--snapshot",
            str(tmp_path / "snapshot.json"),
            "--staging",
            str(staging),
            "-s",
            "https://example.invalid/book",
        )
    finally:
        os.umask(previous_umask)

    page = staging / "pages" / "page-123-0000000.json"
    assert result.returncode != 0
    assert page.is_file()
    assert staging.stat().st_mode & 0o777 == 0o700
    assert page.parent.stat().st_mode & 0o777 == 0o700
    assert page.stat().st_mode & 0o777 == 0o600
