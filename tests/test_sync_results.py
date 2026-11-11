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
