import json
from pathlib import Path

import pytest

from scripts import sync_knowledge_base as pipeline


def fake_runner(staging: Path, calls: list[str], fail_graph: bool = False):
    def run(command: list[str]):
        script = Path(command[1]).name
        calls.append(script)
        if script == "get_sync_updates.py":
            output = Path(command[command.index("-o") + 1])
            output.mkdir(parents=True, exist_ok=True)
            (output / "sync-updates-uat-20261102.json").write_text(
                json.dumps(
                    {
                        "new": [],
                        "updated": [],
                        "renamed": [],
                        "moved": [],
                        "deleted": [],
                    }
                )
            )
        elif script == "apply_sync_updates.py":
            assert command[command.index("--cutoff") + 1] == "2026-01-01"
            (staging / "manifest.json").write_text(
                json.dumps({"exported": [], "deleted": [], "skipped": []})
            )
        elif script == "update_graph.py":
            if fail_graph:
                raise RuntimeError("injected graph failure")
            target = staging / "graph-update" / "result.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"status": "completed"}))
        elif script == "publish_web.py":
            output = Path(command[command.index("-o") + 1])
            (output / "map").mkdir(parents=True, exist_ok=True)
            (output / "catalog.json").write_text(
                json.dumps({"stats": {"documents": 1, "nodes": 2, "edges": 3}})
            )
            (output / "map" / "map-data.json").write_text("{}")
        else:
            raise AssertionError(script)

    return run


def options(tmp_path: Path):
    return pipeline.PipelineOptions(
        input_dir=tmp_path / "corpus",
        source_url="https://example.invalid/book",
        staging=tmp_path / "staging",
        cutoff=None,
        dry_run=False,
        resume=False,
        graph_dir=tmp_path / "corpus" / "graphify-out",
        web_dir=tmp_path / "web",
        graphify_python=Path("/usr/local/python3/bin/python3.12"),
        skip_graph=False,
        skip_web=False,
    )


def test_pipeline_resumes_after_graph_failure(tmp_path: Path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("doc")
    calls = []

    try:
        pipeline.run_pipeline(
            config, runner=fake_runner(config.staging, calls, fail_graph=True)
        )
    except RuntimeError as error:
        assert "injected graph failure" in str(error)
    else:
        raise AssertionError("graph failure was not propagated")
    assert calls == [
        "get_sync_updates.py",
        "apply_sync_updates.py",
        "update_graph.py",
    ]

    calls.clear()
    config.resume = True
    result = pipeline.run_pipeline(
        config, runner=fake_runner(config.staging, calls, fail_graph=False)
    )

    assert calls == ["update_graph.py", "publish_web.py"]
    assert result["status"] == "completed"
    assert result["stages"] == {
        "plan": "completed",
        "corpus": "completed",
        "graph": "completed",
        "web": "completed",
    }


def test_pipeline_dry_run_stops_after_plan(tmp_path: Path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("doc")
    config.dry_run = True
    calls = []

    result = pipeline.run_pipeline(config, runner=fake_runner(config.staging, calls))

    assert calls == ["get_sync_updates.py"]
    assert result["status"] == "dry_run"
    assert not (config.web_dir / "catalog.json").exists()


def test_rebase_reuses_published_manifest_after_baseline_conflict(tmp_path: Path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("old")
    calls = []
    original = fake_runner(config.staging, calls)
    retained = {
        "exported": [{"id": 1, "file": "new-2026-11-05-[1].md"}],
        "deleted": [],
        "skipped": [],
    }

    def conflict(command):
        original(command) if Path(command[1]).name != "update_graph.py" else None
        if Path(command[1]).name == "apply_sync_updates.py":
            (config.staging / "manifest.json").write_text(json.dumps(retained))
            (config.input_dir / "new-2026-11-05-[1].md").write_text("new")
        if Path(command[1]).name == "update_graph.py":
            raise RuntimeError("graph artifact changed concurrently")

    with pytest.raises(RuntimeError, match="changed concurrently"):
        pipeline.run_pipeline(config, runner=conflict)
    config.resume = True
    config.rebase_graph = True
    calls.clear()

    def recover(command):
        if Path(command[1]).name == "update_graph.py":
            assert "--rebase" in command
            manifest = Path(command[command.index("--manifest") + 1])
            assert json.loads(manifest.read_text()) == retained
        original(command)

    result = pipeline.run_pipeline(config, runner=recover)
    assert calls == ["update_graph.py", "publish_web.py"]
    assert result["status"] == "completed"


def test_rebase_requires_existing_resume(tmp_path: Path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    config.rebase_graph = True
    with pytest.raises(ValueError, match="requires --resume"):
        pipeline.run_pipeline(config, runner=lambda command: pytest.fail(str(command)))
