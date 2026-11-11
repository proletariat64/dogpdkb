"""Persistent batch results, anomaly handoff and disposition via the installed CLI.

Black-box seam: ``dogpdkb sync`` (capture archiving) and ``dogpdkb anomalies``
(read + disposition). Temporary corpora and the existing local Yuque double only;
no real Yuque/OCR/Graphify/network/paid calls.
"""

from __future__ import annotations

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
