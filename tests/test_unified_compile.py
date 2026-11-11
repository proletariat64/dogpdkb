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
OCR_DOUBLE = """
import os
if os.environ.get("DOGPDKB_OCR_DOUBLE") == "1":
    from dogpdkb.sync_stages import register_handler, StageResult
    from dogpdkb.sync_run import read_json
    from dogpdkb.knowledge_plan import _load_state, state_lock
    from dogpdkb.knowledge_state import save_view
    from mdocr.ocr import build_ocr_block

    def _ocr_double(context):
        plan = read_json(context.require("plan", "plan"))
        items = plan.get("ocr") or []
        if not items:
            return StageResult(
                status="skipped",
                counts={"total": 0, "completed": 0, "reused": 0, "failed": 0},
            )
        fail = set(
            filter(None, os.environ.get("DOGPDKB_OCR_DOUBLE_FAIL", "").split(","))
        )
        cache = context.corpus.parent / "ocr-cache"
        completed = failed = 0
        evidence_by_id = {}
        for item in items:
            identity = str(item["document_id"])
            images = []
            for resource in item.get("resources") or []:
                rid = resource["resource_id"]
                slug = rid.split("://")[-1].replace("/", "_")
                doc_dir = cache / identity
                doc_dir.mkdir(parents=True, exist_ok=True)
                prepared = doc_dir / (slug + ".jpg")
                prepared.write_bytes(b"prepared-jpeg-double-bytes")
                artifact = doc_dir / (slug + ".md")
                record = {
                    "resource_id": rid,
                    "source_path": str(prepared),
                    "prepared_image": str(prepared),
                    "artifact": str(artifact),
                }
                if rid in fail:
                    record["status"] = "failed"
                    failed += 1
                else:
                    artifact.write_text(
                        build_ocr_block(prepared.name, "OCR TEXT " + rid),
                        encoding="utf-8",
                    )
                    record["status"] = "success"
                    completed += 1
                images.append(record)
            evidence_by_id[identity] = {
                "contract": "prepared-jpeg-ocr-v1",
                "preprocessing": {"name": "mdocr", "version": "1"},
                "configuration": {"engine": "double"},
                "images": images,
            }
        state_path = context.corpus / ".sync-state.json"
        with state_lock(state_path):
            state = _load_state(state_path, plan["book"])
            for identity, evidence in evidence_by_id.items():
                record = state["documents"].setdefault(
                    identity,
                    {
                        "observation": None,
                        "corpus": {
                            "status": "not_captured",
                            "version": None,
                            "raw": None,
                        },
                        "graph": {},
                        "pending": [],
                    },
                )
                record.setdefault("graph", {})["ocr"] = evidence
            save_view(state_path, state)
        return StageResult(
            status="completed",
            counts={
                "total": completed + failed,
                "completed": completed,
                "reused": 0,
                "failed": failed,
            },
        )

    register_handler("ocr", _ocr_double)
"""


# Inject a fault on the canonical state write *after* compile has really published
# its graph, mirroring the legacy publication-recovery regression. The compile
# graph result lives under the run's staging dir, which is only known once the run
# exists, so the guard globs for it rather than naming a fixed path.
COMPILE_STATE_FAILURE = """
import glob
import os
from pathlib import Path
_replace = os.replace
def guarded_replace(source, destination):
    if (
        os.environ.get("DOGPDKB_COMPILE_STATE_FAIL") == "1"
        and Path(destination).name == ".sync-state.json"
    ):
        book_staging = os.environ.get("DOGPDKB_BOOK_STAGING")
        if book_staging and glob.glob(
            os.path.join(book_staging, "run-*", "compile", "graph-update", "result.json")
        ):
            raise OSError("injected state failure after compile publish")
    return _replace(source, destination)
os.replace = guarded_replace
"""


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
    # Item 9: a partial batch still finishes and releases the canonical claim, so
    # the next cron run is not blocked by a residual active run.
    assert state["active_run"] is None


def _set_detail(workspace: CompileWorkspace, doc_id: int, **fields: Any) -> None:
    remote = read_json(workspace.root / "remote.json")
    remote["details"][str(doc_id)].update(fields)
    write_json(workspace.root / "remote.json", remote)


def _set_title(workspace: CompileWorkspace, doc_id: int, title: str) -> None:
    remote = read_json(workspace.root / "remote.json")
    for row in remote["documents"]:
        if row["id"] == doc_id:
            row["title"] = title
    write_json(workspace.root / "remote.json", remote)


def _bump_remote_version(
    workspace: CompileWorkspace, doc_id: int, version: str
) -> None:
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
        row["file"] for row in workspace.graphify_calls() if row["event"] == "extract"
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


def test_fault_between_publish_and_state_record_recovers_without_rebilling(
    workspace: CompileWorkspace,
):
    # Only one sitecustomize loads, so fold the failure guard into the OCR double's
    # shim rather than shadowing it with a second one.
    shim_file = workspace.shim / "sitecustomize.py"
    shim_file.write_text(
        shim_file.read_text(encoding="utf-8") + "\n" + COMPILE_STATE_FAILURE,
        encoding="utf-8",
    )
    workspace.env["DOGPDKB_COMPILE_STATE_FAIL"] = "1"
    workspace.env["DOGPDKB_BOOK_STAGING"] = str(workspace.staging / "fund")

    failed = workspace.sync()

    assert failed.returncode != 0
    assert "injected state failure" in failed.stderr
    run_dir = workspace.only_run()
    # The graph was really published (the commit happened)...
    graph_path = workspace.corpus / "graphify-out" / "graph.json"
    published = graph_path.read_bytes()
    assert "source-[2].md" in {
        node["source_file"] for node in workspace.graph()["nodes"]
    }
    assert (run_dir / "compile" / "graph-update" / "result.json").is_file()
    # ...but the success version did NOT advance and no compile result was recorded.
    doc2 = workspace.state()["books"]["fund"]["documents"]["2"]
    assert doc2["graph"].get("version") is None
    assert not (run_dir / "compile" / "compile-result.json").exists()
    first_calls = workspace.graphify_calls()
    assert sum(row["event"] == "extract" for row in first_calls) == 2

    workspace.env["DOGPDKB_COMPILE_STATE_FAIL"] = "0"
    resumed = workspace.sync("--resume", run_dir.name)

    assert resumed.returncode == 0, resumed.stderr
    # Recovery reuses the retained candidate: no re-extraction, no re-billing.
    assert workspace.graphify_calls() == first_calls
    # Publication is idempotent: the graph is byte-identical to the first commit.
    assert graph_path.read_bytes() == published
    # The success version advances only now, after the real successful write.
    doc2 = workspace.state()["books"]["fund"]["documents"]["2"]
    assert doc2["graph"]["status"] == "covered"
    assert doc2["graph"]["version"]["utc"] == "2026-11-10T11:00:00+08:00"
    assert (run_dir / "compile" / "compile-result.json").is_file()
    assert workspace.runs() == [run_dir]


def test_invalid_candidate_structure_does_not_advance_the_success_version(
    workspace: CompileWorkspace,
):
    graph_path = workspace.corpus / "graphify-out" / "graph.json"
    before = graph_path.read_bytes()
    # A structurally invalid candidate (dangling edge) must be rejected by the
    # artifact check before publication, so neither the graph nor the success
    # version moves.
    workspace.env["GRAPHIFY_STUB_DANGLING"] = "source-[2]"

    failed = workspace.sync()

    assert failed.returncode != 0
    assert graph_path.read_bytes() == before
    sources = {node["source_file"] for node in workspace.graph()["nodes"]}
    assert "source-[1].md" not in sources
    assert "source-[2].md" not in sources
    documents = workspace.state()["books"]["fund"]["documents"]
    assert documents["1"]["graph"].get("version") is None
    assert documents["2"]["graph"].get("version") is None
    run_dir = workspace.only_run()
    assert not (run_dir / "compile" / "compile-result.json").exists()


def test_compile_executes_planned_metadata_maintenance_for_body_reuse(
    workspace: CompileWorkspace,
):
    # Doc One's body matches its historical baseline, so it is reused rather than
    # recompiled. Its incoming source_ref still differs from the unversioned
    # baseline, so the plan schedules metadata maintenance. The compile stage is
    # the executor: it must apply and record that maintenance even though Doc One
    # is not in the compile list.
    _set_detail(workspace, 1, body="# One\n\nOld rule.\n")

    completed = workspace.sync()

    assert completed.returncode == 0, completed.stderr
    run_dir = workspace.only_run()
    plan = read_json(run_dir / "plan" / "plan.json")
    assert 1 in {row["document_id"] for row in plan["body_reuse"]}
    assert 1 not in {row["document_id"] for row in plan["compile"]}
    assert 1 in {row["document_id"] for row in plan["metadata"]}

    report = read_json(run_dir / "compile" / "compile-result.json")
    executed = {row["document_id"]: row for row in report["metadata"]}
    assert 1 in executed
    assert executed[1]["status"] == "executed"
    assert any(task["field"] == "source_ref" for task in executed[1]["tasks"])

    doc1 = workspace.state()["books"]["fund"]["documents"]["1"]
    assert doc1["graph"]["metadata"]["name"] == "One"
    assert doc1["graph"]["metadata"]["source_ref"] == "123:1"
    assert doc1["steps"]["metadata_maintenance"]["status"] == "completed"


IMAGE_A = "https://cdn.example.invalid/a.png"
IMAGE_B = "https://cdn.example.invalid/b.png"


def test_compile_enriches_body_with_successful_ocr_and_records_final_inputs(
    workspace: CompileWorkspace,
):
    # Doc Two's prepared body carries an image. The OCR double publishes a
    # successful prepared-jpeg-ocr-v1 artifact for it; compile must freeze the
    # enriched body (OCR block inserted), declare the generated region, and
    # record the actual final inputs it used.
    _set_detail(workspace, 2, body=f"# Two\n\n![diagram]({IMAGE_A})\n")

    completed = workspace.sync()

    assert completed.returncode == 0, completed.stderr
    run_dir = workspace.only_run()
    report = read_json(run_dir / "compile" / "compile-result.json")
    result2 = next(row for row in report["results"] if row["document_id"] == 2)
    execution = result2["execution"]
    final = execution["final_inputs"]
    assert any(item["kind"] == "raw" for item in final)
    assert any(
        item["kind"] == "ocr_artifact" and item["resource_id"] == IMAGE_A
        for item in final
    )
    assert any(
        item["kind"] == "prepared_image" and item["resource_id"] == IMAGE_A
        for item in final
    )
    assert execution["missing_items"] == []
    assert execution["status"] == "completed"

    # The frozen compile source is the enriched whole source, not the raw body.
    frozen = run_dir / "compile" / "execution-corpus" / "source-[2].md"
    text = frozen.read_text(encoding="utf-8")
    assert "mdocr:ocr-begin" in text
    assert "OCR TEXT " + IMAGE_A in text

    # The generated OCR region is declared so the next batch can reuse the body
    # independently of OCR.
    doc2 = workspace.state()["books"]["fund"]["documents"]["2"]
    regions = doc2["graph"]["body_scope"]["regions"]
    assert doc2["graph"]["body_scope"]["version"] == "generated-regions-v1"
    assert len(regions) == 1
    assert regions[0]["kind"] == "mdocr_ocr"
    # The successful OCR evidence persists in canonical state for next-batch reuse.
    assert doc2["graph"]["ocr"]["contract"] == "prepared-jpeg-ocr-v1"


def test_compile_keeps_unrecognized_image_and_records_missing_items(
    workspace: CompileWorkspace,
):
    # Two images: OCR succeeds for A and fails for B. Incomplete OCR must not
    # block the body compile; B's original reference is kept and recorded as a
    # missing item rather than disguised as a completed OCR input.
    _set_detail(workspace, 2, body=f"![a]({IMAGE_A})\n\n![b]({IMAGE_B})\n")
    workspace.env["DOGPDKB_OCR_DOUBLE_FAIL"] = IMAGE_B

    completed = workspace.sync()

    assert completed.returncode == 0, completed.stderr
    run_dir = workspace.only_run()
    report = read_json(run_dir / "compile" / "compile-result.json")
    result2 = next(row for row in report["results"] if row["document_id"] == 2)
    execution = result2["execution"]
    assert any(
        item["kind"] == "ocr_artifact" and item["resource_id"] == IMAGE_A
        for item in execution["final_inputs"]
    )
    missing = execution["missing_items"]
    assert any(item["resource_id"] == IMAGE_B for item in missing)
    assert not any(item["resource_id"] == IMAGE_A for item in missing)

    frozen = run_dir / "compile" / "execution-corpus" / "source-[2].md"
    text = frozen.read_text(encoding="utf-8")
    # The recognized image became an OCR block; the unrecognized one keeps its
    # original reference verbatim.
    assert "OCR TEXT " + IMAGE_A in text
    assert f"![b]({IMAGE_B})" in text
    # Only the inserted block is declared as a generated region.
    doc2 = workspace.state()["books"]["fund"]["documents"]["2"]
    assert len(doc2["graph"]["body_scope"]["regions"]) == 1


def test_compile_consumes_pending_posthoc_followup_for_suppressed_version(
    workspace: CompileWorkspace,
):
    # Doc Two's first compile fails, archiving a same-version anomaly the daily
    # batch suppresses. A post-hoc OCR backfill that changed the published input
    # registers followup compile work (03 contract). The compile stage must consume
    # it: recompile the suppressed version with the updated OCR and mark the
    # followup done only after real publication, never just because OCR was cached.
    from dogpdkb.sync_results import load_followups, register_compile_followup
    from mdocr.config import load_config

    _set_detail(workspace, 2, body=f"# Two\n\n![diagram]({IMAGE_A})\n")
    workspace.env["GRAPHIFY_STUB_PARTIAL"] = "source-[2]"
    first = workspace.sync()
    assert first.returncode == 0, first.stderr

    config = load_config(workspace.config)
    doc2 = workspace.state()["books"]["fund"]["documents"]["2"]
    version = doc2["observation"]["version"]
    artifact = doc2["graph"]["ocr"]["images"][0]["artifact"]
    followup = register_compile_followup(
        config,
        "fund",
        document_id=2,
        source_version=version,
        reason="post-hoc OCR backfill changed the published compile input",
        inputs=[{"kind": "cache", "path": artifact}],
    )
    assert followup["status"] == "pending"

    # The next daily batch would suppress this version, but the followup is real
    # pending compile work the stage must still carry out.
    workspace.env["GRAPHIFY_STUB_PARTIAL"] = ""
    second = workspace.sync()
    assert second.returncode == 0, second.stderr
    run_dir = workspace.runs()[-1]
    plan = read_json(run_dir / "plan" / "plan.json")
    assert plan["decisions"]["2"]["decision"] == "suppressed"

    registry = load_followups(config, "fund")
    consumed = registry["followups"][followup["id"]]
    assert consumed["status"] == "done"
    assert consumed["run_id"] == run_dir.name

    report = read_json(run_dir / "compile" / "compile-result.json")
    assert 2 in {row["document_id"] for row in report["results"]}
    doc2 = workspace.state()["books"]["fund"]["documents"]["2"]
    assert doc2["graph"]["status"] == "covered"
    assert doc2["graph"]["version"]["utc"] == version["utc"]
