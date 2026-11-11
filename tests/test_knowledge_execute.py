"""Black-box approval, extraction, validation, and recovery for knowledge sync."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_knowledge_sync import captured_update, snapshot, write_raw_baseline

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def sync(
    *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    if env:
        environment.update(env)
    # Ticket 08 converged `dogpdkb sync` onto the unified runner. These legacy
    # extraction/validation/recovery regressions exercise the retained
    # deprecated compatibility executor directly; assertions are unchanged.
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb.knowledge_sync", *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def candidate_plan(tmp_path: Path) -> tuple[dict[str, Any], Path, Path, Path, Path]:
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-07T00:00:00Z",
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    observed = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-09T00:00:00Z",
                "body": (
                    "# Limits\n\nRefund requires approval.\n\n"
                    "Refund requires manager approval when amount exceeds 100 USD.\n"
                ),
            }
        ],
    )
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "execution"
    planned = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )
    assert planned.returncode == 0, planned.stderr
    return (
        json.loads((staging / "plan.json").read_text()),
        snapshot_path,
        state_path,
        staging,
        tmp_path / "graphify-out",
    )


def review_dispositions(execution: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "issue_id": issue["issue_id"],
            "decision": (
                "approve_removal"
                if "approve_removal" in issue["allowed_decisions"]
                else "accept_revision"
                if "accept_revision" in issue["allowed_decisions"]
                else "acknowledged"
            ),
        }
        for issue in execution["review_issues"]
    ]


def approval_document(plan: dict[str, Any]) -> dict[str, Any]:
    row = plan["results"][0]
    execution = row["execution"]
    return {
        "schema_version": "knowledge-approvals-2.0",
        "plan": "execution/plan.json",
        "approvals": [
            {
                "book_id": row["book_id"],
                "document_id": row["document_id"],
                "source_version": execution["source_version"],
                "raw": execution["raw"],
                "source_scope": "whole_document",
                "review_dispositions": review_dispositions(execution),
            }
        ],
    }


def quality_document(plan: dict[str, Any]) -> dict[str, Any]:
    row = plan["results"][0]
    return {
        "schema_version": "knowledge-quality-cases-2.0",
        "plan": "execution/plan.json",
        "cases": [
            {
                "case_id": "new-limit",
                "book_id": row["book_id"],
                "document_id": row["document_id"],
                "source_version": row["execution"]["source_version"],
                "record": "node",
                "where": {"label": "Refund limit"},
                "required": {"rationale": ["manager approval", "100 USD"]},
            }
        ],
    }


def execute_arguments(
    tmp_path: Path,
    snapshot_path: Path,
    state_path: Path,
    staging: Path,
    graph_dir: Path,
) -> list[str]:
    return [
        "--knowledge",
        "--execute",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--approvals",
        str(tmp_path / "approvals.json"),
        "--quality-cases",
        str(tmp_path / "quality.json"),
        "--graph-dir",
        str(graph_dir),
        "--graphify-python",
        sys.executable,
    ]


def install_graphify_stub(tmp_path: Path) -> Path:
    package = tmp_path / "stub-packages" / "graphify"
    package.mkdir(parents=True)
    # The external Graphify stub also supplies its minimal NetworkX boundary.
    (package.parent / "networkx.py").write_text(
        "class Graph:\n"
        "    def add_node(self, *args, **kwargs): pass\n"
        "    def add_edge(self, *args, **kwargs): pass\n"
        "class DiGraph(Graph): pass\n"
    )
    (package / "__init__.py").write_text("")
    (package / "cache.py").write_text(
        """import json, os
from pathlib import Path

def _entry(path, cache_root):
    return Path(cache_root) / "graphify-out" / "cache" / "semantic-stub" / (Path(path).name + ".json")

def _log(event):
    target = os.environ.get("GRAPHIFY_STUB_LOG")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\\n")

def check_semantic_cache(files, root, cache_root, prompt):
    nodes, edges, hyperedges, missing = [], [], [], []
    for name in files:
        entry = _entry(name, cache_root)
        value = json.loads(entry.read_text()) if entry.exists() else None
        if value is None or value["body"] != Path(name).read_text():
            missing.append(name)
            continue
        _log({"event": "cache_hit", "file": Path(name).name, "cache_root": str(cache_root)})
        nodes.extend(value["nodes"])
        edges.extend(value["edges"])
        hyperedges.extend(value["hyperedges"])
    return nodes, edges, hyperedges, missing
"""
    )
    (package / "llm.py").write_text(
        """import json, os
from pathlib import Path
from graphify.cache import _entry, _log

def detect_backend():
    return None if os.environ.get("GRAPHIFY_STUB_NO_BACKEND") else "stub"

def _default_model_for_backend(backend):
    return "stub-model"

def _extraction_system(deep=False):
    return "stub-prompt"

def extract_corpus_parallel(files, backend, root, cache_root, **kwargs):
    nodes, edges = [], []
    if os.environ.get("GRAPHIFY_STUB_STATE_DRIFT") == "1":
        state_path = Path(os.environ["GRAPHIFY_STUB_STATE_PATH"])
        state = json.loads(state_path.read_text())
        state["documents"]["1"]["observation"]["observed_at"] = "concurrent-writer"
        state_path.write_text(json.dumps(state))
    partial_stem = os.environ.get("GRAPHIFY_STUB_PARTIAL")
    partial_files = []
    for path in files:
        if os.environ.get("GRAPHIFY_STUB_FAIL") == path.stem:
            raise RuntimeError("injected extraction failure")
        if partial_stem and path.stem == partial_stem:
            # Simulate a per-chunk failure: this source yields no records and is
            # reported as partial rather than aborting the whole extraction.
            partial_files.append(path.name)
            _log({"event": "partial_skip", "file": path.name})
            continue
        body = path.read_text()
        source = path.name
        current_nodes = [
            {"id": "refund-approval", "label": "Refund approval", "rationale": "Refund requires approval.", "source_file": source},
            {"id": "refund-limit", "label": "Refund limit", "rationale": "Refund requires manager approval when amount exceeds 100 USD.", "source_file": source},
        ]
        current_edges = [{"source": "refund-limit", "target": "refund-approval", "relation": "requires", "rationale": "manager approval above 100 USD", "source_file": source}]
        if os.environ.get("GRAPHIFY_STUB_DANGLING") == path.stem:
            # Emit a structurally invalid candidate (edge to a missing node) so the
            # artifact structure check must reject it before any publication.
            current_edges.append({"source": "refund-limit", "target": "missing-node", "relation": "requires", "rationale": "dangling", "source_file": source})
        value = {"body": body, "nodes": current_nodes, "edges": current_edges, "hyperedges": []}
        entry = _entry(path, cache_root)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(json.dumps(value))
        nodes.extend(current_nodes)
        edges.extend(current_edges)
        _log({"event": "extract", "file": source, "cache_root": str(cache_root)})
    result = {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 123, "output_tokens": 45, "external_calls": len(files)}
    if partial_files:
        result["external_calls"] = len(files) - len(partial_files)
        result["_partial_files"] = partial_files
        result["uncovered_files"] = [str(path) for path in files if path.name in partial_files]
    return result
"""
    )
    (package / "detect.py").write_text(
        """import json
from pathlib import Path
def save_manifest(files, *, manifest_path, kind, root):
    path = Path(manifest_path)
    value = json.loads(path.read_text())
    for name in files["document"]:
        value[Path(name).name] = {"kind": kind}
    path.write_text(json.dumps(value))
"""
    )
    (package / "analyze.py").write_text(
        "def god_nodes(graph):\n    return []\n"
        "def suggest_questions(graph, communities, labels):\n    return []\n"
        "def surprising_connections(graph, communities):\n    return []\n"
    )
    (package / "cluster.py").write_text(
        "def score_all(graph, communities):\n    return {}\n"
        "def community_member_sigs(communities):\n    return ''\n"
    )
    (package / "report.py").write_text(
        "def generate(*args, **kwargs):\n    return 'bounded stub report'\n"
    )
    return package.parent


def install_state_failure(tmp_path: Path) -> Path:
    shim = tmp_path / "failure-shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        """import os
from pathlib import Path
_replace = os.replace
def guarded_replace(source, destination):
    if (
        os.environ.get("GRAPHIFY_STUB_STATE_FAILURE") == "1"
        and Path(destination) == Path(os.environ["GRAPHIFY_STUB_STATE_PATH"])
        and Path(os.environ["GRAPHIFY_STUB_GRAPH_RESULT"]).exists()
    ):
        raise OSError("injected state failure after graph publish")
    return _replace(source, destination)
os.replace = guarded_replace
"""
    )
    return shim


def test_execute_without_approved_candidates_does_not_invoke_graphify(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {"nodes": [{"id": "old", "source_file": "old-[1].md"}], "links": []}
    write_json(graph_dir / "graph.json", original)
    approvals = tmp_path / "approvals.json"
    quality = tmp_path / "quality.json"
    write_json(
        approvals,
        {
            "schema_version": "knowledge-approvals-2.0",
            "plan": "execution/plan.json",
            "approvals": [],
        },
    )
    write_json(
        quality,
        {
            "schema_version": "knowledge-quality-cases-2.0",
            "plan": "execution/plan.json",
            "cases": [],
        },
    )
    before = (graph_dir / "graph.json").read_bytes()

    result = sync(
        "--knowledge",
        "--execute",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--approvals",
        str(approvals),
        "--quality-cases",
        str(quality),
        "--graph-dir",
        str(graph_dir),
        "--graphify-python",
        sys.executable,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads((staging / "execution-result.json").read_text())
    assert report["counts"]["approved"] == 0
    assert report["counts"]["external_calls"] == 0
    assert report["counts"]["cache_hits"] == 0
    assert report["counts"]["graph_successes"] == 0
    assert (graph_dir / "graph.json").read_bytes() == before
    assert not (staging / "graph-update").exists()
    assert plan["results"][0]["action"] == "BODY_DELTA_CANDIDATE"


def test_execution_rejects_state_from_another_book_with_same_document_id(
    tmp_path: Path,
):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    state = json.loads(state_path.read_text())
    state["book"]["book_id"] = 456
    write_json(state_path, state)
    stub = install_graphify_stub(tmp_path)
    log = tmp_path / "graphify-calls.jsonl"

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={
            "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")]),
            "GRAPHIFY_STUB_LOG": str(log),
        },
    )

    assert result.returncode != 0
    assert "different book" in result.stderr
    assert not log.exists()
    assert json.loads(state_path.read_text())["documents"]["1"]["pending"] == [
        "APPROVAL_REQUIRED"
    ]


def test_external_pass_document_is_not_quality_evidence(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(
        tmp_path / "quality.json",
        {
            "schema_version": "knowledge-quality-cases-2.0",
            "plan": "execution/plan.json",
            "pass": True,
            "cases": [],
        },
    )
    stub = install_graphify_stub(tmp_path)

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode != 0
    assert "at least one quality case" in result.stderr
    assert not (staging / "graph-update").exists()


def test_approved_execution_replaces_complete_source_and_updates_state(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(
        graph_dir / "graph.json",
        {
            "directed": True,
            "nodes": [
                {
                    "id": "old-approval",
                    "label": "Refund approval",
                    "rationale": "Refund requires approval.",
                    "source_file": "old-[1].md",
                    "source_url": "https://example.invalid/doc/1",
                },
                {"id": "outside", "label": "Outside", "source_file": "outside-[2].md"},
            ],
            "links": [],
            "hyperedges": [],
        },
    )
    write_json(tmp_path / "approvals.json", approval_document(plan))
    quality = quality_document(plan)
    quality["cases"].append(
        {
            "case_id": "old-restriction",
            "book_id": 123,
            "document_id": 1,
            "source_version": plan["results"][0]["execution"]["source_version"],
            "record": "node",
            "where": {"label": "Refund approval"},
            "required": {"rationale": ["Refund", "requires approval"]},
        }
    )
    quality["cases"].append(
        {
            "case_id": "subject-condition-relation",
            "book_id": 123,
            "document_id": 1,
            "source_version": plan["results"][0]["execution"]["source_version"],
            "record": "edge",
            "where": {
                "source_label": "Refund limit",
                "target_label": "Refund approval",
                "relation": "requires",
            },
            "required": {"rationale": ["manager approval", "100 USD"]},
        }
    )
    write_json(tmp_path / "quality.json", quality)
    stub = install_graphify_stub(tmp_path)
    log = tmp_path / "graphify-calls.jsonl"

    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )
    env = {
        "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")]),
        "GRAPHIFY_STUB_LOG": str(log),
    }
    result = sync(*arguments, env=env)

    assert result.returncode == 0, result.stderr
    graph = json.loads((graph_dir / "graph.json").read_text())
    by_id = {node["id"]: node for node in graph["nodes"]}
    assert by_id["refund-limit"]["source_file"] == "source-[1].md"
    assert by_id["refund-limit"]["source_url"] == "https://example.invalid/doc/1"
    assert by_id["old-approval"]["source_file"] == "source-[1].md"
    assert by_id["outside"]["source_file"] == "outside-[2].md"
    assert not any(node["source_file"] == "old-[1].md" for node in graph["nodes"])
    frozen = (staging / plan["results"][0]["execution"]["raw"]["path"]).read_bytes()
    assert (staging / "execution-corpus" / "source-[1].md").read_bytes() == frozen
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["event"] for row in calls] == ["extract", "cache_hit"]
    assert all(row["cache_root"] == str(graph_dir.parent) for row in calls)
    report = json.loads((staging / "execution-result.json").read_text())
    assert report["counts"] == {
        "approved": 1,
        "executions": 1,
        "external_calls": 1,
        "cache_hits": 0,
        "graph_successes": 1,
    }
    assert report["tokens"] == {
        "input_tokens": 123,
        "output_tokens": 45,
        "evidence": "graphify_result",
    }
    assert report["cost"] == {"status": "unknown", "evidence": "none"}
    assert report["quality_gate"]["passed_cases"] == 3
    state = json.loads(state_path.read_text())
    record = state["documents"]["1"]
    assert record["graph"]["version"]["utc"] == "2026-11-09T00:00:00Z"
    assert record["graph"]["coverage"] == "approved_whole_document"
    assert record["pending"] == []
    report_bytes = (staging / "execution-result.json").read_bytes()
    published = (graph_dir / "graph.json").read_bytes()

    repeated = sync(*arguments, env=env)

    assert repeated.returncode == 0, repeated.stderr
    assert (staging / "execution-result.json").read_bytes() == report_bytes
    assert (graph_dir / "graph.json").read_bytes() == published
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(row["event"] == "extract" for row in calls) == 1


def test_missing_old_limit_blocks_publish_and_candidate_resume_is_cached(
    tmp_path: Path,
):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {
        "directed": True,
        "nodes": [
            {
                "id": "old-approval",
                "label": "Refund approval",
                "rationale": "Refund requires approval.",
                "source_file": "old-[1].md",
            }
        ],
        "links": [],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", original)
    write_json(tmp_path / "approvals.json", approval_document(plan))
    quality = quality_document(plan)
    quality["cases"][0]["where"]["label"] = "Missing restriction"
    write_json(tmp_path / "quality.json", quality)
    stub = install_graphify_stub(tmp_path)
    log = tmp_path / "graphify-calls.jsonl"
    env = {
        "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")]),
        "GRAPHIFY_STUB_LOG": str(log),
    }
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )

    failed = sync(*arguments, env=env)

    assert failed.returncode != 0
    assert "quality case failed: new-limit" in failed.stderr
    assert json.loads((graph_dir / "graph.json").read_text()) == original
    state = json.loads(state_path.read_text())
    assert state["documents"]["1"]["graph"]["version"]["utc"] == "2026-11-07T00:00:00Z"
    assert state["documents"]["1"]["pending"] == ["APPROVAL_REQUIRED"]
    assert not (staging / "execution-result.json").exists()
    assert (staging / "graph-update" / "candidate-ready.json").is_file()
    quality["cases"][0]["where"]["label"] = "Refund limit"
    write_json(tmp_path / "quality.json", quality)

    resumed = sync(*arguments, env=env)

    assert resumed.returncode == 0, resumed.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(row["event"] == "extract" for row in calls) == 1
    assert json.loads(state_path.read_text())["documents"]["1"]["pending"] == []


def test_invalid_fresh_edge_is_rejected_before_lossy_merge(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {
        "directed": True,
        "nodes": [
            {
                "id": "old-approval",
                "label": "Refund approval",
                "rationale": "Refund requires approval.",
                "source_file": "old-[1].md",
            }
        ],
        "links": [],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", original)
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    llm = stub / "graphify" / "llm.py"
    source = llm.read_text()
    assert '"target": "refund-approval"' in source
    llm.write_text(
        source.replace('"target": "refund-approval"', '"target": "missing-node"')
    )

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode != 0
    assert "candidate-only failed" in result.stderr
    assert json.loads((graph_dir / "graph.json").read_text()) == original
    assert not (staging / "execution-result.json").exists()
    assert not (staging / "graph-update" / "candidate-ready.json").exists()
    assert json.loads(state_path.read_text())["documents"]["1"]["pending"] == [
        "APPROVAL_REQUIRED"
    ]


def test_fresh_reference_to_replaced_old_node_is_not_silently_dropped(
    tmp_path: Path,
):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {
        "directed": True,
        "nodes": [
            {
                "id": "old-only",
                "label": "Old only",
                "rationale": "Old rule absent from fresh extraction.",
                "source_file": "old-[1].md",
            }
        ],
        "links": [],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", original)
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    llm = stub / "graphify" / "llm.py"
    source = llm.read_text()
    assert '"target": "refund-approval"' in source
    llm.write_text(
        source.replace('"target": "refund-approval"', '"target": "old-only"')
    )

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode != 0
    assert "candidate-only failed" in result.stderr
    assert json.loads((graph_dir / "graph.json").read_text()) == original
    assert not (staging / "graph-update" / "candidate-ready.json").exists()


def test_invalid_cached_hyperedge_is_rejected_before_lossy_merge(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {"directed": True, "nodes": [], "links": [], "hyperedges": []}
    write_json(graph_dir / "graph.json", original)
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    llm = stub / "graphify" / "llm.py"
    source = llm.read_text()
    old = '"edges": current_edges, "hyperedges": []'
    assert old in source
    llm.write_text(
        source.replace(
            old,
            '"edges": current_edges, '
            '"hyperedges": [{"members": ["refund-limit", "missing-node"], "source_file": source}]',
        )
    )

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode != 0
    assert "candidate-only failed" in result.stderr
    assert json.loads((graph_dir / "graph.json").read_text()) == original
    assert not (staging / "graph-update" / "candidate-ready.json").exists()


def test_partially_missing_fresh_hyperedge_is_rejected_after_replacement(
    tmp_path: Path,
):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {
        "directed": True,
        "nodes": [
            {
                "id": "old-only",
                "label": "Old only",
                "rationale": "Old rule absent from fresh extraction.",
                "source_file": "old-[1].md",
            },
            {
                "id": "outside",
                "label": "Outside",
                "rationale": "Unrelated source remains in candidate.",
                "source_file": "outside-[2].md",
            },
        ],
        "links": [],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", original)
    state_before = state_path.read_bytes()
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    llm = stub / "graphify" / "llm.py"
    source = llm.read_text()
    old = '"edges": current_edges, "hyperedges": []'
    assert old in source
    llm.write_text(
        source.replace(
            old,
            '"edges": current_edges, '
            '"hyperedges": [{"members": ["refund-limit", "old-only"], "source_file": source}]',
        )
    )

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode != 0
    assert "candidate-only failed" in result.stderr
    assert (graph_dir / "graph.json").read_text() == json.dumps(original)
    assert state_path.read_bytes() == state_before
    assert not (staging / "execution-result.json").exists()
    assert not (staging / "graph-update" / "candidate-ready.json").exists()


def test_fresh_hyperedge_with_all_members_present_is_published(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(
        graph_dir / "graph.json",
        {"directed": True, "nodes": [], "links": [], "hyperedges": []},
    )
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    llm = stub / "graphify" / "llm.py"
    source = llm.read_text()
    old = '"edges": current_edges, "hyperedges": []'
    assert old in source
    llm.write_text(
        source.replace(
            old,
            '"edges": current_edges, '
            '"hyperedges": [{"members": ["refund-limit", "refund-approval"], "source_file": source}]',
        )
    )

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode == 0, result.stderr
    graph = json.loads((graph_dir / "graph.json").read_text())
    assert len(graph["hyperedges"]) == 1
    assert set(graph["hyperedges"][0]["members"]) == {
        "refund-limit",
        "refund-approval",
    }
    assert (
        json.loads(state_path.read_text())["documents"]["1"]["graph"]["version"]["utc"]
        == "2026-11-09T00:00:00Z"
    )


def test_unrelated_source_edge_collision_is_safely_rejected(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {
        "directed": True,
        "nodes": [
            {
                "id": "old-approval",
                "label": "Refund approval",
                "rationale": "Refund requires approval.",
                "source_file": "old-[1].md",
            },
            {
                "id": "old-limit",
                "label": "Refund limit",
                "rationale": "Old limit",
                "source_file": "old-[1].md",
            },
        ],
        "links": [
            {
                "source": "old-limit",
                "target": "old-approval",
                "relation": "prohibits",
                "rationale": "unapproved source fact",
                "source_file": "outside-[2].md",
            }
        ],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", original)
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode != 0
    assert "candidate-only failed" in result.stderr
    assert json.loads((graph_dir / "graph.json").read_text()) == original
    assert not (staging / "execution-result.json").exists()
    assert json.loads(state_path.read_text())["documents"]["1"]["pending"] == [
        "APPROVAL_REQUIRED"
    ]


def test_missing_adapter_usage_is_unknown_not_measured_zero(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(
        graph_dir / "graph.json",
        {
            "directed": True,
            "nodes": [
                {
                    "id": "old-approval",
                    "label": "Refund approval",
                    "rationale": "Refund requires approval.",
                    "source_file": "old-[1].md",
                }
            ],
            "links": [],
            "hyperedges": [],
        },
    )
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    llm = stub / "graphify" / "llm.py"
    source = llm.read_text()
    assert ', "input_tokens": 123, "output_tokens": 45' in source
    assert ', "external_calls": len(files)' in source
    llm.write_text(
        source.replace(', "input_tokens": 123, "output_tokens": 45', "").replace(
            ', "external_calls": len(files)', ""
        )
    )

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode == 0, result.stderr
    report = json.loads((staging / "execution-result.json").read_text())
    assert report["tokens"] == {
        "input_tokens": None,
        "output_tokens": None,
        "evidence": "unknown",
    }
    assert report["counts"]["external_calls"] is None
    assert report["usage"]["token_evidence"] == "unknown"
    assert report["usage"]["external_calls_evidence"] == "unknown"


def test_concurrent_graph_baseline_change_rejects_publish(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {
        "directed": True,
        "nodes": [
            {
                "id": "old-approval",
                "label": "Refund approval",
                "rationale": "Refund requires approval.",
                "source_file": "old-[1].md",
            }
        ],
        "links": [],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", original)
    write_json(tmp_path / "approvals.json", approval_document(plan))
    quality = quality_document(plan)
    quality["cases"][0]["where"]["label"] = "Missing until candidate exists"
    write_json(tmp_path / "quality.json", quality)
    stub = install_graphify_stub(tmp_path)
    env = {"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])}
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )
    failed = sync(*arguments, env=env)
    assert failed.returncode != 0
    external = {
        "directed": True,
        "nodes": [{"id": "external", "source_file": "outside-[9].md"}],
        "links": [],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", external)
    quality["cases"][0]["where"]["label"] = "Refund limit"
    write_json(tmp_path / "quality.json", quality)

    resumed = sync(*arguments, env=env)

    assert resumed.returncode != 0
    assert "publish-validated failed" in resumed.stderr
    assert json.loads((graph_dir / "graph.json").read_text()) == external
    assert (
        json.loads(state_path.read_text())["documents"]["1"]["graph"]["version"]["utc"]
        == "2026-11-07T00:00:00Z"
    )
    assert not (staging / "execution-result.json").exists()


def test_state_change_during_candidate_build_blocks_publication(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {
        "directed": True,
        "nodes": [
            {
                "id": "old-approval",
                "label": "Refund approval",
                "rationale": "Refund requires approval.",
                "source_file": "old-[1].md",
            }
        ],
        "links": [],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", original)
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    env = {
        "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")]),
        "GRAPHIFY_STUB_STATE_DRIFT": "1",
        "GRAPHIFY_STUB_STATE_PATH": str(state_path),
    }

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env=env,
    )

    assert result.returncode != 0
    assert "knowledge state changed concurrently" in result.stderr
    assert json.loads((graph_dir / "graph.json").read_text()) == original
    assert not (staging / "graph-update" / "result.json").exists()
    assert (staging / "graph-update" / "candidate-ready.json").is_file()
    assert not (staging / "execution-result.json").exists()
    assert (
        json.loads(state_path.read_text())["documents"]["1"]["observation"][
            "observed_at"
        ]
        == "concurrent-writer"
    )


def test_graph_publish_then_state_failure_recovers_without_rebilling(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(
        graph_dir / "graph.json",
        {
            "directed": True,
            "nodes": [
                {
                    "id": "old-approval",
                    "label": "Refund approval",
                    "rationale": "Refund requires approval.",
                    "source_file": "old-[1].md",
                }
            ],
            "links": [],
            "hyperedges": [],
        },
    )
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    shim = install_state_failure(tmp_path)
    log = tmp_path / "graphify-calls.jsonl"
    env = {
        "PYTHONPATH": os.pathsep.join([str(shim), str(stub), str(ROOT / "src")]),
        "GRAPHIFY_STUB_LOG": str(log),
        "GRAPHIFY_STUB_STATE_FAILURE": "1",
        "GRAPHIFY_STUB_STATE_PATH": str(state_path),
        "GRAPHIFY_STUB_GRAPH_RESULT": str(staging / "graph-update" / "result.json"),
    }
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )

    failed = sync(*arguments, env=env)

    assert failed.returncode != 0
    assert "injected state failure" in failed.stderr
    assert not (staging / "execution-result.json").exists()
    assert (
        json.loads(state_path.read_text())["documents"]["1"]["graph"]["version"]["utc"]
        == "2026-11-07T00:00:00Z"
    )
    published = (graph_dir / "graph.json").read_bytes()
    assert b"refund-limit" in published
    assert (staging / "graph-update" / "result.json").is_file()
    env["GRAPHIFY_STUB_STATE_FAILURE"] = "0"

    resumed = sync(*arguments, env=env)

    assert resumed.returncode == 0, resumed.stderr
    assert (graph_dir / "graph.json").read_bytes() == published
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(row["event"] == "extract" for row in calls) == 1
    assert (
        json.loads(state_path.read_text())["documents"]["1"]["graph"]["version"]["utc"]
        == "2026-11-09T00:00:00Z"
    )
    assert (staging / "execution-result.json").is_file()


def test_hard_exit_after_state_commit_recovers_report_without_reextracting(
    tmp_path: Path,
):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(
        graph_dir / "graph.json",
        {
            "directed": True,
            "nodes": [
                {
                    "id": "old-approval",
                    "label": "Refund approval",
                    "rationale": "Refund requires approval.",
                    "source_file": "old-[1].md",
                }
            ],
            "links": [],
            "hyperedges": [],
        },
    )
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    shim = tmp_path / "hard-exit-shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        """import os
from pathlib import Path
original = os.replace
def exit_after_state(source, destination, *args, **kwargs):
    result = original(source, destination, *args, **kwargs)
    if (
        os.environ.get("INJECT_HARD_EXIT") == "1"
        and Path(destination) == Path(os.environ["STATE_PATH"])
        and Path(os.environ["GRAPH_RESULT"]).exists()
    ):
        os._exit(77)
    return result
os.replace = exit_after_state
"""
    )
    log = tmp_path / "graphify-calls.jsonl"
    env = {
        "PYTHONPATH": os.pathsep.join([str(shim), str(stub), str(ROOT / "src")]),
        "GRAPHIFY_STUB_LOG": str(log),
        "INJECT_HARD_EXIT": "1",
        "STATE_PATH": str(state_path),
        "GRAPH_RESULT": str(staging / "graph-update" / "result.json"),
    }
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )

    crashed = sync(*arguments, env=env)

    assert crashed.returncode == 77
    assert not (staging / "execution-result.json").exists()
    published = (graph_dir / "graph.json").read_bytes()
    assert b"refund-limit" in published
    assert (
        json.loads(state_path.read_text())["documents"]["1"]["graph"]["version"]["utc"]
        == "2026-11-09T00:00:00Z"
    )
    env["INJECT_HARD_EXIT"] = "0"

    resumed = sync(*arguments, env=env)

    assert resumed.returncode == 0, resumed.stderr
    assert (staging / "execution-result.json").is_file()
    assert (graph_dir / "graph.json").read_bytes() == published
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(row["event"] == "extract" for row in calls) == 1


def test_new_document_can_be_added_to_existing_graph_without_full_rebuild(
    tmp_path: Path,
):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Existing",
                "version": old_version,
                "body": "# Existing\n\nExisting rule.\n",
            }
        ],
    )
    observed = snapshot(
        [
            {"id": 1, "title": "Existing", "version": old_version},
            {"id": 2, "title": "New", "version": new_version},
        ],
        status="captured",
    )
    observed["changes"]["new"] = [2]
    observed["selected"] = [2]
    raw = tmp_path / "current-raw" / "2.md"
    raw.parent.mkdir(parents=True)
    raw.write_text(
        "# New\n\nRefund requires manager approval when amount exceeds 100 USD.\n"
    )
    observed["exports"]["2"] = {
        "status": "captured",
        "labels": ["new"],
        "raw": {"path": "current-raw/2.md", "stage": "raw_export"},
        "listing_version": observed["documents"]["2"]["version"],
        "detail_version": observed["documents"]["2"]["version"],
        "original": {"status": "not_applicable"},
    }
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "execution"
    planned = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )
    assert planned.returncode == 0, planned.stderr
    plan = json.loads((staging / "plan.json").read_text())
    row = plan["results"][1]
    execution = row["execution"]
    write_json(
        tmp_path / "approvals.json",
        {
            "schema_version": "knowledge-approvals-2.0",
            "plan": "execution/plan.json",
            "approvals": [
                {
                    "book_id": 123,
                    "document_id": 2,
                    "source_version": execution["source_version"],
                    "raw": execution["raw"],
                    "source_scope": "whole_document",
                    "review_dispositions": review_dispositions(execution),
                }
            ],
        },
    )
    write_json(
        tmp_path / "quality.json",
        {
            "schema_version": "knowledge-quality-cases-2.0",
            "plan": "execution/plan.json",
            "cases": [
                {
                    "case_id": "new-document-fact",
                    "book_id": 123,
                    "document_id": 2,
                    "source_version": execution["source_version"],
                    "record": "node",
                    "where": {"label": "Refund limit"},
                    "required": {"rationale": ["manager approval", "100 USD"]},
                }
            ],
        },
    )
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    write_json(
        graph_dir / "graph.json",
        {
            "directed": True,
            "nodes": [
                {"id": "existing", "label": "Existing", "source_file": "old-[1].md"}
            ],
            "links": [],
            "hyperedges": [],
        },
    )
    stub = install_graphify_stub(tmp_path)

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])},
    )

    assert result.returncode == 0, result.stderr
    graph = json.loads((graph_dir / "graph.json").read_text())
    assert "existing" in {node["id"] for node in graph["nodes"]}
    assert any(node["source_file"] == "source-[2].md" for node in graph["nodes"])
    state = json.loads(state_path.read_text())
    assert state["documents"]["1"]["graph"]["version"]["utc"] == old_version
    assert state["documents"]["2"]["graph"]["version"]["utc"] == new_version
    assert state["documents"]["2"]["pending"] == []
    report = json.loads((staging / "execution-result.json").read_text())
    assert report["counts"]["approved"] == 1
    assert report["counts"]["graph_successes"] == 1


def test_partial_extraction_failure_preserves_graph_and_resumes_from_cache(
    tmp_path: Path,
):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    documents = [
        {
            "id": doc_id,
            "title": f"Limits {doc_id}",
            "version": new_version,
            "body": "# Limits\n\nRefund requires approval.\n\nRefund requires manager approval when amount exceeds 100 USD.\n",
        }
        for doc_id in (1, 2)
    ]
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                **document,
                "version": old_version,
                "body": "# Limits\n\nRefund requires approval.\n",
            }
            for document in documents
        ],
    )
    observed = captured_update(tmp_path, documents)
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "execution"
    planned = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )
    assert planned.returncode == 0, planned.stderr
    plan = json.loads((staging / "plan.json").read_text())
    approvals = []
    cases = []
    for row in plan["results"]:
        execution = row["execution"]
        approvals.append(
            {
                "book_id": row["book_id"],
                "document_id": row["document_id"],
                "source_version": execution["source_version"],
                "raw": execution["raw"],
                "source_scope": "whole_document",
                "review_dispositions": review_dispositions(execution),
            }
        )
        cases.append(
            {
                "case_id": f"new-limit-{row['document_id']}",
                "book_id": row["book_id"],
                "document_id": row["document_id"],
                "source_version": execution["source_version"],
                "record": "node",
                "where": {"label": "Refund limit"},
                "required": {"rationale": ["manager approval", "100 USD"]},
            }
        )
    write_json(
        tmp_path / "approvals.json",
        {
            "schema_version": "knowledge-approvals-2.0",
            "plan": "execution/plan.json",
            "approvals": approvals,
        },
    )
    write_json(
        tmp_path / "quality.json",
        {
            "schema_version": "knowledge-quality-cases-2.0",
            "plan": "execution/plan.json",
            "cases": cases,
        },
    )
    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    original = {
        "directed": True,
        "nodes": [
            {
                "id": f"old-{doc_id}",
                "label": "Refund approval",
                "rationale": "Refund requires approval.",
                "source_file": f"old-[{doc_id}].md",
            }
            for doc_id in (1, 2)
        ],
        "links": [],
        "hyperedges": [],
    }
    write_json(graph_dir / "graph.json", original)
    original_bytes = (graph_dir / "graph.json").read_bytes()
    stub = install_graphify_stub(tmp_path)
    log = tmp_path / "graphify-calls.jsonl"
    env = {
        "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")]),
        "GRAPHIFY_STUB_LOG": str(log),
        "GRAPHIFY_STUB_FAIL": "source-[2]",
    }
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )

    failed = sync(*arguments, env=env)

    assert failed.returncode != 0
    assert "candidate-only failed" in failed.stderr
    assert (graph_dir / "graph.json").read_bytes() == original_bytes
    assert not (staging / "execution-result.json").exists()
    failed_state = json.loads(state_path.read_text())
    assert all(
        failed_state["documents"][str(doc_id)]["graph"]["version"]["utc"] == old_version
        for doc_id in (1, 2)
    )
    env["GRAPHIFY_STUB_FAIL"] = ""

    resumed = sync(*arguments, env=env)

    assert resumed.returncode == 0, resumed.stderr
    report = json.loads((staging / "execution-result.json").read_text())
    assert report["counts"]["approved"] == 2
    assert report["counts"]["external_calls"] == 1
    assert report["counts"]["cache_hits"] == 1
    assert report["counts"]["graph_successes"] == 2
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["file"] for row in calls if row["event"] == "extract"] == [
        "source-[1].md",
        "source-[2].md",
    ]
    state = json.loads(state_path.read_text())
    assert all(state["documents"][str(doc_id)]["pending"] == [] for doc_id in (1, 2))


def old_rule_plan(tmp_path: Path) -> tuple[dict[str, Any], Path, Path, Path, Path]:
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-07T00:00:00Z",
                "body": (
                    "# Limits\n\nRefund requires approval.\n\n"
                    "Never give unverified applicants login access.\n\n"
                    "Only Finance can authorize chargebacks.\n"
                ),
            }
        ],
    )
    observed = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-09T00:00:00Z",
                "body": (
                    "# Limits\n\nRefund requires approval.\n\n"
                    "Refund requires manager approval when amount exceeds 100 USD.\n"
                ),
            }
        ],
    )
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "execution"
    planned = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )
    assert planned.returncode == 0, planned.stderr
    return (
        json.loads((staging / "plan.json").read_text()),
        snapshot_path,
        state_path,
        staging,
        tmp_path / "graphify-out",
    )


def test_each_old_rule_requires_an_individual_explicit_disposition(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = old_rule_plan(tmp_path)
    assert plan["schema_version"] == "knowledge-plan-2.0"
    execution = plan["results"][0]["execution"]
    old_issues = [
        issue
        for issue in execution["review_issues"]
        if issue["code"] == "OLD_BODY_NOT_OBSERVED"
    ]
    assert len(old_issues) == 2
    assert {issue["old"]["start_line"] for issue in old_issues} == {5, 7}
    assert len({issue["issue_id"] for issue in old_issues}) == 2
    graph_dir.mkdir()
    write_json(
        graph_dir / "graph.json",
        {
            "directed": True,
            "nodes": [
                {
                    "id": "old-login",
                    "label": "Old login limit",
                    "rationale": "Never give unverified applicants login access.",
                    "source_file": "old-[1].md",
                },
                {
                    "id": "old-finance",
                    "label": "Old finance limit",
                    "rationale": "Only Finance can authorize chargebacks.",
                    "source_file": "old-[1].md",
                },
            ],
            "links": [],
            "hyperedges": [],
        },
    )
    approval = approval_document(plan)
    dispositions = approval["approvals"][0]["review_dispositions"]
    old_dispositions = [
        item
        for item in dispositions
        if item["issue_id"] in {issue["issue_id"] for issue in old_issues}
    ]
    assert len(old_dispositions) == 2
    assert all(item["decision"] == "approve_removal" for item in old_dispositions)
    write_json(tmp_path / "quality.json", quality_document(plan))
    stub = install_graphify_stub(tmp_path)
    log = tmp_path / "graphify-calls.jsonl"
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )
    env = {
        "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")]),
        "GRAPHIFY_STUB_LOG": str(log),
    }
    removed = next(
        item for item in dispositions if item["issue_id"] == old_issues[0]["issue_id"]
    )
    dispositions.remove(removed)
    write_json(tmp_path / "approvals.json", approval)

    failed = sync(*arguments, env=env)

    assert failed.returncode != 0
    assert "exactly cover review issues" in failed.stderr
    assert not log.exists()
    dispositions.append(removed)
    write_json(tmp_path / "approvals.json", approval)

    succeeded = sync(*arguments, env=env)

    assert succeeded.returncode == 0, succeeded.stderr
    labels = {
        node["label"]
        for node in json.loads((graph_dir / "graph.json").read_text())["nodes"]
    }
    assert "Old login limit" not in labels
    assert "Old finance limit" not in labels


def test_preserved_old_rule_requires_and_validates_its_quality_case(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = old_rule_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": [], "hyperedges": []})
    execution = plan["results"][0]["execution"]
    issue = next(
        item
        for item in execution["review_issues"]
        if item["code"] == "OLD_BODY_NOT_OBSERVED" and item["old"]["start_line"] == 5
    )
    approval = approval_document(plan)
    disposition = next(
        item
        for item in approval["approvals"][0]["review_dispositions"]
        if item["issue_id"] == issue["issue_id"]
    )
    disposition["decision"] = "preserve"
    write_json(tmp_path / "approvals.json", approval)
    quality = quality_document(plan)
    quality["cases"].append(
        {
            "case_id": "old-login",
            "book_id": 123,
            "document_id": 1,
            "source_version": execution["source_version"],
            "record": "node",
            "where": {"label": "Old login limit"},
            "required": {"rationale": ["unverified applicants", "login access"]},
        }
    )
    write_json(tmp_path / "quality.json", quality)
    stub = install_graphify_stub(tmp_path)
    llm = stub / "graphify" / "llm.py"
    source = llm.read_text()
    marker = "        current_edges = "
    assert marker in source
    llm.write_text(
        source.replace(
            marker,
            '        current_nodes.append({"id": "old-login", "label": "Old login limit", "rationale": "Never give unverified applicants login access.", "source_file": source})\n'
            + marker,
        )
    )
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )
    env = {"PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")])}

    missing_binding = sync(*arguments, env=env)

    assert missing_binding.returncode != 0
    assert "requires explicit quality case bindings" in missing_binding.stderr
    disposition["quality_case_ids"] = ["old-login"]
    write_json(tmp_path / "approvals.json", approval)

    succeeded = sync(*arguments, env=env)

    assert succeeded.returncode == 0, succeeded.stderr
    labels = {
        node["label"]
        for node in json.loads((graph_dir / "graph.json").read_text())["nodes"]
    }
    assert "Old login limit" in labels
    assert "Old finance limit" not in labels


def test_legacy_generic_review_ack_is_safely_rejected(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
    legacy = approval_document(plan)
    legacy["schema_version"] = "knowledge-approvals-1.0"
    legacy["approvals"][0]["review_dispositions"] = [
        {"code": "OLD_BODY_NOT_OBSERVED", "decision": "acknowledged"}
    ]
    write_json(tmp_path / "approvals.json", legacy)
    write_json(tmp_path / "quality.json", quality_document(plan))

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir)
    )

    assert result.returncode != 0
    assert "unsupported schema" in result.stderr
    assert not (staging / "graph-update").exists()


def test_planner_compares_the_same_frozen_new_bytes_it_approves(tmp_path: Path):
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-07T00:00:00Z",
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    observed = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-09T00:00:00Z",
                "body": "# Limits\n\nRefund requires approval.\n\nFee is 100 USD.\n",
            }
        ],
    )
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        """import os
from pathlib import Path
original = Path.read_bytes
triggered = False
def mutate_before_read(self):
    global triggered
    if not triggered and str(self) == os.environ.get("MUTATE_INPUT"):
        triggered = True
        self.write_text("# Limits\\n\\nThis is a DIFFERENT source, fee is ZERO.\\n")
    return original(self)
Path.read_bytes = mutate_before_read
"""
    )
    current_raw = tmp_path / "current-raw" / "1.md"
    staging = tmp_path / "execution"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(tmp_path / "state.json"),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
        env={
            "PYTHONPATH": os.pathsep.join([str(shim), str(ROOT / "src")]),
            "MUTATE_INPUT": str(current_raw),
        },
    )

    assert result.returncode == 0, result.stderr
    row = json.loads((staging / "plan.json").read_text())["results"][0]
    frozen = staging / row["execution"]["raw"]["path"]
    changed = "This is a DIFFERENT source, fee is ZERO."
    expected = changed if changed in frozen.read_text() else "Fee is 100 USD."
    assert row["comparison"]["graphify_plan"]["candidate_body_chars"] == len(expected)


def test_old_comparison_raw_change_after_approval_is_rejected(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    (tmp_path / "baseline-raw" / "1.md").write_text(
        "# Limits\n\nA DIFFERENT OLD LIMIT REQUIRES BOARD APPROVAL.\n"
    )
    stub = install_graphify_stub(tmp_path)
    log = tmp_path / "graphify-calls.jsonl"

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir),
        env={
            "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")]),
            "GRAPHIFY_STUB_LOG": str(log),
        },
    )

    assert result.returncode != 0
    assert "old comparison input changed" in result.stderr
    assert not log.exists()


def test_superseded_plan_cannot_publish_or_clear_newer_pending(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    original = {"nodes": [], "links": [], "hyperedges": []}
    write_json(graph_dir / "graph.json", original)
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    newer_root = tmp_path / "newer"
    newer_root.mkdir()
    newer = captured_update(
        newer_root,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-10T00:00:00Z",
                "body": (
                    "# Limits\n\nRefund requires approval.\n\n"
                    "Refund now requires director approval for 200 USD.\n"
                ),
            }
        ],
    )
    newer_snapshot = newer_root / "snapshot.json"
    write_json(newer_snapshot, newer)
    newer_staging = newer_root / "planning"
    planned = sync(
        "--knowledge",
        "--snapshot",
        str(newer_snapshot),
        "--state",
        str(state_path),
        "--staging",
        str(newer_staging),
    )
    assert planned.returncode == 0, planned.stderr
    before = (graph_dir / "graph.json").read_bytes()

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir)
    )

    assert result.returncode != 0
    assert "superseded" in result.stderr
    record = json.loads(state_path.read_text())["documents"]["1"]
    assert record["observation"]["version"]["utc"] == "2026-11-10T00:00:00Z"
    assert record["graph"]["version"]["utc"] == "2026-11-07T00:00:00Z"
    assert record["pending"] == ["APPROVAL_REQUIRED"]
    assert (graph_dir / "graph.json").read_bytes() == before


def test_raw_change_after_approval_is_rejected_before_extraction(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    current_raw = tmp_path / "current-raw" / "1.md"
    current_raw.write_text(current_raw.read_text() + "Changed after approval.\n")

    result = sync(
        *execute_arguments(tmp_path, snapshot_path, state_path, staging, graph_dir)
    )

    assert result.returncode != 0
    assert "raw input changed" in result.stderr
    assert not (staging / "execution-result.json").exists()
    assert not (staging / "graph-update").exists()


def test_execution_rejects_input_aliases_and_output_ancestors(tmp_path: Path):
    for collision in (
        "result-hardlink",
        "result-symlink",
        "raw-hardlink",
        "candidate-hardlink",
        "graph-ancestor",
    ):
        case_root = tmp_path / collision
        case_root.mkdir()
        plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(case_root)
        graph_dir.mkdir()
        write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
        write_json(case_root / "approvals.json", approval_document(plan))
        quality_path = case_root / "quality.json"
        quality = quality_document(plan)
        if collision == "graph-ancestor":
            quality_path = graph_dir / "quality.json"
            quality["plan"] = os.path.relpath(staging / "plan.json", graph_dir)
        write_json(quality_path, quality)
        frozen = staging / plan["results"][0]["execution"]["raw"]["path"]
        if collision.startswith("result-"):
            output = staging / "execution-result.json"
            if collision == "result-hardlink":
                os.link(quality_path, output)
            else:
                output.symlink_to(quality_path)
        elif collision == "raw-hardlink":
            output = staging / "execution-corpus" / "source-[1].md"
            output.parent.mkdir(parents=True)
            os.link(frozen, output)
        elif collision == "candidate-hardlink":
            output = staging / "graph-update" / "candidate" / "graph.json"
            output.parent.mkdir(parents=True)
            os.link(quality_path, output)
        arguments = execute_arguments(
            case_root, snapshot_path, state_path, staging, graph_dir
        )
        arguments[arguments.index("--quality-cases") + 1] = str(quality_path)
        protected = [
            case_root / "approvals.json",
            quality_path,
            snapshot_path,
            state_path,
            staging / "plan.json",
            frozen,
        ]
        before = {path: path.read_bytes() for path in protected}

        result = sync(*arguments)

        assert result.returncode != 0, collision
        assert "REPORT_INPUT_COLLISION" in result.stderr, collision
        assert {path: path.read_bytes() for path in protected} == before, collision
        assert json.loads((graph_dir / "graph.json").read_text()) == {
            "nodes": [],
            "links": [],
        }


def test_execution_outputs_cannot_overwrite_approval_input(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
    approvals = staging / "execution-manifest.json"
    approval = approval_document(plan)
    approval["plan"] = "plan.json"
    write_json(approvals, approval)
    write_json(tmp_path / "quality.json", quality_document(plan))
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )
    arguments[arguments.index("--approvals") + 1] = str(approvals)
    protected = [
        approvals,
        tmp_path / "quality.json",
        snapshot_path,
        state_path,
        staging / "plan.json",
        staging / plan["results"][0]["execution"]["raw"]["path"],
    ]
    before = {path: path.read_bytes() for path in protected}

    result = sync(*arguments)

    assert result.returncode != 0
    assert "REPORT_INPUT_COLLISION" in result.stderr
    assert {path: path.read_bytes() for path in protected} == before
    assert not (staging / "execution-result.json").exists()
    assert not (staging / "graph-update").exists()


def test_graphify_python_symlink_preserves_virtual_environment(tmp_path: Path):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
    write_json(tmp_path / "approvals.json", approval_document(plan))
    write_json(tmp_path / "quality.json", quality_document(plan))
    graph_env = tmp_path / "graph-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(graph_env)],
        check=True,
        capture_output=True,
    )
    site = (
        graph_env
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    stub = install_graphify_stub(tmp_path)
    shutil.copytree(stub, site, dirs_exist_ok=True)
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )
    arguments[arguments.index("--graphify-python") + 1] = str(graph_env / "bin/python")

    result = sync(*arguments)

    assert result.returncode == 0, result.stderr
    report = json.loads((staging / "execution-result.json").read_text())
    assert report["counts"]["graph_successes"] == 1
    assert report["quality_gate"]["status"] == "passed"


@pytest.mark.parametrize("recover_report", [False, True])
@pytest.mark.parametrize("changed_field", ["where", "required", "satisfied"])
def test_completed_execution_rechecks_changed_quality_assertions(
    tmp_path: Path, recover_report: bool, changed_field: str
):
    plan, snapshot_path, state_path, staging, graph_dir = candidate_plan(tmp_path)
    graph_dir.mkdir()
    write_json(graph_dir / "graph.json", {"nodes": [], "links": []})
    write_json(tmp_path / "approvals.json", approval_document(plan))
    quality = quality_document(plan)
    write_json(tmp_path / "quality.json", quality)
    stub = install_graphify_stub(tmp_path)
    log = tmp_path / "graphify-calls.jsonl"
    env = {
        "PYTHONPATH": os.pathsep.join([str(stub), str(ROOT / "src")]),
        "GRAPHIFY_STUB_LOG": str(log),
    }
    arguments = execute_arguments(
        tmp_path, snapshot_path, state_path, staging, graph_dir
    )
    first = sync(*arguments, env=env)
    assert first.returncode == 0, first.stderr
    report_path = staging / "execution-result.json"
    if recover_report:
        report_path.unlink()
    if changed_field == "where":
        quality["cases"][0]["where"] = {"label": "Unobserved updated rule"}
    elif changed_field == "required":
        quality["cases"][0]["required"]["rationale"].append("unobserved condition")
    else:
        quality["cases"][0]["required"]["rationale"].append("Refund")
    write_json(tmp_path / "quality.json", quality)
    protected = [graph_dir / "graph.json", state_path, log]
    if not recover_report:
        protected.append(report_path)
    before = {path: path.read_bytes() for path in protected}

    repeated = sync(*arguments, env=env)

    if changed_field == "satisfied":
        assert repeated.returncode == 0, repeated.stderr
    else:
        assert repeated.returncode != 0
        assert "quality case failed: new-limit" in repeated.stderr
    assert {path: path.read_bytes() for path in protected} == before
    assert report_path.exists() is (not recover_report or changed_field == "satisfied")
