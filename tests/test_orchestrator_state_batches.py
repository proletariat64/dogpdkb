"""Batch persistence costs and recovery boundaries with real JSON/files/tools."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dogpdkb import records
from dogpdkb.apply import compare_inputs, record_interruption
from dogpdkb.graph import run_graph
from dogpdkb.observe import report, snapshot
from dogpdkb.records import Run, Store
from dogpdkb.settings import load_config, select_book
from scripts.smoke_install import GRAPHIFY_PREFLIGHT
from tests.test_orchestrator_apply_cli import VERSION, instance, two_sources
from tests.test_orchestrator_graph_cli import fixture as graph_fixture


def comparison(tmp_path, count):
    config, _ = instance(tmp_path)
    settings = load_config(config)
    book = select_book(settings, None)
    store = Store(settings, book)
    run = Run.create(store, "apply")
    rows = []
    for index in range(count):
        identifier = str(index)
        path = tmp_path / f"input-{index}.md"
        path.write_text(f"# Title {index}\n\nCurrent full source.\n")
        store.document(identifier)["raw"] = {"version": VERSION, "path": str(path)}
        rows.append(
            {
                "document_id": identifier,
                "version": VERSION,
                "raw_path": str(path),
                "baseline": {},
            }
        )
    store.save()
    return settings, book, store, run, rows


def count_state_writes(monkeypatch, store):
    writes = []
    original = records.atomic_json

    def write(path, payload, *, durable=False):
        original(path, payload, durable=durable)
        if Path(path) == store.path:
            writes.append(Path(path).stat().st_size)

    monkeypatch.setattr(records, "atomic_json", write)
    return writes


@pytest.mark.parametrize("count", [1, 25])
def test_compare_writes_state_three_times_independent_of_batch_size(
    tmp_path, monkeypatch, count
):
    settings, book, store, run, rows = comparison(tmp_path, count)
    # The child verifies the entire batch was persisted before any tool starts.
    wrapper = tmp_path / "checked_mdcompare.py"
    wrapper.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        f"state=json.loads(Path({str(store.path)!r}).read_text())\n"
        f"assert len(state['documents'])=={count}\n"
        "assert all(d['steps']['compare']['status']=='running' for d in state['documents'].values())\n"
        f"assert json.loads(Path({str(run.path)!r}).read_text())['phase']=='tools'\n"
        "os.execv(sys.executable,[sys.executable,'-m','mdcompare',*sys.argv[1:]])\n"
    )
    settings.tools["mdcompare"] = (sys.executable, str(wrapper))
    writes = count_state_writes(monkeypatch, store)
    # Real public subprocesses import the source in this worktree.
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    with store.lock():
        knowledge, detail, semantic = compare_inputs(settings, book, store, run, rows)
    assert len(writes) == 3
    assert len(knowledge["results"]) == len(detail["results"]) == count
    assert len(semantic["results"]) == count
    reloaded = Store(settings, book)
    for row in rows:
        step = reloaded.step(row["document_id"], "compare")
        assert step is not None and step["status"] == "completed"
    status = snapshot(store, str(run.path))
    assert status["phase"] == "record-results"
    assert "phase=record-results" in report(status)
    run.stage("plan", total=count)
    assert "phase" not in snapshot(store, str(run.path))


def test_failed_attempt_save_prevents_comparison_tool_launch(tmp_path):
    settings, book, store, run, rows = comparison(tmp_path, 2)
    saved = store.path.read_bytes()
    backup = store.path.with_suffix(".backup")
    store.path.rename(backup)
    store.path.symlink_to(backup)
    with pytest.raises(ValueError, match="symlink"):
        compare_inputs(settings, book, store, run, rows)
    assert backup.read_bytes() == saved
    assert not (run.directory / "mdcompare.log").exists()


def test_local_preparation_failure_does_not_mark_external_attempts(tmp_path):
    settings, book, store, run, rows = comparison(tmp_path, 2)
    rows[1]["metadata"] = None
    with pytest.raises(AttributeError):
        compare_inputs(settings, book, store, run, rows)
    reloaded = Store(settings, book)
    assert all(reloaded.step(row["document_id"], "compare") is None for row in rows)


def test_batch_preserves_other_version_blocks_and_rejects_invalid_versions(tmp_path):
    settings, book, store, run, rows = comparison(tmp_path, 2)
    store.complete("0", "compare", "old", "failed", "old-run")
    batch = [
        {
            "doc_id": row["document_id"],
            "name": "compare",
            "version": row["version"],
            "status": "running",
            "run_id": run.id,
        }
        for row in rows
    ]
    before = store.path.read_bytes()
    invalid = [batch[0], {**batch[1], "version": None}]
    with pytest.raises(ValueError):
        store.complete_batch(invalid)
    assert store.path.read_bytes() == before
    old_step = store.step("0", "compare")
    assert old_step is not None and old_step["version"] == "old"
    store.complete_batch(batch)
    assert all(store.suppressed(row["document_id"], "compare", VERSION) for row in rows)
    store.complete_batch({**item, "status": "completed"} for item in batch)
    reloaded = Store(settings, book)
    assert reloaded.suppressed("0", "compare", "old")
    assert not any(
        reloaded.suppressed(row["document_id"], "compare", VERSION) for row in rows
    )


def test_interruption_records_whole_batch_with_one_state_save(tmp_path, monkeypatch):
    settings, book, store, run, rows = comparison(tmp_path, 25)
    store.complete_batch(
        {
            "doc_id": row["document_id"],
            "name": "compare",
            "version": VERSION,
            "status": "running",
            "run_id": run.id,
        }
        for row in rows
    )
    store.attempt("other-run", "export", VERSION, "different-run")
    writes = count_state_writes(monkeypatch, store)
    record_interruption(store, run, 143)
    assert len(writes) == 1
    reloaded = Store(settings, book)
    for row in rows:
        step = reloaded.step(row["document_id"], "compare")
        assert step is not None and step["status"] == "unknown"
    other_step = reloaded.step("other-run", "export")
    assert other_step is not None and other_step["status"] == "running"
    assert len(records.read_json(run.path)["failures"]) == len(rows)


def test_terminating_live_compare_marks_whole_batch_unknown(tmp_path):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    wrapper = tmp_path / "mdcompare.py"
    wrapper.write_text(
        "import json,time\nfrom pathlib import Path\nroot=Path(__file__).parent\n"
        "state=json.loads((root/'state/funds/state.json').read_text())\n"
        "assert all(d['steps']['compare']['status']=='running' for d in state['documents'].values())\n"
        "(root/'compare-started').touch()\ntime.sleep(30)\n"
    )
    config.write_text(
        config.read_text().replace(
            json.dumps([sys.executable, "-m", "mdcompare"]),
            json.dumps([sys.executable, str(wrapper)]),
        )
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "apply",
            "--config",
            str(config),
            "--input",
            str(differences),
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            deadline = time.monotonic() + 10
            while (
                not (tmp_path / "compare-started").exists()
                and time.monotonic() < deadline
            ):
                if process.poll() is not None:
                    break
                time.sleep(0.02)
            assert (tmp_path / "compare-started").exists()
        finally:
            process.terminate()
            process.communicate(timeout=5)
        assert process.returncode == 143
    state = records.read_json(tmp_path / "state/funds/state.json")
    assert all(
        d["steps"]["compare"]["status"] == "unknown"
        for d in state["documents"].values()
    )
    run = records.read_json(next((tmp_path / "runs/funds").glob("*/run.json")))
    assert run["status"] == "interrupted"
    assert len(run["failures"]) == 2


@pytest.mark.parametrize("mode", ["success", "failed", "interrupt"])
@pytest.mark.parametrize("count", [1, 25])
def test_graph_batch_writes_state_twice_for_actual_tool_result(
    tmp_path, monkeypatch, mode, count
):
    config, manifest, _ = graph_fixture(tmp_path)
    settings = load_config(config)
    book = select_book(settings, None)
    store = Store(settings, book)
    run = Run.load(store, manifest.parent)
    data = records.read_json(manifest)
    original = data["documents"][0]
    for index in range(1, count):
        identifier = f"extra-{index}"
        path = book.corpus / f"{identifier}.md"
        path.write_text(f"# Additional source {index}\n")
        witness = tmp_path / f"witness-{index}.md"
        witness.write_bytes(path.read_bytes())
        row = {
            **original,
            "document_id": identifier,
            "path": str(path),
            "input_copy": str(witness),
        }
        data["documents"].append(row)
        store.document(identifier)["corpus"] = {
            "path": str(path),
            "version": VERSION,
            "metadata": row["metadata"],
            "input_copy": str(witness),
        }
    store.save()
    records.atomic_json(manifest, data)
    (tmp_path / "mode").write_text(mode)
    wrapper = tmp_path / "graphify.py"
    wrapper.write_text(
        GRAPHIFY_PREFLIGHT + "import json\nfrom pathlib import Path\n"
        f"state=json.loads(Path({str(store.path)!r}).read_text())\n"
        "assert all(d['steps']['graph']['status']=='running' for d in state['documents'].values())\n"
        + wrapper.read_text()
    )
    writes = count_state_writes(monkeypatch, store)
    with store.lock():
        code = run_graph(settings, book, store, run)
    assert code == {"success": 0, "failed": 2, "interrupt": 130}[mode]
    assert len(writes) == 2
    expected = {"success": "completed", "failed": "failed", "interrupt": "unknown"}[
        mode
    ]
    reloaded = Store(settings, book)
    for row in data["documents"]:
        step = reloaded.step(row["document_id"], "graph")
        assert step is not None and step["status"] == expected
