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

from tests.test_unified_sync import DOCUMENTS, Workspace, read_json

PLAN_SCHEMA = "unified-plan-1.0"
MDCOMPARE_SCHEMA = "unified-mdcompare-1.0"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, DOCUMENTS)


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