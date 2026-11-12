"""Persistent batch results, anomaly handoff and disposition via the installed CLI.

Black-box seam: ``dogpdkb sync`` (capture archiving) and ``dogpdkb anomalies``
(read + disposition). Temporary corpora and the existing local Yuque double only;
no real Yuque/OCR/Graphify/network/paid calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_unified_sync import DOCUMENTS, Workspace, read_json, write_json


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    ws = Workspace(tmp_path, DOCUMENTS)
    # A durable results store outside the cleanable wash run directory.
    text = ws.config.read_text(encoding="utf-8").replace(
        'web="web"', 'web="web"\nresults="results"'
    )
    ws.config.write_text(text, encoding="utf-8")
    ws.results = tmp_path / "results" / "fund"  # type: ignore[attr-defined]
    return ws


def make_capture_anomaly(workspace: Workspace) -> None:
    """doc 2 detail is newer than its listing: a capture anomaly with raw present."""
    remote = read_json(workspace.root / "remote.json")
    remote["details"]["2"]["content_updated_at"] = "2026-11-10T12:00:00+08:00"
    write_json(workspace.root / "remote.json", remote)


def make_export_failure(workspace: Workspace) -> None:
    """doc 2 detail has no body: its export fails and is archived as a failure."""
    remote = read_json(workspace.root / "remote.json")
    remote["details"]["2"].pop("body", None)
    write_json(workspace.root / "remote.json", remote)


def publish_new_version(workspace: Workspace) -> None:
    """doc 2 gets a valid, newer remote version (listing and detail agree)."""
    remote = read_json(workspace.root / "remote.json")
    stamp = "2026-11-11T10:00:00+08:00"
    remote["documents"][1]["content_updated_at"] = stamp
    remote["details"]["2"] = {
        "id": 2,
        "body": "body 2 v2",
        "content_updated_at": stamp,
    }
    write_json(workspace.root / "remote.json", remote)


def run_batch(workspace: Workspace) -> str:
    """One daily batch; stages owned by later tickets may still be unregistered."""
    completed = workspace.sync()
    assert completed.returncode in (0, 1), completed.stderr
    run = workspace.runs()[-1]
    state = read_json(run / "run.json")
    assert state["stage_states"]["capture"]["status"] in {
        "completed",
        "partial",
        "skipped",
    }, state["stage_states"]["capture"]
    return run.name


def only_anomaly(workspace: Workspace) -> dict[str, Any]:
    registry = read_json(workspace.results / "anomalies.json")
    assert registry["schema_version"] == "unified-anomalies-1.0"
    records = registry["anomalies"]
    assert len(records) == 1, records
    return next(iter(records.values()))


def test_capture_anomaly_is_archived_outside_wash_with_a_persistent_input(
    workspace: Workspace,
):
    make_capture_anomaly(workspace)

    completed = workspace.sync("--stop-after", "capture")

    assert completed.returncode == 0, completed.stderr
    run_dir = workspace.only_run()
    record = only_anomaly(workspace)
    assert record["book_key"] == "fund"
    assert record["document_id"] == 2
    assert record["stage"] == "capture"
    assert record["code"] == "detail_newer_than_listing"
    assert record["disposition"] == "pending"
    # Identity reuses the observed (listing) source version, not the detail export.
    assert record["source_version"]["utc"].startswith("2026-11-10T11:00:00")

    # The necessary raw input is persisted outside wash and byte-identical.
    snapshot = read_json(run_dir / "capture" / "snapshot.json")
    raw_ref = snapshot["exports"]["2"]["raw"]["path"]
    raw = (run_dir / "capture" / raw_ref).resolve()
    inputs = record["necessary_inputs"]
    assert inputs, record
    durable = Path(inputs[0]["path"])
    assert durable.is_file()
    assert not durable.is_relative_to(workspace.staging)
    assert durable.read_bytes() == raw.read_bytes()


def test_persistent_input_survives_wash_cleanup(workspace: Workspace):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    record = only_anomaly(workspace)
    durable = Path(record["necessary_inputs"][0]["path"])
    expected = durable.read_bytes()

    # Simulate ticket 07 removing the finished wash run directory.
    for run in workspace.runs():
        for path in sorted(run.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        run.rmdir()

    assert durable.is_file()
    assert durable.read_bytes() == expected
    # The registry itself is durable and still readable.
    assert only_anomaly(workspace)["document_id"] == 2


def test_batch_result_is_persisted_outside_wash_and_survives_cleanup(
    workspace: Workspace,
):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    run_id = workspace.only_run().name

    result_path = workspace.results / "runs" / f"{run_id}.json"
    assert result_path.is_file()
    assert not result_path.is_relative_to(workspace.staging)
    result = read_json(result_path)
    assert result["schema_version"] == "unified-result-1.0"
    assert result["run_id"] == run_id
    assert result["book_key"] == "fund"
    assert result["status"] == "paused"
    assert result["stages"]["capture"]["status"] == "partial"
    assert result["counts"]["total"] == 2
    assert result["anomaly_ids"] == [only_anomaly(workspace)["id"]]

    # A finished wash run can be removed without losing the structured result.
    for run in workspace.runs():
        for path in sorted(run.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        run.rmdir()
    assert read_json(result_path)["run_id"] == run_id


def anomalies_cli(workspace: Workspace, *args: str):
    return workspace.cli(
        "anomalies", "--config", str(workspace.config), "--book", "fund", *args
    )


def test_anomalies_scan_lists_durable_results_and_anomalies_without_executing(
    workspace: Workspace,
):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    run_id = workspace.only_run().name
    registry_before = (workspace.results / "anomalies.json").read_bytes()
    state_before = (workspace.corpus / ".sync-state.json").read_bytes()

    scanned = anomalies_cli(workspace, "--json")

    assert scanned.returncode == 0, scanned.stderr
    view = json.loads(scanned.stdout)
    assert view["schema_version"] == "unified-anomaly-scan-1.0"
    assert view["book_key"] == "fund"
    assert [row["run_id"] for row in view["runs"]] == [run_id]
    assert view["runs"][0]["status"] == "paused"
    record = only_anomaly(workspace)
    assert [row["id"] for row in view["anomalies"]] == [record["id"]]
    assert view["anomalies"][0]["disposition"] == "pending"
    assert view["counts"]["anomalies"] == 1
    assert view["counts"]["pending"] == 1
    # The scan hands the post-hoc agent readable durable inputs, not wash refs.
    assert Path(view["anomalies"][0]["necessary_inputs"][0]["path"]).is_file()

    again = anomalies_cli(workspace, "--json")
    assert again.returncode == 0
    assert again.stdout == scanned.stdout
    # A repeated scan executes nothing: no new run, no state or registry change.
    assert len(workspace.runs()) == 1
    assert (workspace.results / "anomalies.json").read_bytes() == registry_before
    assert (workspace.corpus / ".sync-state.json").read_bytes() == state_before


def test_anomalies_scan_without_history_returns_an_empty_view(workspace: Workspace):
    scanned = anomalies_cli(workspace, "--json")

    assert scanned.returncode == 0, scanned.stderr
    view = json.loads(scanned.stdout)
    assert view["runs"] == []
    assert view["anomalies"] == []
    assert view["counts"]["anomalies"] == 0


def test_anomalies_show_reads_one_record_and_resolve_records_disposition(
    workspace: Workspace,
):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    aid = only_anomaly(workspace)["id"]
    state_before = (workspace.corpus / ".sync-state.json").read_bytes()

    shown = anomalies_cli(workspace, "--show", aid)
    assert shown.returncode == 0, shown.stderr
    record = json.loads(shown.stdout)
    assert record["id"] == aid
    assert record["document_id"] == 2
    assert Path(record["necessary_inputs"][0]["path"]).is_file()

    resolved = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "abandoned",
        "--note",
        "superseded",
    )
    assert resolved.returncode == 0, resolved.stderr
    assert json.loads(resolved.stdout)["disposition"] == "abandoned"
    registry = read_json(workspace.results / "anomalies.json")
    record = registry["anomalies"][aid]
    assert record["disposition"] == "abandoned"
    assert record["resolution"]["note"] == "superseded"
    assert record["resolved_at"]
    # A disposition is handoff state: it never fabricates canonical success.
    assert (workspace.corpus / ".sync-state.json").read_bytes() == state_before

    registry_before = (workspace.results / "anomalies.json").read_bytes()
    repeat = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "abandoned",
        "--note",
        "superseded",
    )
    assert repeat.returncode == 0, repeat.stderr
    assert (workspace.results / "anomalies.json").read_bytes() == registry_before

    unknown = anomalies_cli(workspace, "--resolve", "missing", "--disposition", "stale")
    assert unknown.returncode != 0
    assert "missing" in unknown.stderr
    shown_unknown = anomalies_cli(workspace, "--show", "missing")
    assert shown_unknown.returncode != 0


def test_a_single_remote_export_failure_is_archived_without_blocking_others(
    workspace: Workspace,
):
    make_export_failure(workspace)

    run_batch(workspace)

    run = workspace.runs()[-1]
    snapshot = read_json(run / "capture" / "snapshot.json")
    # doc 1 still captured; doc 2's failure is an archived anomaly, not a stage failure.
    assert snapshot["exports"]["1"]["status"] == "captured"
    assert snapshot["exports"]["2"]["status"] == "failed"
    assert snapshot["anomalies"] == [{"id": 2, "code": "export_failed"}]
    record = only_anomaly(workspace)
    assert record["code"] == "export_failed"
    assert record["disposition"] == "pending"


def test_archived_export_failure_is_not_retried_by_the_next_daily_sync(
    workspace: Workspace,
):
    make_export_failure(workspace)
    first = run_batch(workspace)
    record = only_anomaly(workspace)
    assert record["code"] == "export_failed"
    assert record["attempts"] == 1

    second = run_batch(workspace)

    # The handed-off source version is not re-attempted: no new export, no new
    # attempt recorded, and the batch still references the archived anomaly.
    registry = read_json(workspace.results / "anomalies.json")
    record = registry["anomalies"][record["id"]]
    assert record["attempts"] == 1
    assert record["runs"] == [first]
    assert record["disposition"] == "pending"
    snapshot = read_json(
        workspace.staging / "fund" / second / "capture" / "snapshot.json"
    )
    assert 2 not in snapshot["selected"]
    assert "2" not in snapshot["exports"]
    assert snapshot["suppressed"] == [
        {
            "id": 2,
            "anomaly_id": record["id"],
            "code": "export_failed",
            "version_stamp": record["version_stamp"],
        }
    ]
    result = read_json(workspace.results / "runs" / f"{second}.json")
    assert record["id"] in result["anomaly_ids"]
    assert result["stages"]["capture"]["status"] == "partial"
    # Attempt/handoff state never fabricates a successful download.
    state = read_json(workspace.corpus / ".sync-state.json")
    assert state["books"]["fund"]["documents"]["2"]["steps"]["download"]["status"] == (
        "failed"
    )


def test_run_scoped_scan_includes_an_anomaly_carried_into_a_later_result(
    workspace: Workspace,
):
    make_export_failure(workspace)
    first = run_batch(workspace)
    aid = only_anomaly(workspace)["id"]
    second = run_batch(workspace)

    # The anomaly was registered in the first run only, but the second run's
    # durable result carries it forward as a live handoff.
    registry = read_json(workspace.results / "anomalies.json")
    assert registry["anomalies"][aid]["runs"] == [first]
    result = read_json(workspace.results / "runs" / f"{second}.json")
    assert aid in result["anomaly_ids"]

    scanned = anomalies_cli(workspace, "--run", second, "--json")
    assert scanned.returncode == 0, scanned.stderr
    view = json.loads(scanned.stdout)
    assert [row["id"] for row in view["anomalies"]] == [aid]
    assert view["counts"]["anomalies"] == 1
    # Scanning is read-only: it never increments retry attempts.
    assert view["anomalies"][0]["attempts"] == 1


def test_a_new_remote_version_syncs_normally_despite_an_archived_failure(
    workspace: Workspace,
):
    make_export_failure(workspace)
    run_batch(workspace)
    archived = only_anomaly(workspace)

    publish_new_version(workspace)
    second = run_batch(workspace)

    snapshot = read_json(
        workspace.staging / "fund" / second / "capture" / "snapshot.json"
    )
    assert 2 in snapshot["selected"]
    assert snapshot["exports"]["2"]["status"] == "captured"
    assert snapshot["suppressed"] == []
    # The old-version handoff is untouched; the new version produced no anomaly.
    registry = read_json(workspace.results / "anomalies.json")
    assert list(registry["anomalies"]) == [archived["id"]]
    assert registry["anomalies"][archived["id"]]["attempts"] == 1


def test_abandoning_an_old_version_does_not_ban_the_source(workspace: Workspace):
    make_export_failure(workspace)
    run_batch(workspace)
    aid = only_anomaly(workspace)["id"]
    resolved = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "abandoned",
        "--note",
        "give up v1",
    )
    assert resolved.returncode == 0, resolved.stderr

    publish_new_version(workspace)
    second = run_batch(workspace)

    snapshot = read_json(
        workspace.staging / "fund" / second / "capture" / "snapshot.json"
    )
    assert snapshot["exports"]["2"]["status"] == "captured"
    registry = read_json(workspace.results / "anomalies.json")
    assert registry["anomalies"][aid]["disposition"] == "abandoned"
    assert len(registry["anomalies"]) == 1


def test_stale_disposition_hands_the_failure_back_to_daily_sync(workspace: Workspace):
    make_export_failure(workspace)
    first = run_batch(workspace)
    aid = only_anomaly(workspace)["id"]
    resolved = anomalies_cli(workspace, "--resolve", aid, "--disposition", "stale")
    assert resolved.returncode == 0, resolved.stderr

    # A stale handoff no longer suppresses: the daily batch re-attempts the export.
    second = run_batch(workspace)
    registry = read_json(workspace.results / "anomalies.json")
    record = registry["anomalies"][aid]
    assert record["attempts"] == 2
    assert record["runs"] == [first, second]
    # The repeated failure is handed off again instead of retrying every day.
    assert record["disposition"] == "pending"

    third = run_batch(workspace)
    record = read_json(workspace.results / "anomalies.json")["anomalies"][aid]
    assert record["attempts"] == 2
    assert record["runs"] == [first, second]
    snapshot = read_json(
        workspace.staging / "fund" / third / "capture" / "snapshot.json"
    )
    assert 2 not in snapshot["selected"]


def write_evidence(
    workspace: Workspace, *, cache: Path, input_changed: bool, step: str = "ocr"
) -> Path:
    evidence = workspace.root / "evidence.json"
    write_json(
        evidence,
        {
            "schema_version": "unified-posthoc-evidence-1.0",
            "cache": [{"kind": "ocr", "path": str(cache)}],
            "completion": {"step": step, "details": {"cache": str(cache)}},
            "input_changed": input_changed,
        },
    )
    return evidence


def test_posthoc_done_updates_real_completion_and_registers_compile_followup(
    workspace: Workspace,
):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    record = only_anomaly(workspace)
    aid = record["id"]
    cache = workspace.root / "ocr-cache.json"
    write_json(cache, {"image": "img-1", "text": "backfilled"})
    evidence = write_evidence(workspace, cache=cache, input_changed=True)

    resolved = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "done",
        "--evidence",
        str(evidence),
        "--note",
        "backfilled",
    )

    assert resolved.returncode == 0, resolved.stderr
    out = json.loads(resolved.stdout)
    assert out["disposition"] == "done"
    assert out["resolution"]["note"] == "backfilled"
    assert out["resolution"]["cache"][0]["path"] == str(cache.resolve())
    followup_id = out["resolution"]["followup_id"]
    assert followup_id
    # Real completion is recorded in canonical state, separate from disposition.
    state = read_json(workspace.corpus / ".sync-state.json")
    step = state["books"]["fund"]["documents"]["2"]["steps"]["ocr"]
    assert step["status"] == "completed"
    # Changing published compile input explicitly registers follow-up compile
    # work; writing the OCR cache alone is not a completed knowledge update.
    followups = read_json(workspace.results / "compile-followups.json")
    assert followups["schema_version"] == "unified-compile-followups-1.0"
    followup = followups["followups"][followup_id]
    assert followup["document_id"] == 2
    assert followup["status"] == "pending"
    assert followup["anomaly_id"] == aid
    assert followup["version_stamp"] == record["version_stamp"]
    assert followup["inputs"] == [{"kind": "ocr", "path": str(cache.resolve())}]
    view = json.loads(anomalies_cli(workspace, "--json").stdout)
    assert [row["id"] for row in view["compile_followups"]] == [followup_id]

    # Repeating the same post-hoc success never duplicates the follow-up work.
    again = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "done",
        "--evidence",
        str(evidence),
    )
    assert again.returncode == 0, again.stderr
    followups = read_json(workspace.results / "compile-followups.json")
    assert len(followups["followups"]) == 1


def test_posthoc_done_refuses_missing_or_unreadable_evidence(workspace: Workspace):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    aid = only_anomaly(workspace)["id"]
    registry_before = (workspace.results / "anomalies.json").read_bytes()
    state_before = (workspace.corpus / ".sync-state.json").read_bytes()

    missing = anomalies_cli(workspace, "--resolve", aid, "--disposition", "done")
    assert missing.returncode != 0
    assert "evidence" in missing.stderr

    evidence = write_evidence(
        workspace, cache=workspace.root / "absent.json", input_changed=True
    )
    unreadable = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "done",
        "--evidence",
        str(evidence),
    )
    assert unreadable.returncode != 0
    # Nothing is fabricated: no disposition, no canonical success, no follow-up.
    assert (workspace.results / "anomalies.json").read_bytes() == registry_before
    assert (workspace.corpus / ".sync-state.json").read_bytes() == state_before
    assert not (workspace.results / "compile-followups.json").exists()


def test_posthoc_done_without_input_change_registers_no_followup(workspace: Workspace):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    aid = only_anomaly(workspace)["id"]
    cache = workspace.root / "ocr-cache.json"
    write_json(cache, {"image": "img-1", "text": "backfilled"})
    evidence = write_evidence(workspace, cache=cache, input_changed=False)

    resolved = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "done",
        "--evidence",
        str(evidence),
    )

    assert resolved.returncode == 0, resolved.stderr
    out = json.loads(resolved.stdout)
    assert out["disposition"] == "done"
    assert out["resolution"]["followup_id"] is None
    view = json.loads(anomalies_cli(workspace, "--json").stdout)
    assert view["compile_followups"] == []


def test_done_anomaly_does_not_keep_the_next_batch_partial(workspace: Workspace):
    make_export_failure(workspace)
    run_batch(workspace)
    record = only_anomaly(workspace)
    aid = record["id"]
    cache = workspace.root / "ocr-cache.json"
    write_json(cache, {"image": "img-1", "text": "backfilled"})
    evidence = write_evidence(workspace, cache=cache, input_changed=False)
    resolved = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "done",
        "--evidence",
        str(evidence),
    )
    assert resolved.returncode == 0, resolved.stderr
    assert json.loads(resolved.stdout)["disposition"] == "done"

    # A disposed (done) anomaly is no current failure: the next daily batch
    # still suppresses the resolved version (no re-export) but completes capture
    # instead of handing the resolved record back as a live anomaly.
    second = run_batch(workspace)
    snapshot = read_json(
        workspace.staging / "fund" / second / "capture" / "snapshot.json"
    )
    assert 2 not in snapshot["selected"]
    assert snapshot["suppressed"] == [
        {
            "id": 2,
            "anomaly_id": aid,
            "code": "export_failed",
            "version_stamp": record["version_stamp"],
        }
    ]
    result = read_json(workspace.results / "runs" / f"{second}.json")
    assert result["stages"]["capture"]["status"] == "completed"
    assert aid not in result["anomaly_ids"]
    assert aid not in result["stages"]["capture"]["anomaly_ids"]


def test_replaying_identical_done_evidence_does_not_reopen_a_consumed_followup(
    workspace: Workspace,
):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    aid = only_anomaly(workspace)["id"]
    cache = workspace.root / "ocr-cache.json"
    write_json(cache, {"image": "img-1", "text": "backfilled"})
    evidence = write_evidence(workspace, cache=cache, input_changed=True)
    resolved = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "done",
        "--evidence",
        str(evidence),
    )
    assert resolved.returncode == 0, resolved.stderr
    fid = json.loads(resolved.stdout)["resolution"]["followup_id"]

    # Compile publishes the source and consumes the follow-up to done.
    followups_path = workspace.results / "compile-followups.json"
    followups = read_json(followups_path)
    followups["followups"][fid]["status"] = "done"
    followups["followups"][fid]["resolution"] = {
        "disposition": "done",
        "reason": "compile published the source with the updated input",
    }
    write_json(followups_path, followups)

    # Replaying the identical post-hoc success must not reopen consumed work.
    again = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "done",
        "--evidence",
        str(evidence),
    )
    assert again.returncode == 0, again.stderr
    assert read_json(followups_path)["followups"][fid]["status"] == "done"

    # Genuinely new evidence still reopens the follow-up for fresh compile work.
    fresh = workspace.root / "evidence-fresh.json"
    write_json(
        fresh,
        {
            "schema_version": "unified-posthoc-evidence-1.0",
            "cache": [{"kind": "ocr", "path": str(cache)}],
            "completion": {
                "step": "ocr",
                "details": {"cache": str(cache), "revision": 2},
            },
            "input_changed": True,
        },
    )
    reopened = anomalies_cli(
        workspace,
        "--resolve",
        aid,
        "--disposition",
        "done",
        "--evidence",
        str(fresh),
    )
    assert reopened.returncode == 0, reopened.stderr
    assert read_json(followups_path)["followups"][fid]["status"] == "pending"


def test_batch_result_carries_document_identities_surviving_wash(workspace: Workspace):
    make_capture_anomaly(workspace)
    assert workspace.sync("--stop-after", "capture").returncode == 0
    run_id = workspace.only_run().name
    result_path = workspace.results / "runs" / f"{run_id}.json"

    result = read_json(result_path)
    docs = {row["document_id"]: row for row in result["documents"]}
    assert set(docs) == {1, 2}
    assert docs[2]["source_version"]["utc"].startswith("2026-11-10T11:00:00")
    assert docs[1]["export_status"] == "captured"
    assert docs[2]["export_status"] == "captured"

    for run in workspace.runs():
        for path in sorted(run.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        run.rmdir()
    result = read_json(result_path)
    assert {row["document_id"] for row in result["documents"]} == {1, 2}


def test_archiving_failure_keeps_inputs_and_a_valid_corpus(workspace: Workspace):
    make_capture_anomaly(workspace)
    # Block the durable store: archiving cannot persist, but must not lose input
    # or break a valid corpus, and the usable set still ends the stage.
    (workspace.root / "results").write_text("not a directory", encoding="utf-8")

    completed = workspace.sync("--stop-after", "capture")

    assert completed.returncode == 0, completed.stderr
    run = workspace.only_run()
    state = read_json(run / "run.json")
    assert state["status"] == "paused"
    assert state["stage_states"]["capture"]["status"] == "partial"
    assert "archiving failed" in (run / "logs" / "capture.log").read_text()
    assert "result archiving failed" in completed.stderr
    envelope = read_json(run / "capture" / "result.json")
    assert envelope["status"] == "partial"
    assert envelope["anomalies"][0]["code"] == "detail_newer_than_listing"
    # The necessary input is still readable in the run directory (never lost).
    snapshot = read_json(run / "capture" / "snapshot.json")
    raw_ref = snapshot["exports"]["2"]["raw"]["path"]
    assert (run / "capture" / raw_ref).is_file()
    canonical = read_json(workspace.corpus / ".sync-state.json")
    documents = canonical["books"]["fund"]["documents"]
    assert documents["1"]["steps"]["download"]["status"] == "completed"
    assert documents["2"]["steps"]["download"]["status"] == "completed"


def test_batch_cause_enumerates_affected_images(tmp_path: Path):
    """The shared token/quota contract ticket 05 consumes (no OCR stage yet)."""
    from types import SimpleNamespace

    from dogpdkb.sync_anomalies import scan_view
    from dogpdkb.sync_results import archive_anomalies, load_registry

    config = SimpleNamespace(results=tmp_path / "results")
    context = SimpleNamespace(
        config=config, book=SimpleNamespace(key="fund"), layout=None
    )
    version = {"field": "content_updated_at", "utc": "2026-11-10T11:00:00+08:00"}
    anomalies = [
        {
            "stage": "ocr",
            "document_id": 2,
            "source_version": version,
            "code": "ocr_unattempted",
            "resource_id": f"img-{index}",
            "image": {"url": f"https://example.invalid/{index}.png"},
        }
        for index in (1, 2)
    ]
    cause = {"stage": "ocr", "code": "token_invalid", "reason": "OCR token rejected"}

    archived = archive_anomalies(context, anomalies, run_id="run-x", batch_cause=cause)

    registry = load_registry(config, "fund")
    assert len(registry["batch_causes"]) == 1
    record = next(iter(registry["batch_causes"].values()))
    assert record["code"] == "token_invalid"
    assert record["reason"] == "OCR token rejected"
    assert record["anomaly_ids"] == [row["id"] for row in archived]
    assert all(row["batch_cause"] == record["id"] for row in archived)
    # The affected images are enumerable through the scan the agent reads.
    view = scan_view(config, "fund")
    assert view["counts"]["batch_causes"] == 1
    assert view["batch_causes"][0]["anomaly_ids"] == record["anomaly_ids"]

    # Re-archiving the same batch cause never duplicates it.
    again = archive_anomalies(context, anomalies, run_id="run-x", batch_cause=cause)
    registry = load_registry(config, "fund")
    assert len(registry["batch_causes"]) == 1
    assert registry["batch_causes"][record["id"]]["anomaly_ids"] == [
        row["id"] for row in again
    ]


def test_book_keyed_namespaces_distinguish_trailing_hyphen_keys(tmp_path: Path):
    """Validator-legal keys differing only by a trailing hyphen must not collide."""
    import re
    from types import SimpleNamespace

    from dogpdkb.ocr_cache import cache_root
    from dogpdkb.sync_results import anomaly_id, followup_id, results_root

    config = SimpleNamespace(
        results=tmp_path / "results", ocr_cache=tmp_path / "ocr-cache"
    )
    version = {"field": "content_updated_at", "utc": "2026-11-10T11:00:00+08:00"}
    keys = ("fund", "fund-")
    # Both keys are accepted by the books-file validator (mdocr/config.py).
    assert all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", key) for key in keys)

    roots = {results_root(config, key) for key in keys}
    aids = {anomaly_id(key, 2, version, "capture", "export_failed") for key in keys}
    fids = {followup_id(key, 2, version) for key in keys}
    caches = {cache_root(config, key) for key in keys}
    assert len(roots) == 2, roots
    assert len(aids) == 2, aids
    assert len(fids) == 2, fids
    assert len(caches) == 2, caches
