"""Ticket 08 acceptance: whole-batch stage order and no-update convergence.

These close the two Testing-Decisions gaps not already locked by a per-ticket
test: (1) a *successful* full batch runs all five stages in whole-batch order
(all capture -> compare -> plan -> OCR -> compile), and (2) a second batch with
an unchanged remote completes by reusing recorded work instead of repeating
downloads, OCR submissions or extraction. Both drive the installed ``dogpdkb
sync`` black box with a temporary corpus and the deterministic Graphify / OCR
doubles; no real Yuque / OCR / Graphify / network / paid call is made.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

from tests.test_unified_compile import CompileWorkspace
from tests.test_unified_sync import STAGES, read_json

PROGRESS = re.compile(
    r"\]\s+(capture|mdcompare|plan|ocr|compile):\s+"
    r"(started|completed|skipped|partial|failed)\b"
)


@pytest.fixture
def workspace(tmp_path: Path) -> CompileWorkspace:
    return CompileWorkspace(tmp_path)


def _summary(completed: object) -> dict:
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_full_batch_runs_all_five_stages_in_whole_batch_order(
    workspace: CompileWorkspace,
):
    completed = workspace.sync()
    assert completed.returncode == 0, completed.stderr
    summary = _summary(completed)
    assert summary["status"] == "completed"
    for stage in STAGES:
        assert summary["stages"][stage] in {"completed", "skipped"}

    # Observable progress stream: every stage starts exactly once, in order, and
    # each stage ends before the next one starts (no interleaved partial batches).
    events = PROGRESS.findall(completed.stderr)
    started = [stage for stage, kind in events if kind == "started"]
    assert started == list(STAGES)
    start_at = {stage: i for i, (stage, kind) in enumerate(events) if kind == "started"}
    end_at = {stage: i for i, (stage, kind) in enumerate(events) if kind != "started"}
    for previous, following in itertools.pairwise(STAGES):
        assert end_at[previous] < start_at[following]

    # Independent observable: the durable run state stamps the same order.
    states = read_json(workspace.only_run() / "run.json")["stage_states"]
    previous_end = None
    for stage in STAGES:
        row = states[stage]
        assert row["status"] in {"completed", "skipped"}
        if previous_end is not None:
            assert row["started_at"] >= previous_end
        previous_end = row["ended_at"]


def test_no_update_second_batch_completes_by_reusing_recorded_work(
    workspace: CompileWorkspace,
):
    first = workspace.sync()
    assert first.returncode == 0, first.stderr
    assert _summary(first)["status"] == "completed"
    first_extracts = [
        row for row in workspace.graphify_calls() if row["event"] == "extract"
    ]
    assert first_extracts  # batch one really compiled the new sources

    # The remote is unchanged: a second daily batch must converge, not redo work.
    second = workspace.sync()
    assert second.returncode == 0, second.stderr
    summary = _summary(second)
    assert summary["status"] == "completed"
    assert summary["stages"]["compile"] == "skipped"
    assert summary["stages"]["ocr"] == "skipped"

    run2 = workspace.runs()[-1]
    capture = read_json(run2 / "capture" / "result.json")
    assert capture["counts"]["reused"] == 2
    assert capture["counts"]["failed"] == 0
    # Both raws are reused from the prior batch: no re-download of unchanged docs.
    snapshot = read_json(run2 / "capture" / "snapshot.json")
    assert all(row["reused"] for row in snapshot["exports"].values())
    plan = read_json(run2 / "plan" / "plan.json")
    assert plan["compile"] == []
    assert read_json(run2 / "ocr" / "result.json")["status"] == "skipped"
    assert read_json(run2 / "compile" / "result.json")["status"] == "skipped"

    # No repeat extraction across the no-update batch (no re-billing).
    second_extracts = [
        row for row in workspace.graphify_calls() if row["event"] == "extract"
    ]
    assert len(second_extracts) == len(first_extracts)
