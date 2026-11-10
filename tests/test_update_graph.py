"""Graph publication retries preserve the validated candidate and original backup."""

import json
import sys
from pathlib import Path

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
    manifest.write_text(
        json.dumps(
            {
                "exported": [
                    {
                        "id": 1,
                        "file": "a-[1].md",
                        "source_version": {
                            "field": "updated_at",
                            "value": "2026-11-10T10:00:00+08:00",
                            "utc": "2026-11-10T10:00:00+08:00",
                        },
                    }
                ]
            }
        )
    )
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
    corpus, graph, staging, builds, original = graph_job
    canonical = json.loads((corpus / ".sync-state.json").read_text())
    record = canonical["books"]["adhoc"]["documents"]["1"]
    assert record["steps"]["graph_candidate"]["status"] == "completed"
    assert not record.get("graph")
    published = (graph / "graph.json").read_bytes()
    update_graph.main()
    assert len(builds) == 1
    assert (graph / "graph.json").read_bytes() == published
    assert json.loads((staging / "result.json").read_text())["status"] == "completed"
    assert json.loads((staging / "graph-backup/graph.json").read_text()) == original
    assert not (staging / "graph-backup/new-artifact.json").exists()
    canonical = json.loads((corpus / ".sync-state.json").read_text())
    assert canonical["books"]["adhoc"]["documents"]["1"]["graph"]["status"] == "covered"
    assert (
        canonical["books"]["adhoc"]["documents"]["1"]["graph"]["version"]["field"]
        == "updated_at"
    )
    frozen = Path(canonical["books"]["adhoc"]["documents"]["1"]["graph"]["raw"]["path"])
    assert frozen == staging / "source-snapshots/a-[1].md"
    (corpus / "a-[1].md").write_text("later corpus version")
    assert frozen.read_text() == "source"
    assert canonical["active_run"] is None


def test_manifest_version_anomaly_blocks_direct_graph_update(graph_job):
    _corpus, graph, staging, builds, _original = graph_job
    manifest = staging.parent / "manifest.json"
    payload = json.loads(manifest.read_text())
    payload["exported"][0]["anomalies"] = ["detail_newer_than_listing"]
    manifest.write_text(json.dumps(payload))
    before = (graph / "graph.json").read_bytes()
    with pytest.raises(ValueError, match="version anomalies"):
        update_graph.main()
    assert (graph / "graph.json").read_bytes() == before
    assert builds == [1]


def test_applied_graph_deletion_clears_only_its_pending_reason(graph_job, monkeypatch):
    corpus, _graph, staging, _builds, _original = graph_job
    update_graph.main()
    path = corpus / ".sync-state.json"
    state = json.loads(path.read_text())
    state["books"]["adhoc"]["documents"]["1"]["pending"] = [
        "GRAPH_DELETION_REQUIRED",
        "review_pending",
    ]
    path.write_text(json.dumps(state))
    (corpus / "a-[1].md").unlink()
    manifest = staging.parent / "delete-manifest.json"
    manifest.write_text(
        json.dumps({"exported": [], "deleted": [{"id": 1, "local_file": "a-[1].md"}]})
    )
    args = list(sys.argv)
    args[args.index("--manifest") + 1] = str(manifest)
    args[args.index("--staging") + 1] = str(staging.parent / "delete-stage")
    monkeypatch.setattr(sys, "argv", args)
    update_graph.main()
    record = json.loads(path.read_text())["books"]["adhoc"]["documents"]["1"]
    assert record["graph"]["status"] == "deleted"
    assert record["pending"] == ["review_pending"]


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


def test_concurrent_publishers_cannot_overwrite_success(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    graph = tmp_path / "graph"
    graph.mkdir()
    update_graph.write_json(graph / "graph.json", {"version": "base"})
    jobs = []
    for name in ("first", "second"):
        stage = tmp_path / name
        stage.mkdir()
        update_graph.prepare_base(graph, stage)
        candidate = stage / "candidate"
        candidate.mkdir()
        update_graph.write_json(candidate / "graph.json", {"version": name})
        jobs.append((candidate, stage))
    entered = Event()
    release = Event()
    second_started = Event()
    copy = update_graph.atomic_copy

    def pause_first(source, destination):
        if source == jobs[0][0] / "graph.json":
            entered.set()
            assert release.wait(5)
        copy(source, destination)

    monkeypatch.setattr(update_graph, "atomic_copy", pause_first)

    def publish_second():
        second_started.set()
        update_graph.publish_candidate(jobs[1][0], graph, jobs[1][1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            update_graph.publish_candidate, jobs[0][0], graph, jobs[0][1]
        )
        assert entered.wait(5)
        second = pool.submit(publish_second)
        try:
            assert second_started.wait(5)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            release.set()
        first.result(timeout=5)
        with pytest.raises(RuntimeError, match="changed concurrently"):
            second.result(timeout=5)
    assert update_graph.load_json(graph / "graph.json") == {"version": "first"}
    assert sorted(path.name for path in graph.iterdir()) == ["graph.json"]


def test_explicit_rebase_keeps_corpus_manifest_and_rebuilds_candidate(
    graph_job, monkeypatch
):
    corpus, graph, staging, builds, _original = graph_job
    manifest = staging.parent / "manifest.json"
    manifest_bytes = manifest.read_bytes()
    update_graph.write_json(
        graph / "graph.json", {"nodes": [], "links": [], "captured_at": "external"}
    )
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--rebase"])
    update_graph.main()
    assert len(builds) == 2
    assert (
        update_graph.load_json(staging / "graph-backup/graph.json")["captured_at"]
        == "external"
    )
    assert update_graph.load_json(graph / "graph.json")["captured_at"] == "attempt-2"
    assert manifest.read_bytes() == manifest_bytes
    assert (corpus / "a-[1].md").read_text() == "source"


@pytest.mark.parametrize("body", ["", " \n\t"])
def test_blank_document_removes_old_semantics_without_extraction(
    tmp_path, monkeypatch, body
):
    from types import ModuleType

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    filename = "blank-[1].md"
    (corpus / filename).write_text(body)
    graph_dir = corpus / "graphify-out"
    graph_dir.mkdir()
    untouched = {"id": "outside", "label": "Keep", "source_file": "outside-[2].md"}
    old = {
        "directed": True,
        "graph": {},
        "nodes": [
            {"id": "stale", "label": "Removed text", "source_file": "old-[1].md"},
            untouched,
        ],
        "links": [
            {"source": "outside", "target": "stale", "source_file": "outside-[2].md"}
        ],
        "hyperedges": [],
    }
    update_graph.write_json(graph_dir / "graph.json", old)
    update_graph.write_json(
        graph_dir / "manifest.json",
        {"old-[1].md": {}, "outside-[2].md": {"keep": True}},
    )
    staging = tmp_path / "stage"
    staging.mkdir()
    base_dir = update_graph.prepare_base(graph_dir, staging)

    def unexpected_extract(*args):
        pytest.fail("blank sources must not invoke semantic extraction")

    monkeypatch.setattr(update_graph, "extract_semantic", unexpected_extract)
    monkeypatch.setattr(update_graph, "analysis_artifacts", lambda *args: {})
    detect = ModuleType("graphify.detect")

    def save_manifest(files, *, manifest_path, kind, root):
        assert files == {"document": [str(corpus / filename)]}
        manifest = update_graph.load_json(Path(manifest_path))
        manifest[filename] = {"kind": kind}
        update_graph.write_json(Path(manifest_path), manifest)

    detect.save_manifest = save_manifest
    monkeypatch.setitem(sys.modules, "graphify.detect", detect)
    candidate, _audit = update_graph.build_candidate(
        corpus,
        graph_dir,
        staging,
        [filename],
        ["old-[1].md"],
        [],
        {},
        base_dir=base_dir,
    )
    result = update_graph.load_json(candidate / "graph.json")
    assert result["nodes"] == [untouched]
    assert result["links"] == []
    manifest = update_graph.load_json(candidate / "manifest.json")
    assert filename in manifest
    assert "old-[1].md" not in manifest
    assert manifest["outside-[2].md"] == {"keep": True}


@pytest.mark.parametrize("change", ["update", "empty", "delete"])
def test_rebased_delta_removes_third_filename_for_same_document(
    tmp_path, monkeypatch, change
):
    from pathlib import Path
    from types import ModuleType

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    current = "current-[1].md"
    if change != "delete":
        (corpus / current).write_text("new" if change == "update" else "")
    graph = corpus / "graphify-out"
    graph.mkdir()
    untouched = {"id": "other", "label": "Keep", "source_file": "other-[2].md"}
    baseline = {
        "directed": True,
        "graph": {},
        "nodes": [
            {"id": "stale", "label": "Stale concept", "source_file": "third-[1].md"},
            untouched,
        ],
        "links": [
            {"source": "stale", "target": "other", "source_file": "third-[1].md"}
        ],
        "hyperedges": [{"nodes": ["stale", "other"], "source_file": "third-[1].md"}],
    }
    update_graph.write_json(graph / "graph.json", baseline)
    update_graph.write_json(
        graph / "manifest.json", {"third-[1].md": {}, "other-[2].md": {"keep": True}}
    )
    staging = tmp_path / "stage"
    staging.mkdir()
    base = update_graph.prepare_base(graph, staging)
    monkeypatch.setattr(update_graph, "analysis_artifacts", lambda *args: {})
    monkeypatch.setattr(
        update_graph,
        "extract_semantic",
        lambda *args: (
            {
                "nodes": [
                    {"id": "new", "label": "New concept", "source_file": current}
                ],
                "edges": [],
                "hyperedges": [],
            },
            {"input_tokens": 0, "output_tokens": 0},
        ),
    )
    detect = ModuleType("graphify.detect")

    def save_manifest(files, *, manifest_path, **kwargs):
        path = Path(manifest_path)
        payload = update_graph.load_json(path)
        payload.update({Path(name).name: {} for name in files["document"]})
        update_graph.write_json(path, payload)

    detect.save_manifest = save_manifest
    monkeypatch.setitem(sys.modules, "graphify.detect", detect)
    candidate, _ = update_graph.build_candidate(
        corpus,
        graph,
        staging,
        [] if change == "delete" else [current],
        ["original-[1].md"],
        ["original-[1].md"] if change == "delete" else [],
        {},
        base_dir=base,
    )
    result = update_graph.load_json(candidate / "graph.json")
    assert "stale" not in {node["id"] for node in result["nodes"]}
    assert untouched in result["nodes"]
    assert result["links"] == []
    assert result["hyperedges"] == []
    assert len(result["nodes"]) == (2 if change == "update" else 1)
    manifest = update_graph.load_json(candidate / "manifest.json")
    assert "third-[1].md" not in manifest
    assert manifest["other-[2].md"] == {"keep": True}


@pytest.mark.parametrize("cached_candidate", [False, True])
def test_rebased_deletion_rejects_restored_document(
    tmp_path, monkeypatch, cached_candidate
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    graph = corpus / "graphify-out"
    graph.mkdir()
    live_graph = {
        "nodes": [{"id": "restored", "source_file": "restored-[1].md"}],
        "links": [],
    }
    update_graph.write_json(graph / "graph.json", live_graph)
    update_graph.write_json(
        graph / "manifest.json", {"restored-[1].md": {"keep": True}}
    )
    manifest = tmp_path / "manifest.json"
    update_graph.write_json(
        manifest, {"deleted": [{"id": 1, "local_file": "deleted-[1].md"}]}
    )
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
            "--rebase",
        ],
    )
    restored = corpus / "restored-[1].md"
    timestamp = corpus / ".sync-state.json"
    timestamp.write_text(
        json.dumps(
            {
                "version": 2,
                "active_run": None,
                "books": {
                    "adhoc": {
                        "documents": {
                            "1": {
                                "baseline": {"content_updated_at": "2026-11-06"},
                                "pending": [],
                                "steps": {},
                            }
                        }
                    }
                },
            }
        )
    )
    if cached_candidate:

        def build(*args, **kwargs):
            candidate = staging / "candidate"
            candidate.mkdir()
            update_graph.write_json(
                candidate / "graph.json", {"nodes": [], "links": []}
            )
            return candidate, {}

        monkeypatch.setattr(update_graph, "build_candidate", build)
        monkeypatch.setattr(
            update_graph,
            "publish_candidate",
            lambda *args: (_ for _ in ()).throw(OSError("before publish")),
        )
        with pytest.raises(OSError, match="before publish"):
            update_graph.main()
        monkeypatch.setattr(sys, "argv", sys.argv[:-1])
        monkeypatch.setattr(
            update_graph,
            "publish_candidate",
            lambda *args: pytest.fail("stale candidate published"),
        )
    restored.write_text("restored content")
    with pytest.raises(RuntimeError, match="deleted documents restored in corpus"):
        update_graph.main()
    assert update_graph.load_json(graph / "graph.json") == live_graph
    assert update_graph.load_json(graph / "manifest.json") == {
        "restored-[1].md": {"keep": True}
    }
    assert restored.read_text() == "restored content"
    document = json.loads(timestamp.read_text())["books"]["adhoc"]["documents"]["1"]
    assert document["baseline"]["content_updated_at"] == "2026-11-06"
    assert "graph" not in document["steps"]
