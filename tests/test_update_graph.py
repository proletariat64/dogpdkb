"""Graph publication retries preserve the validated candidate and original backup."""

import json
import sys

import pytest

from scripts import update_graph


@pytest.fixture
def graph_job(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a-[1].md").write_text("source")
    graph = corpus / "graphify-out"
    graph.mkdir()
    original = {"nodes": [], "links": [], "captured_at": "original"}
    update_graph.write_json(graph / "graph.json", original)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"exported": [{"id": 1, "file": "a-[1].md"}]}))
    staging = tmp_path / "graph-update"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_graph",
            "--input",
            str(corpus),
            "--manifest",
            str(manifest),
            "--staging",
            str(staging),
        ],
    )
    builds = []

    def build(corpus, graph_dir, staging, *args):
        builds.append(1)
        candidate = staging / "candidate"
        candidate.mkdir(exist_ok=True)
        update_graph.write_json(
            candidate / "graph.json",
            {
                "nodes": [],
                "links": [],
                "captured_at": f"attempt-{len(builds)}",
            },
        )
        update_graph.write_json(candidate / "new-artifact.json", {"label": "new"})
        return candidate, {"status": "candidate_validated"}

    monkeypatch.setattr(update_graph, "build_candidate", build)
    write = update_graph.write_json

    def fail_result(path, payload):
        if path == staging / "result.json":
            raise OSError("injected result failure after publish")
        write(path, payload)

    monkeypatch.setattr(update_graph, "write_json", fail_result)
    with pytest.raises(OSError, match="result failure"):
        update_graph.main()
    monkeypatch.setattr(update_graph, "write_json", write)
    return corpus, graph, staging, builds, original


def test_resume_after_successful_publish_reuses_candidate(graph_job):
    _corpus, graph, staging, builds, original = graph_job
    published = (graph / "graph.json").read_bytes()
    update_graph.main()
    assert len(builds) == 1
    assert (graph / "graph.json").read_bytes() == published
    assert json.loads((staging / "result.json").read_text())["status"] == "completed"
    assert json.loads((staging / "graph-backup/graph.json").read_text()) == original
    assert not (staging / "graph-backup/new-artifact.json").exists()


def test_resume_still_rejects_external_graph_changes(graph_job):
    _corpus, graph, _staging, _builds, _original = graph_job
    (graph / "graph.json").write_text('{"nodes": ["external"]}')
    with pytest.raises(RuntimeError, match="changed concurrently"):
        update_graph.main()
    assert json.loads((graph / "graph.json").read_text()) == {"nodes": ["external"]}


def test_resume_rejects_changed_source_documents(graph_job):
    corpus, _graph, _staging, builds, _original = graph_job
    (corpus / "a-[1].md").write_text("modified source")
    with pytest.raises(RuntimeError, match="candidate inputs changed"):
        update_graph.main()
    assert len(builds) == 1
