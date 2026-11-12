"""Ticket 08 acceptance: whole-batch order, no-update and body-reuse convergence.

These close the Testing-Decisions gaps not already locked by a per-ticket
test: (1) a *successful* full batch runs all five stages in whole-batch order
(all capture -> compare -> plan -> OCR -> compile), (2) a second batch with
an unchanged remote completes by reusing recorded work instead of repeating
downloads, OCR submissions or extraction, and (3) a verified body reuse keeps
its canonical input reference resolvable so the *next* batch still converges
without recompiling. All drive the installed ``dogpdkb sync`` black box with a
temporary corpus and the deterministic Graphify / OCR doubles; no real Yuque /
OCR / Graphify / network / paid call is made.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

from tests.test_unified_compile import CompileWorkspace
from tests.test_unified_sync import STAGES, read_json, write_json

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
    # Settled documents converge to no-work: the transient COMPARISON_PENDING is
    # cleared once a terminal decision is recorded (M16), so an unchanged batch
    # neither re-selects nor re-compares them, and a no-work document is never an
    # anomaly (H7). The listing still observes both documents.
    capture = read_json(run2 / "capture" / "result.json")
    assert capture["counts"]["failed"] == 0
    assert capture["counts"]["documents"] == 2
    snapshot = read_json(run2 / "capture" / "snapshot.json")
    assert snapshot["selected"] == []
    plan = read_json(run2 / "plan" / "plan.json")
    assert plan["decisions"]["1"]["decision"] == "no_work"
    assert plan["decisions"]["2"]["decision"] == "no_work"
    assert plan["compile"] == []
    assert plan["counts"]["anomalies"] == 0
    assert read_json(run2 / "ocr" / "result.json")["status"] == "skipped"
    assert read_json(run2 / "compile" / "result.json")["status"] == "skipped"

    # No repeat extraction across the no-update batch (no re-billing).
    second_extracts = [
        row for row in workspace.graphify_calls() if row["event"] == "extract"
    ]
    assert len(second_extracts) == len(first_extracts)


def _canonical_raw(workspace: CompileWorkspace, doc_id: str) -> Path:
    """Resolve a canonical graph raw reference the way the next batch will."""
    state = read_json(workspace.corpus / ".sync-state.json")
    reference = state["books"]["fund"]["documents"][doc_id]["graph"]["raw"]
    assert reference["stage"] in {"raw_export", "historical_cleaned"}
    path = Path(reference["path"])
    return path if path.is_absolute() else workspace.corpus / path


def test_body_reuse_survives_the_next_batch_without_recompiling(
    workspace: CompileWorkspace,
):
    # Doc One's remote body equals the registered corpus baseline: the plan
    # must reuse the existing body knowledge, not recompile it.
    remote = read_json(workspace.root / "remote.json")
    remote["details"]["1"]["body"] = "# One\n\nOld rule.\n"
    write_json(workspace.root / "remote.json", remote)

    first = workspace.sync()
    assert first.returncode == 0, first.stderr
    assert _summary(first)["status"] == "completed"
    plan1 = read_json(workspace.runs()[-1] / "plan" / "plan.json")
    assert {row["document_id"] for row in plan1["body_reuse"]} == {1}
    assert {row["document_id"] for row in plan1["compile"]} == {2}
    first_extracts = [
        row for row in workspace.graphify_calls() if row["event"] == "extract"
    ]
    assert len(first_extracts) == 1  # only the new document was extracted

    # The reuse record must keep a canonical input reference that resolves
    # from the corpus state, not one relative to the mdcompare report.
    state = read_json(workspace.corpus / ".sync-state.json")
    graph = state["books"]["fund"]["documents"]["1"]["graph"]
    assert graph["status"] == "covered"
    assert graph["coverage"] == "body_reuse"
    assert _canonical_raw(workspace, "1").is_file()

    # The next daily batch with the same remote must still converge: the
    # reused body is verified again but never recompiled or re-extracted.
    second = workspace.sync()
    assert second.returncode == 0, second.stderr
    summary = _summary(second)
    assert summary["status"] == "completed"
    assert summary["stages"]["compile"] == "skipped"
    plan2 = read_json(workspace.runs()[-1] / "plan" / "plan.json")
    assert plan2["compile"] == []
    second_extracts = [
        row for row in workspace.graphify_calls() if row["event"] == "extract"
    ]
    assert len(second_extracts) == len(first_extracts)

    # Wash cleanup may retire the first run directory; the canonical reference
    # must still resolve (migrated to the durable store) after the batch.
    assert _canonical_raw(workspace, "1").is_file()
