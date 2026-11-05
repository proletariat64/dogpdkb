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

    def build(corpus, graph_dir, staging, *args, base_dir=None):
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


@pytest.mark.parametrize("change_point", ["during_build", "before_publish"])
@pytest.mark.parametrize("artifact", ["graph.json", ".graphify_labels.json"])
def test_rejects_base_changes_before_first_publication(
    tmp_path, monkeypatch, change_point, artifact
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a-[1].md").write_text("source")
    graph = corpus / "graphify-out"
    graph.mkdir()
    original = {"nodes": [{"id": "a"}], "links": []}
    labels = {"1": "original"}
    update_graph.write_json(graph / "graph.json", original)
    update_graph.write_json(graph / ".graphify_labels.json", labels)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"exported": [{"id": 1, "file": "a-[1].md"}]}))
    staging = tmp_path / "stage"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_graph",
            "-i",
            str(corpus),
            "--manifest",
            str(manifest),
            "--staging",
            str(staging),
        ],
    )
    external = (
        {"nodes": [{"id": "b"}], "links": []}
        if artifact == "graph.json"
        else {"1": "external"}
    )

    def build(_corpus, graph_dir, stage, *args, base_dir=None):
        assert update_graph.load_json(base_dir / "graph.json") == original
        assert update_graph.load_json(base_dir / ".graphify_labels.json") == labels
        candidate = stage / "candidate"
        candidate.mkdir()
        update_graph.write_json(
            candidate / "graph.json",
            {"nodes": [{"id": "a"}, {"id": "delta"}], "links": []},
        )
        update_graph.write_json(candidate / ".graphify_labels.json", {"1": "candidate"})
        if change_point == "during_build":
            update_graph.write_json(graph_dir / artifact, external)
        return candidate, {}

    monkeypatch.setattr(update_graph, "build_candidate", build)
    if change_point == "before_publish":
        publish = update_graph.publish_candidate
        with monkeypatch.context() as patch:

            def interrupted(*args):
                raise OSError("injected before publish")

            patch.setattr(update_graph, "publish_candidate", interrupted)
            with pytest.raises(OSError, match="before publish"):
                update_graph.main()
        monkeypatch.setattr(update_graph, "publish_candidate", publish)
        update_graph.write_json(graph / artifact, external)
    with pytest.raises(RuntimeError, match="changed concurrently"):
        update_graph.main()
    assert update_graph.load_json(graph / artifact) == external
    assert update_graph.load_json(staging / "graph-backup/graph.json") == original
    assert (
        update_graph.load_json(staging / "graph-backup/.graphify_labels.json") == labels
    )
