"""--max-select: bounded capture selection for affordable smoke batches.

Drives the installed ``dogpdkb sync`` black box with the deterministic Graphify
stub and OCR double; no real Yuque / OCR / Graphify / network / paid call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_unified_compile import CompileWorkspace
from tests.test_unified_sync import read_json

DOCUMENTS = [
    {
        "id": 1,
        "title": "One",
        "slug": "one",
        "content_updated_at": "2026-11-10T10:00:00+08:00",
    },
    *[
        {
            "id": i,
            "title": f"Doc{i}",
            "slug": f"doc{i}",
            "content_updated_at": f"2026-11-{10 + i:02d}T10:00:00+08:00",
        }
        for i in range(2, 6)
    ],
]


@pytest.fixture
def workspace(tmp_path: Path) -> CompileWorkspace:
    return CompileWorkspace(tmp_path, DOCUMENTS)


def _snapshot(workspace: CompileWorkspace) -> dict:
    return read_json(workspace.only_run() / "capture" / "snapshot.json")


def _capture_log(workspace: CompileWorkspace) -> str:
    return (workspace.only_run() / "logs" / "capture.log").read_text(encoding="utf-8")


def test_cap_exports_only_the_newest_n_and_records_the_audit(
    workspace: CompileWorkspace,
):
    completed = workspace.sync("--max-select", "2", "--stop-after", "capture")
    assert completed.returncode == 0, completed.stderr
    assert "selection capped: 5 -> 2 (max-select 2)" in _capture_log(workspace)

    snapshot = _snapshot(workspace)
    assert snapshot["selected"] == [4, 5]
    assert snapshot["selection_limit"] == 2
    assert snapshot["selected_before_limit"] == 5
    assert snapshot["counts"]["selected"] == 2
    assert set(snapshot["exports"]) == {"4", "5"}
    # The observation still describes the whole book; only the selection is capped.
    assert snapshot["counts"]["documents"] == 5
    assert sorted(snapshot["changes"]["new"]) == [2, 3, 4, 5]


def test_resume_keeps_the_capped_scope_without_re_export(
    workspace: CompileWorkspace,
):
    paused = workspace.sync("--max-select", "2", "--stop-after", "capture")
    assert paused.returncode == 0, paused.stderr
    run_id = workspace.only_run().name

    resumed = workspace.sync("--resume", run_id)
    assert resumed.returncode == 0, resumed.stderr
    summary = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert summary["status"] == "completed"

    snapshot = _snapshot(workspace)
    assert snapshot["selected"] == [4, 5]
    assert set(snapshot["exports"]) == {"4", "5"}
    documents = workspace.state()["books"]["fund"]["documents"]
    assert documents["4"]["corpus"]["status"] == "captured"
    assert documents["5"]["corpus"]["status"] == "captured"
    assert documents["2"]["corpus"]["status"] == "not_captured"
    assert documents["3"]["corpus"]["status"] == "not_captured"


def test_resume_rejects_a_changed_cap(workspace: CompileWorkspace):
    paused = workspace.sync("--max-select", "2", "--stop-after", "capture")
    assert paused.returncode == 0, paused.stderr
    run_id = workspace.only_run().name

    rejected = workspace.sync("--resume", run_id, "--max-select", "3")
    assert rejected.returncode == 1
    assert "max_select" in rejected.stderr


def test_without_a_cap_the_selection_is_unbounded(workspace: CompileWorkspace):
    completed = workspace.sync("--stop-after", "capture")
    assert completed.returncode == 0, completed.stderr
    snapshot = _snapshot(workspace)
    assert snapshot["selected"] == [1, 2, 3, 4, 5]
    assert snapshot["selection_limit"] is None
    assert snapshot["selected_before_limit"] == 5


def test_a_cap_above_the_selection_is_a_noop(workspace: CompileWorkspace):
    completed = workspace.sync("--max-select", "99", "--stop-after", "capture")
    assert completed.returncode == 0, completed.stderr
    assert "selection capped" not in _capture_log(workspace)
    snapshot = _snapshot(workspace)
    assert snapshot["selected"] == [1, 2, 3, 4, 5]
    assert snapshot["selection_limit"] == 99
    assert snapshot["selected_before_limit"] == 5


def test_a_non_positive_cap_is_rejected(workspace: CompileWorkspace):
    rejected = workspace.sync("--max-select", "0")
    assert rejected.returncode != 0
    assert "positive integer" in rejected.stderr