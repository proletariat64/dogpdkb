"""Offline CLI acceptance for the sync phase and resume contract."""

import json
import sys
from pathlib import Path

import pytest

from scripts.smoke_install import REMOTE_CLI
from tests.test_orchestrator_ocr_batch_cli import _native_instance
from tests.test_sync_source_selection_cli import YUQUE, cli, instance, run_data


def setup_local(root):
    config = instance(root, [YUQUE])
    source = root / "inbox"
    source.mkdir()
    fake = root / "tools.py"
    fake.write_text(REMOTE_CLI)
    with config.open("a") as stream:
        for name in ("mdocr", "graphify"):
            stream.write(f"{name}={json.dumps([sys.executable, str(fake), name])}\n")
    return config, source


@pytest.mark.parametrize("frozen_budget", [None, 512])
def test_each_stop_resumes_the_same_frozen_run(tmp_path, frozen_budget):
    config, source = setup_local(tmp_path)
    (source / "doc.md").write_text("# Frozen body\n")
    original_config = config.read_text()
    if frozen_budget is not None:
        config.write_text(
            original_config + f"\n[graphify]\ntoken_budget={frozen_budget}\n"
        )
    args = ("sync", "--config", config)
    path, collect = run_data(
        cli(tmp_path, *args, "--dir", source, "--stop-after", "collect")
    )
    assert collect["status"] == "paused"
    assert "prepared_inputs" in collect["artifacts"]
    assert "semantic" not in collect["artifacts"]
    source.rename(tmp_path / "moved")
    config.write_text(
        original_config
        + '\n[ocr]\nmode="paddle"\nrequest_budget=0\n'
        + ("\n[graphify]\ntoken_budget=1024\n" if frozen_budget is None else "")
    )
    resume = (*args, "--resume", path)
    _, plan = run_data(cli(tmp_path, *resume, "--stop-after", "plan"))
    assert plan["status"] == "paused"
    assert "plan" in plan["artifacts"] and "ocr_plan" not in plan["artifacts"]
    assert (
        json.loads(Path(plan["artifacts"]["plan"]).read_bytes())["ocr_policy"]["mode"]
        == "llm"
    )
    _, ocr = run_data(cli(tmp_path, *resume, "--stop-after", "ocr"))
    assert ocr["status"] == "paused"
    assert "ocr_plan" in ocr["artifacts"] and "manifest" not in ocr["artifacts"]
    corpus_dir = tmp_path / "corpus/funds"
    assert not list(corpus_dir.glob("*.md"))
    _, corpus = run_data(cli(tmp_path, *resume, "--stop-after", "corpus"))
    assert corpus["status"] == "paused"
    [body] = corpus_dir.glob("*.md")
    assert body.read_text() == "# Frozen body\n"
    assert not (corpus_dir / "graphify-out/graph.json").exists()
    _, graph = run_data(cli(tmp_path, *resume, "--stop-after", "graph"))
    assert graph["status"] == "completed"
    assert (corpus_dir / "graphify-out/graph.json").is_file()
    assert all(
        Path(r["artifacts"]["plan"]) == Path(plan["artifacts"]["plan"])
        for r in (ocr, corpus)
    )
    calls = [
        json.loads(l) for l in (tmp_path / "commands.jsonl").read_text().splitlines()
    ]
    assert [c[:2] for c in calls] == [
        ["mdocr", "plan"],
        ["mdocr", "run"],
        ["graphify", "extract"],
    ]
    if frozen_budget is None:
        assert "--token-budget" not in calls[-1]
    else:
        assert calls[-1][-2:] == ["--token-budget", str(frozen_budget)]


def test_changed_plan_handoff_fails_before_ocr(tmp_path):
    config, source = setup_local(tmp_path)
    (source / "doc.md").write_text("# Frozen body\n")
    args = ("sync", "--config", config)
    path, paused = run_data(
        cli(tmp_path, *args, "--dir", source, "--stop-after", "plan")
    )
    plan = Path(paused["artifacts"]["plan"])
    altered = json.loads(plan.read_bytes())
    altered["ocr_policy"]["request_budget"] = 99
    plan.write_text(json.dumps(altered))
    resumed = cli(tmp_path, *args, "--resume", path)
    assert resumed.returncode == 1, resumed.stderr
    assert "plan checkpoint changed" in resumed.stderr
    assert not (tmp_path / "commands.jsonl").exists()
    assert not list((tmp_path / "corpus/funds").glob("*.md"))


def test_old_ocr_handoff_publishes_only_nonconflicting_documents(tmp_path):
    config, source = setup_local(tmp_path)
    a, b = source / "a.md", source / "b.md"
    a.write_text("# Older A\n")
    b.write_text("# Independent B\n")
    args = ("sync", "--config", config)
    old, _ = run_data(cli(tmp_path, *args, "--dir", source, "--stop-after", "ocr"))
    a.write_text("# Newer A\n")
    b.unlink()
    _, newer = run_data(cli(tmp_path, *args, "--dir", source, "--stop-after", "graph"))
    assert newer["status"] == "completed"
    _, corpus = run_data(
        cli(tmp_path, *args, "--resume", old, "--stop-after", "corpus")
    )
    assert {p.read_text() for p in (tmp_path / "corpus/funds").glob("*.md")} == {
        "# Newer A\n",
        "# Independent B\n",
    }
    manifest = json.loads(Path(corpus["artifacts"]["manifest"]).read_bytes())
    assert len(manifest["documents"]) == 1
    assert [item["reason"] for item in corpus["failures"]] == ["STALE_DOCUMENT"]


def test_corpus_handoff_refuses_new_graph_generation(tmp_path):
    config, source = setup_local(tmp_path)
    (source / "a.md").write_text("# Older A\n")
    args = ("sync", "--config", config)
    old, _ = run_data(cli(tmp_path, *args, "--dir", source, "--stop-after", "corpus"))
    (source / "a.md").write_text("# Newer A\n")
    _, newer = run_data(cli(tmp_path, *args, "--dir", source))
    assert newer["status"] == "completed"
    resumed = cli(tmp_path, *args, "--resume", old)
    assert resumed.returncode == 2, resumed.stderr
    assert (tmp_path / "commands.jsonl").read_text().count(
        '["graphify", "extract"'
    ) == 1
    assert (
        json.loads(old.read_bytes())["failures"][-1]["reason"] == "STALE_GRAPH_HANDOFF"
    )


@pytest.mark.parametrize("newer_graph", [False, True])
def test_resume_after_unsealed_corpus_reuses_frozen_graph_baseline(
    tmp_path, monkeypatch, newer_graph
):
    from dogpdkb import apply, sync

    config, source = setup_local(tmp_path)
    (source / "a.md").write_text("# Published A\n")
    args = ("--config", str(config))
    with monkeypatch.context() as patch:

        def power_loss(*_):
            raise RuntimeError("lost power before corpus checkpoint")

        patch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
        patch.setattr(apply, "seal_corpus", power_loss)
        with pytest.raises(RuntimeError, match="lost power"):
            sync.main([*args, "--dir", str(source)])
    [run] = (tmp_path / "runs/funds").glob("run-*/run.json")
    interrupted = json.loads(run.read_bytes())
    assert "manifest" in interrupted["artifacts"]
    assert "corpus" not in interrupted["checkpoints"]

    public = tmp_path / "corpus/funds/graphify-out"
    newer = b'{"nodes":[{"id":"lib","source_file":"lib.py"}],"links":[]}'
    if newer_graph:
        (public / "graph.json").write_bytes(newer)
        (public / "manifest.json").write_text("{}")
    resumed = cli(tmp_path, "sync", *args, "--resume", run)
    assert resumed.returncode == (2 if newer_graph else 0), resumed.stderr
    calls = (tmp_path / "commands.jsonl").read_text()
    assert calls.count('"graphify", "extract"') == (0 if newer_graph else 1)
    if newer_graph:
        assert (public / "graph.json").read_bytes() == newer
        assert (
            json.loads(run.read_bytes())["failures"][-1]["reason"]
            == "STALE_GRAPH_HANDOFF"
        )
    else:
        assert (public / "graph.json").is_file()


def test_catchup_only_sends_unattempted_document_and_recompiles_enrichment(tmp_path):
    config, _ = _native_instance(tmp_path)
    source = tmp_path / "inbox"
    source.mkdir()
    for n in (1, 2):
        image = f"diagram-{n}.png"
        (source / image).write_bytes(f"synthetic image bytes {n}".encode())
        (source / f"doc{n}.md").write_text(f"# Document {n}\n\n![diagram]({image})\n")
    tools = tmp_path / "graph-fixture.py"
    tools.write_text(REMOTE_CLI)
    config.write_text(
        config.read_text().replace(
            "[tools]\n",
            f"[tools]\ngraphify={json.dumps([sys.executable, str(tools), 'graphify'])}\n",
        )
    )
    args = ("sync", "--config", config, "--dir", source)
    first = cli(tmp_path, *args)
    assert first.returncode == 2, first.stderr
    first_run = json.loads(Path(json.loads(first.stdout)["run"]).read_bytes())
    state = tmp_path / "state/funds/state.json"
    first_docs = json.loads(state.read_bytes())["documents"]
    (complete,) = [
        key
        for key, row in first_docs.items()
        if row["corpus"]["ocr_status"] == "completed"
    ]
    (held,) = [
        key
        for key, row in first_docs.items()
        if row["corpus"]["ocr_status"] == "unattempted"
    ]
    assert first_run["status"] == "partial"
    config.write_text(
        config.read_text().replace("request_budget=1", "request_budget=0")
    )
    still_held = cli(tmp_path, *args)
    assert still_held.returncode == 2, still_held.stderr
    assert (
        json.loads(state.read_bytes())["documents"][held]["corpus"]
        == first_docs[held]["corpus"]
    )
    config.write_text(
        config.read_text().replace("request_budget=0", "request_budget=1")
    )
    after, resumed = run_data(cli(tmp_path, *args, "--stop-after", "plan"))
    plan = json.loads(Path(resumed["artifacts"]["plan"]).read_bytes())
    assert [row["document_id"] for row in plan["documents"]] == [held]
    _, final = run_data(cli(tmp_path, "sync", "--config", config, "--resume", after))
    assert final["status"] == "completed"
    new_docs = json.loads(state.read_bytes())["documents"]
    assert new_docs[complete] == first_docs[complete]
    assert new_docs[held]["corpus"]["ocr_status"] == "completed"
    assert new_docs[held]["graph"]["run_id"] == final["run_id"]
    assert len((tmp_path / "requests").read_text().splitlines()) == 2
