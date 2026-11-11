"""Wash cleanup, historical migration and deployment, through the installed CLI.

Black-box seams: ``dogpdkb sync`` (automatic cleanup at a finished batch),
``dogpdkb wash`` (inventory/migration of historical work directories) and
``dogpdkb anomalies`` (necessary inputs stay readable after cleanup). Every wash
directory a cleanup test deletes is constructed under ``tmp_path``; the real
corpus, graph and private artifacts are never touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_unified_compile import CompileWorkspace
from tests.test_unified_sync import DOCUMENTS, Workspace, read_json, write_json


class WashWorkspace(CompileWorkspace):
    """A full batch workspace with explicit durable results and OCR cache."""

    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path, DOCUMENTS)
        text = self.config.read_text(encoding="utf-8").replace(
            'web="web"', 'web="web"\nresults="results"\nocr_cache="ocr-cache"'
        )
        self.config.write_text(text, encoding="utf-8")
        self.results = tmp_path / "results" / "fund"
        self.ocr_cache = tmp_path / "ocr-cache" / "fund"


@pytest.fixture
def workspace(tmp_path: Path) -> WashWorkspace:
    return WashWorkspace(tmp_path)


def finished_batch(workspace: Workspace) -> Path:
    """One batch that reaches a terminal result; return its work directory."""
    completed = workspace.sync()
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["status"] in {"completed", "partial"}, summary
    return Path(summary["run_dir"])


def test_a_finished_batch_cleans_earlier_finished_work_directories(
    workspace: Workspace,
):
    first = finished_batch(workspace)
    assert first.is_dir()
    first_result = workspace.results / "runs" / f"{first.name}.json"
    assert first_result.is_file()

    second = finished_batch(workspace)

    # Only the most recent finished batch keeps its wash work directory.
    assert not first.exists()
    assert second.is_dir()
    assert workspace.runs() == [second]
    # The durable history of both batches survives the wash cleanup.
    assert read_json(first_result)["run_id"] == first.name
    assert (workspace.results / "runs" / f"{second.name}.json").is_file()


def canonical_fingerprints(workspace: Workspace) -> tuple[dict, dict]:
    """Published corpus text plus graph structure and success versions.

    Timestamps and stub report churn are excluded: the invariant is that cleanup
    removes no published knowledge and advances no success version.
    """
    files = {
        str(path.relative_to(workspace.corpus)): path.read_bytes()
        for path in sorted(workspace.corpus.glob("*.md"))
    }
    graph = read_json(workspace.corpus / "graphify-out" / "graph.json")
    files["graph-nodes"] = sorted(
        (node["id"], node.get("source_file")) for node in graph["nodes"]
    )
    files["graph-links"] = sorted(
        (link["source"], link["target"], link.get("relation"))
        for link in graph.get("links", [])
    )
    state = read_json(workspace.corpus / ".sync-state.json")
    published = {
        doc_id: {
            "corpus": {
                key: (row.get("corpus") or {}).get(key)
                for key in ("status", "version", "file")
            },
            "graph": {
                key: (row.get("graph") or {}).get(key) for key in ("status", "version")
            },
        }
        for doc_id, row in state["books"]["fund"]["documents"].items()
    }
    return files, published


def state_path_references(workspace: Workspace) -> list[Path]:
    """Every absolute file reference the canonical state still holds."""
    state = read_json(workspace.corpus / ".sync-state.json")
    found = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value.startswith("/"):
            path = Path(value)
            if path.suffix or path.is_dir():
                found.append(path)

    walk(state)
    return found


def test_cleanup_leaves_the_canonical_corpus_graph_and_state_intact(
    workspace: Workspace,
):
    finished_batch(workspace)
    # A no-update batch reaches the same steady state and cleans the first run.
    finished_batch(workspace)
    before = canonical_fingerprints(workspace)
    assert before[0], "expected a published corpus before cleanup"

    finished_batch(workspace)

    assert canonical_fingerprints(workspace) == before
    state = read_json(workspace.corpus / ".sync-state.json")
    assert state["active_run"] is None
    # No canonical reference is left pointing into a removed work directory.
    for path in state_path_references(workspace):
        assert path.exists(), path


def write_run_state(run: Path, **changes: Any) -> dict[str, Any]:
    state = read_json(run / "run.json")
    state.update(changes)
    write_json(run / "run.json", state)
    return state


def clone_run(source: Path, target: Path, **changes: Any) -> Path:
    """A constructed wash work directory copied from a real finished batch."""
    import shutil

    shutil.copytree(source, target)
    state = read_json(target / "run.json")
    state.update(run_id=target.name, run_dir=str(target))
    state.update(changes)
    write_json(target / "run.json", state)
    return target
