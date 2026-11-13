"""Core behavior through the public CLI; real files, no internal mocks."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def instance(tmp_path: Path) -> Path:
    (tmp_path / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": "funds",
                    "name": "Funds",
                    "url": "https://example.invalid/team/book",
                    "corpus": "corpus",
                }
            ]
        )
    )
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n'
        '[paths]\nstate="state"\nstaging="runs"\n'
        '[skylark]\nmcp_url="https://example.invalid/mcp"\n'
    )
    return config


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", *args],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_status_without_runs_is_read_only(tmp_path: Path):
    config = instance(tmp_path)
    result = cli("status", "--config", str(config), "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "schema": "dogpdkb-status-1",
        "status": "no-runs",
        "runs": 0,
    }
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "runs").exists()


def test_status_observes_dead_owner_without_rewriting_run(tmp_path: Path):
    config = instance(tmp_path)
    directory = tmp_path / "runs" / "funds" / "run-previous"
    directory.mkdir(parents=True)
    path = directory / "run.json"
    path.write_text(
        json.dumps(
            {
                "schema": "dogpdkb-run-1",
                "run_id": directory.name,
                "book": {"key": "funds"},
                "pid": 999999999,
                "status": "running",
                "stage": "mdcompare",
                "counts": {"completed": 2, "total": 4},
                "started_at": "2026-11-12T10:00:00+08:00",
                "updated_at": "2026-11-12T10:00:03+08:00",
                "artifacts": {"plan": str(directory / "plan.json")},
            }
        )
    )
    before = path.read_bytes()
    result = cli("status", "--config", str(config), "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["status"] == "interrupted"
    assert data["recorded_status"] == "running"
    assert data["stage"] == "mdcompare"
    assert data["counts"] == {"completed": 2, "total": 4}
    assert data["elapsed_seconds"] == 3
    assert path.read_bytes() == before
    assert not (tmp_path / "state").exists()


def test_init_state_keeps_existing_corpus_and_unknown_graph_truth(tmp_path: Path):
    config = instance(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "支付__说明-2026-11-10-[42].md"
    source.write_text("# Existing cleaned knowledge\n\nKeep this exactly.\n")
    graph = corpus / "graphify-out" / "graph.json"
    graph.parent.mkdir()
    graph.write_text('{"nodes": [], "links": []}\n')
    before = source.read_bytes(), graph.read_bytes()
    result = cli("init-state", "--config", str(config))
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["registered"] == 1
    assert summary["graph_unknown"] == 1
    state = json.loads((tmp_path / "state" / "funds" / "state.json").read_text())
    assert state["documents"]["42"]["baseline"]["filename_date"] == "2026-11-10"
    assert "raw" not in state["documents"]["42"]
    assert before == (source.read_bytes(), graph.read_bytes())


def test_doctor_streams_tool_diagnostics_before_process_finishes(tmp_path: Path):
    config = instance(tmp_path)
    tool = tmp_path / "tool.py"
    tool.write_text(
        'import time\nprint("live diagnostic", flush=True)\ntime.sleep(1)\n'
    )
    with config.open("a") as stream:
        stream.write("[tools]\n")
        for name in ("graphify", "mdcompare", "mdocr", "skylark"):
            stream.write(f"{name}={json.dumps([sys.executable, str(tool)])}\n")
    with subprocess.Popen(
        [sys.executable, "-m", "dogpdkb", "doctor", "--config", str(config), "--json"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stderr, selectors.EVENT_READ)
            assert selector.select(2), "tool diagnostics were buffered"
        first_line = process.stderr.readline()
        assert "live diagnostic" in first_line
        assert process.poll() is None
        stdout, stderr = process.communicate(timeout=8)
    assert process.returncode == 0, first_line + stderr
    report = json.loads(stdout)
    assert all(row["status"] == "ok" for row in report)
    assert {row["name"] for row in report} >= {
        "graphify",
        "mdcompare",
        "mdocr",
        "skylark",
    }
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "runs").exists()


def test_durable_state_rejects_same_key_with_changed_source_book(tmp_path: Path):
    config = instance(tmp_path)
    (tmp_path / "corpus").mkdir()
    assert cli("init-state", "--config", str(config)).returncode == 0
    state = tmp_path / "state" / "funds" / "state.json"
    before = state.read_bytes()
    books = tmp_path / "books.json"
    rows = json.loads(books.read_text())
    rows[0]["url"] = "https://example.invalid/other/book"
    books.write_text(json.dumps(rows))
    result = cli("status", "--config", str(config), "--json")
    assert result.returncode == 1
    assert "source book" in result.stderr
    assert state.read_bytes() == before
