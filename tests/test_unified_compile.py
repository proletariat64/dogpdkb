"""Unified compile stage: whole-source update, partial publication, recovery.

Ticket 06. Drives the installed ``dogpdkb sync`` black box with a temporary
corpus and the existing deterministic Graphify stub. Ticket 05 owns the real OCR
handler; these tests register an OCR *double* through ``sitecustomize`` that only
produces the published OCR-result-reference contract, so compile is exercised
without coupling to 05's code. No real Graphify / OCR / network / paid call.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_knowledge_execute import install_graphify_stub
from tests.test_unified_sync import DOCUMENTS, Workspace, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
COMPILE_SCHEMA = "unified-stage-1.0"

# An OCR double registered at interpreter startup. It consumes the plan's OCR
# work list and publishes the ``prepared-jpeg-ocr-v1`` evidence contract that the
# compile stage reads; with no OCR work it skips. This is a test double only.
OCR_DOUBLE = '''
import os
if os.environ.get("DOGPDKB_OCR_DOUBLE") == "1":
    from dogpdkb.sync_stages import register_handler, StageResult
    from dogpdkb.sync_run import read_json

    def _ocr_double(context):
        plan = read_json(context.require("plan", "plan"))
        items = plan.get("ocr") or []
        if not items:
            return StageResult(
                status="skipped",
                counts={"total": 0, "completed": 0, "reused": 0, "failed": 0},
            )
        return StageResult(
            status="completed",
            counts={
                "total": len(items),
                "completed": len(items),
                "reused": 0,
                "failed": 0,
            },
        )

    register_handler("ocr", _ocr_double)
'''


class CompileWorkspace(Workspace):
    """The unified runner wired to the Graphify stub and an OCR double."""

    def __init__(self, tmp_path: Path, documents: list[dict[str, Any]] | None = None):
        super().__init__(tmp_path, DOCUMENTS if documents is None else documents)
        self.stub = install_graphify_stub(tmp_path)
        self.shim = tmp_path / "ocr-double"
        self.shim.mkdir()
        (self.shim / "sitecustomize.py").write_text(OCR_DOUBLE, encoding="utf-8")
        self.env["PYTHONPATH"] = os.pathsep.join(
            [str(self.shim), str(self.stub), str(ROOT / "src")]
        )
        self.env["DOGPDKB_OCR_DOUBLE"] = "1"
        self.graphify_log = tmp_path / "graphify-calls.jsonl"
        self.env["GRAPHIFY_STUB_LOG"] = str(self.graphify_log)

    def graph(self) -> dict[str, Any]:
        return read_json(self.corpus / "graphify-out" / "graph.json")

    def state(self) -> dict[str, Any]:
        return read_json(self.corpus / ".sync-state.json")

    def compile_envelope(self) -> dict[str, Any]:
        return read_json(self.only_run() / "compile" / "result.json")

    def graphify_calls(self) -> list[dict[str, Any]]:
        if not self.graphify_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.graphify_log.read_text(encoding="utf-8").splitlines()
        ]


@pytest.fixture
def workspace(tmp_path: Path) -> CompileWorkspace:
    return CompileWorkspace(tmp_path)


def test_compile_publishes_whole_sources_and_advances_the_success_version(
    workspace: CompileWorkspace,
):
    completed = workspace.sync()

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["stages"]["compile"] == "completed"

    envelope = workspace.compile_envelope()
    assert envelope["schema_version"] == COMPILE_SCHEMA
    assert envelope["status"] == "completed"

    # Graphify updated by COMPLETE source: the new document is published as a
    # whole source and the old filename for document one is replaced, not patched.
    graph = workspace.graph()
    sources = {node["source_file"] for node in graph["nodes"]}
    assert "source-[2].md" in sources
    assert "One-2026-11-10-[1].md" not in sources

    # The success version advances only after a real successful write.
    state = workspace.state()
    doc2 = state["books"]["fund"]["documents"]["2"]
    assert doc2["graph"]["status"] == "covered"
    assert doc2["graph"]["version"]["utc"] == "2026-11-10T11:00:00+08:00"


def test_partial_publication_keeps_failed_source_knowledge_and_version(
    workspace: CompileWorkspace,
):
    # Document one fails extraction this run; document two compiles successfully.
    workspace.env["GRAPHIFY_STUB_PARTIAL"] = "source-[1]"

    completed = workspace.sync()

    # A partial batch still ends normally (exit 0) and hands off its usable set.
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["status"] == "partial"
    assert summary["stages"]["compile"] == "partial"
    envelope = workspace.compile_envelope()
    assert envelope["status"] == "partial"
    assert [row["document_id"] for row in envelope["anomalies"]] == [1]

    graph = workspace.graph()
    sources = {node["source_file"] for node in graph["nodes"]}
    assert "source-[2].md" in sources  # successful source published whole
    assert "One-2026-11-10-[1].md" in sources  # failed source keeps old knowledge
    assert "source-[1].md" not in sources  # failed source is not cleared/replaced

    state = workspace.state()
    doc1 = state["books"]["fund"]["documents"]["1"]
    # The failed source keeps its old success version (here: the unversioned
    # historical baseline); it must not advance to the newly observed version.
    assert doc1["graph"]["version"] is None
    assert doc1["graph"]["coverage"] == "registered_historical_baseline"
    doc2 = state["books"]["fund"]["documents"]["2"]
    assert doc2["graph"]["status"] == "covered"
    assert doc2["graph"]["version"]["utc"] == "2026-11-10T11:00:00+08:00"


def _bump_remote_version(workspace: CompileWorkspace, doc_id: int, version: str) -> None:
    remote = read_json(workspace.root / "remote.json")
    for row in remote["documents"]:
        if row["id"] == doc_id:
            row["content_updated_at"] = version
    remote["details"][str(doc_id)]["content_updated_at"] = version
    remote["details"][str(doc_id)]["body"] = f"body {doc_id} revised"
    write_json(workspace.root / "remote.json", remote)


def test_same_version_compile_failure_is_not_retried_the_next_day(
    workspace: CompileWorkspace,
):
    workspace.env["GRAPHIFY_STUB_PARTIAL"] = "source-[1]"
    first = workspace.sync()
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout.strip().splitlines()[-1])["status"] == "partial"

    # Even though extraction would now succeed, the same archived version is
    # handed off to the post-hoc agent and must not be re-attempted daily.
    workspace.env["GRAPHIFY_STUB_PARTIAL"] = ""
    second = workspace.sync()

    assert second.returncode == 0, second.stderr
    plan = read_json(workspace.runs()[-1] / "plan" / "plan.json")
    assert plan["decisions"]["1"]["decision"] == "suppressed"
    assert [item["document_id"] for item in plan["compile"]] == []
    envelope = read_json(workspace.runs()[-1] / "compile" / "result.json")
    assert envelope["status"] == "skipped"
    # No second extraction attempt for the suppressed source.
    extracts = [
        row["file"]
        for row in workspace.graphify_calls()
        if row["event"] == "extract"
    ]
    assert "source-[1].md" not in extracts


def test_a_new_source_version_is_compiled_normally_after_a_failure(
    workspace: CompileWorkspace,
):
    workspace.env["GRAPHIFY_STUB_PARTIAL"] = "source-[1]"
    first = workspace.sync()
    assert first.returncode == 0, first.stderr

    # A genuinely new version returns the source to normal daily processing.
    workspace.env["GRAPHIFY_STUB_PARTIAL"] = ""
    _bump_remote_version(workspace, 1, "2026-11-11T10:00:00+08:00")
    second = workspace.sync()

    assert second.returncode == 0, second.stderr
    plan = read_json(workspace.runs()[-1] / "plan" / "plan.json")
    assert plan["decisions"]["1"]["decision"] == "compile"
    graph = workspace.graph()
    sources = {node["source_file"] for node in graph["nodes"]}
    assert "source-[1].md" in sources
    state = workspace.state()
    doc1 = state["books"]["fund"]["documents"]["1"]
    assert doc1["graph"]["status"] == "covered"
    assert doc1["graph"]["version"]["utc"] == "2026-11-11T10:00:00+08:00"


def test_graphify_output_streams_to_the_stage_log_not_the_console(
    workspace: CompileWorkspace,
):
    completed = workspace.sync()

    assert completed.returncode == 0, completed.stderr
    log = (workspace.only_run() / "logs" / "compile.log").read_text(encoding="utf-8")
    # Graphify's own diagnostics are streamed into the stage log...
    assert "Graphify semantic extraction" in log
    # ...and never reach the console (stdout is the single summary line).
    assert "Graphify semantic extraction" not in completed.stderr
    assert "Graphify semantic extraction" not in completed.stdout
    assert len(completed.stdout.strip().splitlines()) == 1
    # The default log carries no document body and no token/auth material.
    for text in (log, completed.stdout, completed.stderr):
        assert "body 1" not in text
        assert "body 2" not in text
        assert "Old rule." not in text