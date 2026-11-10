from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def cli(*args: str):
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", *args],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )


def initialized(tmp_path: Path):
    corpus = tmp_path / "corpus/fund"
    corpus.mkdir(parents=True)
    (corpus / "One-2026-11-10-[1].md").write_text("# One\n\nOld rule.\n")
    graph = corpus / "graphify-out"
    graph.mkdir()
    write(
        graph / "graph.json",
        {"nodes": [{"id": "old", "source_file": "One-2026-11-10-[1].md"}], "links": []},
    )
    write(
        tmp_path / "books.json",
        [
            {
                "key": "fund",
                "url": "https://example.invalid/team/uat",
                "name": "UAT",
                "enabled": True,
            }
        ],
    )
    config = tmp_path / "config.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\ncorpus="corpus"\nstaging="staging"\nstate="state"\nweb="web"\n[skylark]\nmcp_url="https://example.invalid/mcp"\nproxy_bin="utoo-proxy"\n'
    )
    result = cli("init-state", "--config", str(config), "--book", "fund")
    assert result.returncode == 0, result.stderr
    return corpus, config


def test_initialized_baseline_downloads_same_day_and_persists_before_plan(
    tmp_path: Path,
):
    corpus, config = initialized(tmp_path)
    listing = tmp_path / "listing.json"
    details = tmp_path / "details"
    details.mkdir()
    doc = {
        "id": 1,
        "title": "One",
        "slug": "one",
        "content_updated_at": "2026-11-10T10:00:00+08:00",
    }
    write(
        listing,
        {
            "book": {"book_id": 123, "slug": "uat", "name": "UAT", "items_count": 1},
            "listing": {
                "complete": True,
                "document_count": 1,
                "pages": [{"offset": 0, "limit": 50, "item_count": 1}],
            },
            "documents": [doc],
        },
    )
    write(details / "1.json", {**doc, "body": "# One\n\nNew rule.\n"})
    snapshot = tmp_path / "current.json"
    result = cli(
        "discover",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(snapshot),
        "--staging",
        str(tmp_path / "capture"),
        "--listing",
        str(listing),
        "--details-dir",
        str(details),
    )
    assert result.returncode == 0, result.stderr
    capture = json.loads(snapshot.read_text())
    assert capture["selected"] == [1]
    state = json.loads((corpus / ".sync-state.json").read_text())
    record = state["books"]["fund"]["documents"]["1"]
    assert record["baseline"]["content_updated_at"] is None
    assert record["steps"]["download"]["status"] == "completed"
    assert record["steps"]["download"]["version"]["utc"] == "2026-11-10T10:00:00+08:00"
    assert record["steps"]["download"]["raw"]["stage"] == "raw_export"
    assert capture["observation"]["timezone"] == "UTC+8"
    planned = cli(
        "sync",
        "--knowledge",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(snapshot),
        "--staging",
        str(tmp_path / "planning"),
    )
    assert planned.returncode == 0, planned.stderr
    row = json.loads((tmp_path / "planning/plan.json").read_text())["results"][0]
    assert "COMPARISON_PENDING" not in row["pending"]


@pytest.mark.parametrize("covered", [True, False])
def test_knowledge_plan_uses_initialized_historical_baseline_without_raw_registration(
    tmp_path: Path,
    covered: bool,
):
    corpus, config = initialized(tmp_path)
    if not covered:
        write(corpus / "graphify-out/graph.json", {"nodes": [], "links": []})
        (corpus / ".sync-state.json").unlink()
        result = cli("init-state", "--config", str(config), "--book", "fund")
        assert result.returncode == 0, result.stderr
    raw = tmp_path / "new.md"
    raw.write_text("# One\n\nOld rule.\n\nAdditional rule.\n")
    version = {
        "field": "content_updated_at",
        "value": "2026-11-10T10:00:00+08:00",
        "utc": "2026-11-10T10:00:00+08:00",
    }
    snapshot = tmp_path / "snapshot.json"
    write(
        snapshot,
        {
            "schema_version": "knowledge-capture-1.0",
            "status": "captured",
            "book": {
                "book_id": 123,
                "slug": "uat",
                "name": "UAT",
                "key": "fund",
                "url": "https://example.invalid/team/uat",
            },
            "observation": {"ended_at": "2026-11-10T11:00:00+08:00"},
            "listing": {"complete": True, "document_count": 1, "document_ids": [1]},
            "documents": {
                "1": {
                    "id": 1,
                    "title": "One",
                    "version": version,
                    "raw": {"stage": "raw_export", "path": str(raw)},
                }
            },
            "selected": [1],
            "changes": {"updated": [1]},
        },
    )
    result = cli(
        "sync",
        "--knowledge",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(snapshot),
        "--staging",
        str(tmp_path / "planning"),
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads((tmp_path / "planning/plan.json").read_text())
    assert plan["counts"]["compared"] == 1
    row = plan["results"][0]
    assert row["graph"]["coverage"] == (
        "registered_historical_baseline"
        if covered
        else "unregistered_historical_baseline"
    )
    if not covered:
        assert row["action"] == "HOLD_EXISTING"
        assert row["graph"]["status"] == "uncovered"
    assert row["comparison"]["source_limitations"] == [
        "HISTORICAL_UNMARKED_TEXT_ORIGIN_UNVERIFIED"
    ]
    state = json.loads((corpus / ".sync-state.json").read_text())
    record = state["books"]["fund"]["documents"]["1"]
    assert record["steps"]["compare"]["status"] == "completed"
    assert record["graph"]["version"] is None
    assert record["baseline"]["content_kind"] == "historical_enriched"
    before = (corpus / ".sync-state.json").read_bytes()
    conflicting = cli(
        "sync",
        "--knowledge",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(snapshot),
        "--staging",
        str(tmp_path / "another-plan"),
    )
    assert conflicting.returncode != 0
    assert "original staging directory" in conflicting.stderr
    assert (corpus / ".sync-state.json").read_bytes() == before
    assert not (tmp_path / "another-plan/plan.json").exists()


def test_historical_filename_title_encoding_does_not_create_subject_change(
    tmp_path: Path,
):
    corpus, config = initialized(tmp_path)
    old = corpus / "One-2026-11-10-[1].md"
    old.rename(corpus / "Payment-Limits-2026-11-10-[1].md")
    write(
        corpus / "graphify-out/graph.json",
        {
            "nodes": [{"id": "old", "source_file": "Payment-Limits-2026-11-10-[1].md"}],
            "links": [],
        },
    )
    (corpus / ".sync-state.json").unlink()
    result = cli("init-state", "--config", str(config), "--book", "fund")
    assert result.returncode == 0, result.stderr
    raw = tmp_path / "current.md"
    raw.write_text("# One\n\nOld rule.\n")
    version = {
        "field": "content_updated_at",
        "value": "2026-11-10T10:00:00+08:00",
        "utc": "2026-11-10T10:00:00+08:00",
    }
    snap = tmp_path / "snapshot.json"
    write(
        snap,
        {
            "schema_version": "knowledge-capture-1.0",
            "status": "captured",
            "book": {"book_id": 123, "key": "fund", "name": "UAT"},
            "observation": {"ended_at": "2026-11-10T11:00:00+08:00"},
            "listing": {"complete": True, "document_count": 1, "document_ids": [1]},
            "documents": {
                "1": {
                    "id": 1,
                    "title": "Payment Limits",
                    "version": version,
                    "raw": {"stage": "raw_export", "path": str(raw)},
                }
            },
            "selected": [1],
            "changes": {"updated": [1]},
        },
    )
    result = cli(
        "sync",
        "--knowledge",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(snap),
        "--staging",
        str(tmp_path / "planning"),
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads((tmp_path / "planning/plan.json").read_text())
    assert plan["results"][0]["action"] == "KEEP_EXISTING"
    assert (
        "DOCUMENT_SUBJECT_CONTEXT_CHANGED"
        not in plan["results"][0]["comparison"]["reason_codes"]
    )


def test_approved_legacy_state_import_publishes_only_canonical_state(tmp_path: Path):
    from tests.test_knowledge_execute import (
        approval_document,
        candidate_plan,
        execute_arguments,
        install_graphify_stub,
        quality_document,
        sync,
    )

    plan, snapshot, legacy, staging, graph = candidate_plan(tmp_path)
    corpus, config = initialized(tmp_path)
    graph.mkdir()
    write(
        graph / "graph.json",
        {
            "directed": True,
            "nodes": [{"id": "old", "label": "Old", "source_file": "old-[1].md"}],
            "links": [],
            "hyperedges": [],
        },
    )
    write(tmp_path / "approvals.json", approval_document(plan))
    write(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    staging = tmp_path / "canonical-execution"
    imported = cli(
        "sync",
        "--knowledge",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(snapshot),
        "--state",
        str(legacy),
        "--staging",
        str(staging),
    )
    assert imported.returncode == 0, imported.stderr
    plan = json.loads((staging / "plan.json").read_text())
    approval = approval_document(plan)
    approval["plan"] = str(staging / "plan.json")
    quality = quality_document(plan)
    quality["plan"] = str(staging / "plan.json")
    write(tmp_path / "approvals.json", approval)
    write(tmp_path / "quality.json", quality)
    args = execute_arguments(tmp_path, snapshot, legacy, staging, graph)
    result = sync(
        *args,
        "--config",
        str(config),
        "--book",
        "fund",
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )
    assert result.returncode == 0, result.stderr
    root = json.loads((corpus / ".sync-state.json").read_text())
    record = root["books"]["fund"]["documents"]["1"]
    assert record["steps"]["graph_candidate"]["status"] == "completed"
    assert record["steps"]["graph_publish"]["status"] == "completed"
    assert record["graph"]["coverage"] == "approved_whole_document"
    assert root["active_run"] is None
    assert not (staging / "execution-corpus/.sync-state.json").exists()
    assert (
        json.loads(legacy.read_text())["schema_version"]
        == "knowledge-state-migrated-1.0"
    )


def test_conflicting_snapshot_cannot_change_state_or_download(tmp_path: Path):
    corpus, config = initialized(tmp_path)
    state_path = corpus / ".sync-state.json"
    state = json.loads(state_path.read_text())
    state["active_run"] = {
        "staging": str(tmp_path / "old.json"),
        "book_key": "fund",
        "phase": "compare",
    }
    write(state_path, state)
    before = state_path.read_bytes()
    listing_path = tmp_path / "list.json"
    write(
        listing_path,
        {
            "book": {"book_id": 123, "slug": "uat", "name": "UAT", "items_count": 0},
            "listing": {
                "complete": True,
                "document_count": 0,
                "pages": [{"offset": 0, "limit": 50, "item_count": 0}],
            },
            "documents": [],
        },
    )
    result = cli(
        "discover",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(tmp_path / "new.json"),
        "--staging",
        str(tmp_path / "new-capture"),
        "--listing",
        str(listing_path),
    )
    assert result.returncode != 0
    assert "unfinished sync plan" in result.stderr
    assert state_path.read_bytes() == before
    assert not (tmp_path / "new.json").exists()
    assert not list((tmp_path / "new-capture").glob("raw/*"))


def test_partial_capture_keeps_completed_download_and_resume_reuses_raw(tmp_path: Path):
    corpus, config = initialized(tmp_path)
    documents = [
        {
            "id": i,
            "title": f"Doc {i}",
            "content_updated_at": "2026-11-10T10:00:00+08:00",
        }
        for i in (1, 2)
    ]
    listing_path = tmp_path / "listing.json"
    details = tmp_path / "details"
    details.mkdir()
    write(
        listing_path,
        {
            "book": {"book_id": 123, "slug": "uat", "name": "UAT", "items_count": 2},
            "listing": {
                "complete": True,
                "document_count": 2,
                "pages": [{"offset": 0, "limit": 50, "item_count": 2}],
            },
            "documents": documents,
        },
    )
    write(details / "1.json", {**documents[0], "body": "# Doc 1\n\nFirst raw.\n"})
    snapshot = tmp_path / "current.json"
    staging = tmp_path / "capture"
    arguments = [
        "discover",
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
    failed = cli(*arguments)
    assert failed.returncode == 1
    state = json.loads((corpus / ".sync-state.json").read_text())
    assert (
        state["books"]["fund"]["documents"]["1"]["steps"]["download"]["status"]
        == "completed"
    )
    assert (
        state["books"]["fund"]["documents"]["2"]["steps"]["download"]["status"]
        == "failed"
    )
    assert (
        state["books"]["fund"]["documents"]["1"]["baseline"]["content_updated_at"]
        is None
    )
    (details / "1.json").unlink()
    write(details / "2.json", {**documents[1], "body": "# Doc 2\n\nSecond raw.\n"})
    resumed = cli(*arguments)
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(snapshot.read_text())
    assert result["exports"]["1"]["reused"] is True
    assert result["exports"]["1"]["raw"]["adapter"] == "canonical_download"
    assert result["exports"]["2"]["status"] == "captured"


def test_naive_remote_time_defaults_utc8_and_utc_crossing_selects_same_day(
    tmp_path: Path,
):
    _corpus, config = initialized(tmp_path)
    documents = [
        {"id": 1, "title": "One", "content_updated_at": "2026-11-09T18:30:00Z"},
        {"id": 2, "title": "Two", "content_updated_at": "2026-11-10T02:31:00"},
    ]
    listing_path = tmp_path / "listing.json"
    details = tmp_path / "details"
    details.mkdir()
    write(
        listing_path,
        {
            "book": {"book_id": 123, "slug": "uat", "name": "UAT", "items_count": 2},
            "listing": {
                "complete": True,
                "document_count": 2,
                "pages": [{"offset": 0, "limit": 50, "item_count": 2}],
            },
            "documents": documents,
        },
    )
    for document in documents:
        write(
            details / f"{document['id']}.json", {**document, "body": "Captured body."}
        )
    output = tmp_path / "snapshot.json"
    result = cli(
        "discover",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(output),
        "--staging",
        str(tmp_path / "capture"),
        "--listing",
        str(listing_path),
        "--details-dir",
        str(details),
    )
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(output.read_text())
    assert snapshot["selected"] == [1, 2]
    assert snapshot["documents"]["1"]["version"]["utc"] == "2026-11-10T02:30:00+08:00"
    assert snapshot["documents"]["2"]["version"]["default_timezone"] is True
    assert snapshot["documents"]["2"]["version"]["utc"] == "2026-11-10T02:31:00+08:00"


def test_direct_graph_stage_reuses_initialized_book_without_shadow_entry(
    tmp_path: Path,
):
    corpus, config = initialized(tmp_path)
    manifest = tmp_path / "manifest.json"
    write(manifest, {"exported": [], "deleted": []})
    result = cli(
        "update-graph",
        "--config",
        str(config),
        "--input",
        str(corpus),
        "--manifest",
        str(manifest),
        "--staging",
        str(tmp_path / "graph-stage"),
        "--graph-dir",
        str(corpus / "graphify-out"),
    )
    assert result.returncode == 0, result.stderr
    state = json.loads((corpus / ".sync-state.json").read_text())
    assert list(state["books"]) == ["fund"]
    assert state["books"]["fund"]["steps"]["graph"]["status"] == "completed"


def test_date_only_remote_version_cannot_be_saved_as_exact_instant(tmp_path: Path):
    corpus, config = initialized(tmp_path)
    listing_path = tmp_path / "listing.json"
    write(
        listing_path,
        {
            "book": {"book_id": 123, "slug": "uat", "name": "UAT", "items_count": 1},
            "listing": {
                "complete": True,
                "document_count": 1,
                "pages": [{"offset": 0, "limit": 50, "item_count": 1}],
            },
            "documents": [
                {"id": 1, "title": "One", "content_updated_at": "2026-11-10"}
            ],
        },
    )
    output = tmp_path / "snapshot.json"
    result = cli(
        "discover",
        "--config",
        str(config),
        "--book",
        "fund",
        "--snapshot",
        str(output),
        "--staging",
        str(tmp_path / "capture"),
        "--listing",
        str(listing_path),
    )
    assert result.returncode != 0
    assert not output.exists()
    state = json.loads((corpus / ".sync-state.json").read_text())
    record = state["books"]["fund"]["documents"]["1"]
    assert record["baseline"]["content_updated_at"] is None
    assert record["observed"] is None
    assert record["steps"].get("download") is None
