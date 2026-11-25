"""Native comparison artifacts and cleanup-only resume through the public CLI."""

import json
import os
from pathlib import Path

import pytest

from tests.test_orchestrator_apply_cli import cli
from tests.test_wash_cleanup_cli import instance


def test_native_cleanup_preserves_comparison_and_recovers_a_pruned_run(tmp_path):
    configured = os.environ.get("MDCOMPARE_RUST_BINARY")
    if not configured:
        pytest.skip("set MDCOMPARE_RUST_BINARY to a freshly built mdcomparer")
    binary = Path(configured).resolve(strict=True)
    config, _ = instance(tmp_path)
    with config.open("a") as stream:
        stream.write(f"mdcompare_rust={json.dumps(str(binary))}\n")

    completed = cli("sync", "--config", config)
    assert completed.returncode == 0, completed.stderr
    run = Path(json.loads(completed.stdout)["run"])
    record = json.loads(run.read_bytes())
    assert record["cleanup"]["status"] == "completed"
    assert not (run.parent / "raw").exists()
    generation = record["artifacts"]["semantic_generation"]
    commit = Path(generation["path"])
    assert commit.is_file()
    assert commit.is_relative_to(tmp_path / "state")

    # Older cleanup runs pruned wash, then failed while treating the native
    # generation binding as a pathname. Reproduce that persisted run shape.
    record["cleanup"] = {"status": "failed"}
    plan = record["artifacts"]["plan"] = str(run.parent / "plan.json")
    assert not Path(plan).exists()
    survivor = run.parent / "retained-evidence"
    survivor.write_text("keep unless the retention plan is available")
    run.write_text(json.dumps(record))
    calls = (tmp_path / "commands.jsonl").read_bytes()
    commit_bytes = commit.read_bytes()

    resumed = cli("sync", "--config", config, "--resume", run)
    assert resumed.returncode == 0, resumed.stderr
    recovered = json.loads(run.read_bytes())
    assert recovered["status"] == record["status"] == "completed"
    assert recovered["ended_at"] == record["ended_at"]
    assert recovered["cleanup"] == {
        "status": "completed",
        "removed_artifacts": {"plan": plan},
    }
    assert recovered["artifacts"]["semantic_generation"] == generation
    assert commit.read_bytes() == commit_bytes
    assert survivor.is_file()
    assert (tmp_path / "commands.jsonl").read_bytes() == calls


def test_retry_incomplete_prune_reuses_retention_plan(tmp_path):
    config, tool = instance(tmp_path)
    tool.write_text(
        tool.read_text().replace(
            "    graph=flag('--out')",
            """    blocked=next((root/'runs/funds').glob('run-*'))/'blocked'
    blocked.mkdir()
    (blocked/'file').write_text('temporary')
    blocked.chmod(0o555)
    graph=flag('--out')""",
        )
    )
    completed = cli("sync", "--config", config)
    assert completed.returncode == 0, completed.stderr
    run = Path(json.loads(completed.stdout)["run"])
    (run.parent / "blocked").chmod(0o755)
    record = json.loads(run.read_bytes())
    assert record["cleanup"]["status"] == "failed", completed.stderr
    assert Path(record["artifacts"]["plan"]).is_file()
    calls = (tmp_path / "commands.jsonl").read_bytes()

    resumed = cli("sync", "--config", config, "--resume", run)
    assert resumed.returncode == 0, resumed.stderr
    recovered = json.loads(run.read_bytes())
    assert recovered["cleanup"]["status"] == "completed"
    assert not (run.parent / "blocked").exists()
    assert not Path(record["artifacts"]["plan"]).exists()
    assert (tmp_path / "commands.jsonl").read_bytes() == calls
