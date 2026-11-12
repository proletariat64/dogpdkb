"""Unified mdcompare + auto-plan stages, through the installed CLI entry.

Ticket 04: a whole-batch mdcompare stage completes first and the plan stage only
consumes its results. The plan replaces the superseded human-approval contract
with automatic body-reuse / compile / OCR / metadata lists. These tests drive the
installed ``dogpdkb sync`` black box with a temporary corpus and the existing
local Yuque / Graphify / OCR doubles; no real service is called.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_unified_sync import DOCUMENTS, Workspace, read_json, write_json

PLAN_SCHEMA = "unified-plan-1.0"
MDCOMPARE_SCHEMA = "unified-mdcompare-1.0"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, DOCUMENTS)


def _set_detail(workspace: Workspace, doc_id: int, **fields: object) -> None:
    remote = read_json(workspace.root / "remote.json")
    remote["details"][str(doc_id)].update(fields)
    write_json(workspace.root / "remote.json", remote)


def _set_title(workspace: Workspace, doc_id: int, title: str) -> None:
    remote = read_json(workspace.root / "remote.json")
    for document in remote["documents"]:
        if document["id"] == doc_id:
            document["title"] = title
    write_json(workspace.root / "remote.json", remote)


def _set_version(workspace: Workspace, doc_id: int, stamp: str) -> None:
    remote = read_json(workspace.root / "remote.json")
    for document in remote["documents"]:
        if document["id"] == doc_id:
            document["content_updated_at"] = stamp
    remote["details"][str(doc_id)]["content_updated_at"] = stamp
    write_json(workspace.root / "remote.json", remote)


def _plan(workspace: Workspace) -> dict:
    return read_json(workspace.only_run() / "plan" / "plan.json")


def _latest_plan(workspace: Workspace) -> dict:
    return read_json(workspace.runs()[-1] / "plan" / "plan.json")


def test_new_document_with_available_raw_auto_enters_compile(workspace: Workspace):
    completed = workspace.sync("--stop-after", "plan")

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["stages"]["capture"] == "completed"
    assert summary["stages"]["mdcompare"] == "completed"
    assert summary["stages"]["plan"] == "completed"

    run_dir = workspace.only_run()
    mdcompare = read_json(run_dir / "mdcompare" / "report.json")
    assert mdcompare["schema_version"] == MDCOMPARE_SCHEMA

    plan = read_json(run_dir / "plan" / "plan.json")
    assert plan["schema_version"] == PLAN_SCHEMA
    # Document Two has no graph baseline: a new document with complete raw
    # available auto-enters the full-document compile list, no human approval.
    compile_ids = {row["document_id"] for row in plan["compile"]}
    assert 2 in compile_ids
    assert plan["execution"]["approval_required"] is False
    assert plan["execution"]["automatic_extraction_allowed"] is True
    # The plan consumes mdcompare; it never re-compares or calls OCR/Graphify.
    assert plan["counts"]["external_calls"] == 0
    # No second approval plan is produced after OCR; compile may proceed with
    # currently-available content even when OCR is incomplete.
    assert plan["ordering"]["second_approval_after_ocr"] is False
    assert plan["ordering"]["compile_waits_for_ocr"] is False


def test_uncertain_and_new_documents_auto_compile(workspace: Workspace):
    # Doc One's historical-baseline body comparison is uncertain (REVIEW); Doc Two
    # is new. Both auto-enter the whole-document compile list when raw is available.
    completed = workspace.sync("--stop-after", "plan")
    assert completed.returncode == 0, completed.stderr
    plan = _plan(workspace)
    assert {row["document_id"] for row in plan["compile"]} == {1, 2}
    assert plan["body_reuse"] == []
    assert plan["execution"]["full_document_fallback"] is True
    one = next(row for row in plan["compile"] if row["document_id"] == 1)
    assert one["scope"] == "whole_document"
    assert "COMPARISON_UNCERTAIN_FULL_RECOMPILE" in one["reason_codes"]
    assert one["raw"] and one["raw"]["path"]


def test_identical_body_reuses_existing_knowledge(workspace: Workspace):
    _set_detail(workspace, 1, body="# One\n\nOld rule.\n")
    completed = workspace.sync("--stop-after", "plan")
    assert completed.returncode == 0, completed.stderr
    plan = _plan(workspace)
    reuse_ids = {row["document_id"] for row in plan["body_reuse"]}
    compile_ids = {row["document_id"] for row in plan["compile"]}
    assert 1 in reuse_ids
    assert 1 not in compile_ids
    assert 2 in compile_ids  # the new document still compiles
    # Metadata maintenance is not dropped just because the body is reused.
    assert 1 in {row["document_id"] for row in plan["metadata"]}
    # Body reuse keeps the existing knowledge: canonical graph stays covered and
    # advances to the verified incoming version without a recompile.
    state = read_json(workspace.corpus / ".sync-state.json")
    graph = state["books"]["fund"]["documents"]["1"]["graph"]
    assert graph["status"] == "covered"
    assert graph["coverage"] == "body_reuse"
    assert graph["version"]["utc"] == "2026-11-10T10:00:00+08:00"


def test_changed_image_with_reused_body_still_goes_to_ocr(workspace: Workspace):
    (workspace.corpus / "One-2026-11-10-[1].md").write_text(
        "# One\n\nOld rule.\n\n![img](https://cdn.example.invalid/old.png)\n",
        encoding="utf-8",
    )
    _set_detail(
        workspace,
        1,
        body="# One\n\nOld rule.\n\n![img](https://cdn.example.invalid/new.png)\n",
    )
    completed = workspace.sync("--stop-after", "plan")
    assert completed.returncode == 0, completed.stderr
    plan = _plan(workspace)
    # Identical body keeps its knowledge; the changed image still reaches OCR.
    assert 1 in {row["document_id"] for row in plan["body_reuse"]}
    ocr = {row["document_id"]: row for row in plan["ocr"]}
    assert 1 in ocr
    assert [item["resource_id"] for item in ocr[1]["resources"]] == [
        "https://cdn.example.invalid/new.png"
    ]


def test_new_document_image_goes_to_ocr_and_compile(workspace: Workspace):
    _set_detail(
        workspace, 2, body="body 2\n\n![img](https://cdn.example.invalid/a.png)\n"
    )
    completed = workspace.sync("--stop-after", "plan")
    assert completed.returncode == 0, completed.stderr
    plan = _plan(workspace)
    assert 2 in {row["document_id"] for row in plan["compile"]}
    ocr = {row["document_id"]: row for row in plan["ocr"]}
    assert 2 in ocr
    resource = ocr[2]["resources"][0]
    assert resource["url"] == "https://cdn.example.invalid/a.png"
    assert resource["kind"] == "image"
    assert resource["resource_id"] == "https://cdn.example.invalid/a.png"
    # Ordering: compile references the OCR resources it may consume, but does not
    # block on OCR completing.
    compile_two = next(row for row in plan["compile"] if row["document_id"] == 2)
    assert compile_two["ocr_dependencies"] == ["https://cdn.example.invalid/a.png"]
    assert compile_two["ocr_blocking"] is False


def test_rename_is_planned_as_metadata_maintenance(workspace: Workspace):
    _set_title(workspace, 1, "One Renamed")
    completed = workspace.sync("--stop-after", "plan")
    assert completed.returncode == 0, completed.stderr
    plan = _plan(workspace)
    metadata = {row["document_id"]: row for row in plan["metadata"]}
    assert 1 in metadata
    assert any(task["field"] == "name" for task in metadata[1]["tasks"])


def test_unavailable_current_input_archives_anomaly_not_compile(workspace: Workspace):
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    snapshot = read_json(run_dir / "capture" / "snapshot.json")
    raw = run_dir / "capture" / snapshot["documents"]["2"]["raw"]["path"]
    raw.unlink()  # the current complete raw is lost before mdcompare runs

    resumed = workspace.sync("--resume", first["run"])
    assert resumed.returncode != 0  # ocr is still unregistered

    plan = read_json(run_dir / "plan" / "plan.json")
    assert 2 not in {row["document_id"] for row in plan["compile"]}
    anomaly = next(row for row in plan["anomalies"] if row["document_id"] == 2)
    assert anomaly["stage"] == "plan"
    assert anomaly["code"] == "CAPTURE_RAW_UNAVAILABLE"
    assert anomaly["disposition"] == "pending"
    # The anomaly is durable outside wash, never a fabricated compile input.
    registry = read_json(workspace.root / "sync-results" / "fund" / "anomalies.json")
    assert anomaly["id"] in registry["anomalies"]


def test_same_version_archived_failure_is_suppressed_until_new_version(
    workspace: Workspace,
):
    # Batch 1: lose Doc Two's raw so plan archives a pending anomaly for its version.
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    snapshot = read_json(run_dir / "capture" / "snapshot.json")
    (run_dir / "capture" / snapshot["documents"]["2"]["raw"]["path"]).unlink()
    workspace.sync("--resume", first["run"])  # ends failed at ocr; anomaly archived

    # Batch 2: same version, raw available again -> still handed off, not retried.
    second = workspace.sync()
    assert second.returncode != 0
    plan2 = _latest_plan(workspace)
    assert 2 in {row["document_id"] for row in plan2["suppressed"]}
    assert 2 not in {row["document_id"] for row in plan2["compile"]}

    # Batch 3: a new version is processed normally.
    _set_version(workspace, 2, "2026-11-11T09:00:00+08:00")
    third = workspace.sync()
    assert third.returncode != 0
    plan3 = _latest_plan(workspace)
    assert 2 in {row["document_id"] for row in plan3["compile"]}
    assert 2 not in {row["document_id"] for row in plan3["suppressed"]}


def test_incoming_relative_image_resolves_against_original_raw_dir(tmp_path: Path):
    # M11: freezing copies only the markdown into mdcompare/inputs, not sibling
    # resources. A relative local image must resolve against the ORIGINAL raw-export
    # dir (where the sibling lives), not the frozen inputs dir, so its bytes stay
    # available for reuse proof. Drives the mdcompare row builder directly because
    # the sibling placement must be controlled relative to the pre-freeze raw path.
    from types import SimpleNamespace

    from dogpdkb.stages import mdcompare

    corpus = tmp_path / "corpus"
    staging = tmp_path / "mdcompare"
    rawdir = tmp_path / "capture" / "raw"
    (corpus / "graphify-out").mkdir(parents=True)
    staging.mkdir(parents=True)
    rawdir.mkdir(parents=True)
    (corpus / "graphify-out" / "graph.json").write_text(
        json.dumps({"nodes": [], "links": []}), encoding="utf-8"
    )
    (rawdir / "1.md").write_text(
        "# One\n\n![chart](images/chart.png)\n", encoding="utf-8"
    )
    sibling = rawdir / "images" / "chart.png"
    sibling.parent.mkdir()
    sibling.write_bytes(b"\x89PNG\r\n\x1a\nlocal-image-bytes")

    ver = {
        "field": "content_updated_at",
        "value": "2026-11-10T10:00:00+08:00",
        "utc": "2026-11-10T10:00:00+08:00",
    }
    book = {
        "key": "fund",
        "book_id": 123,
        "name": "UAT",
        "url": "https://example.invalid/t/u",
    }
    state = {
        "version": 2,
        "active_run": None,
        "books": {
            "fund": {"key": "fund", "book_id": 123, "url": book["url"], "documents": {}}
        },
    }
    (corpus / ".sync-state.json").write_text(json.dumps(state), encoding="utf-8")
    snapshot = {
        "schema_version": "knowledge-capture-1.0",
        "status": "captured",
        "book": book,
        "observation": {"started_at": "x", "ended_at": "y", "timezone": "UTC+8"},
        "listing": {"complete": True, "document_ids": [1], "document_count": 1},
        "changes": {"new": [1], "updated": [], "renamed": [], "moved": []},
        "selected": [1],
        "documents": {
            "1": {
                "id": 1,
                "title": "One",
                "version": ver,
                "toc_path": [],
                "raw": {"path": str(rawdir / "1.md"), "stage": "raw_export"},
            }
        },
        "exports": {
            "1": {
                "status": "captured",
                "reused": False,
                "raw": {"path": str(rawdir / "1.md"), "stage": "raw_export"},
                "detail_version": ver,
                "listing_version": ver,
            }
        },
        "pending": [],
        "anomalies": [],
        "suppressed": [],
    }
    snap = staging / "snapshot.json"
    snap.write_text(json.dumps(snapshot), encoding="utf-8")
    ctx = SimpleNamespace(directory=staging, corpus=corpus, require=lambda s, n: snap)

    rows = mdcompare._rows(ctx, snapshot)
    row = next(r for r in rows if r["document_id"] == 1)
    entry = next(e for e in row["resources"] if e["resource_id"] == "images/chart.png")
    assert entry.get("path") == str(sibling.resolve())
