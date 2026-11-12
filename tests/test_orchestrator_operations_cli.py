"""Signals, actual writer targets and read-only observation via public CLIs."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def config(directory, *, corpus=None, graph=None, tool=None):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": "funds",
                    "name": "Funds",
                    "url": "https://example.invalid/book",
                    "corpus": str(corpus or directory / "corpus"),
                }
            ]
        )
    )
    path = directory / "instance.toml"
    text = '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\n'
    if graph:
        text += f"graph_dir={json.dumps(str(graph))}\n"
    if tool:
        text += (
            "[tools]\n"
            + "\n".join(
                f"{name}={json.dumps([sys.executable, str(tool)])}"
                for name in ("graphify", "skylark")
            )
            + "\n"
        )
    path.write_text(text)
    return path


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", *map(str, args)],
        env=ENV,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


@contextmanager
def running(*args):
    process = subprocess.Popen(
        [sys.executable, "-m", "dogpdkb", *map(str, args)],
        env=ENV,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def line(stream):
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        assert selector.select(3), "CLI did not emit live output"
    return stream.readline()


def alive(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.parametrize(
    ("signum", "code"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)]
)
def test_cli_forwards_signal_to_tool_process_group(tmp_path, signum, code):
    tool = tmp_path / "tool.py"
    tool.write_text("""import json, os, pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path(__file__).with_suffix(".pids").write_text(json.dumps([os.getpid(), child.pid]))
print("tool running", flush=True)
time.sleep(60)
""")
    selected = config(tmp_path / "instance", tool=tool)
    with running("doctor", "--config", selected, "--json") as process:
        assert "tool running" in line(process.stderr)
        pids = json.loads(tool.with_suffix(".pids").read_text())
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=6)
    assert process.returncode == code, stdout + stderr
    assert all(not alive(pid) for pid in pids), "child work survived CLI termination"


@pytest.mark.parametrize("target", ["corpus", "graph"])
def test_actual_write_target_refuses_concurrency_and_releases_after_interrupt(
    tmp_path, target
):
    tool = tmp_path / "tool.py"
    tool.write_text('import time\nprint("writer active", flush=True)\ntime.sleep(60)\n')
    common = tmp_path / "shared"
    first = config(
        tmp_path / "first",
        corpus=common if target == "corpus" else None,
        graph=common if target == "graph" else None,
        tool=tool,
    )
    second = config(
        tmp_path / "second",
        corpus=common if target == "corpus" else None,
        graph=common if target == "graph" else None,
    )
    listing = tmp_path / "listing.json"
    listing.write_text(
        json.dumps(
            {
                "schema": "skylark-listing-1",
                "complete": True,
                "book": {"url": "https://example.invalid/book", "items_count": 0},
                "documents": [],
                "toc": [],
                "captured_at": "2026-11-12T01:00:00Z",
            }
        )
    )
    with running("discover", "--config", first) as process:
        assert "writer active" in line(process.stderr)
        blocked = cli("discover", "--config", second, "--listing", listing)
        assert blocked.returncode == 1
        assert "another writer" in blocked.stderr
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=6)
    assert process.returncode == 143
    next_run = cli("discover", "--config", second, "--listing", listing)
    assert next_run.returncode == 0, next_run.stderr


def test_jsonl_watch_reads_existing_run_without_modifying_state(tmp_path):
    selected = config(tmp_path)
    listing = tmp_path / "listing.json"
    listing.write_text(
        json.dumps(
            {
                "schema": "skylark-listing-1",
                "complete": True,
                "book": {"url": "https://example.invalid/book", "items_count": 0},
                "documents": [],
                "toc": [],
                "captured_at": "2026-11-12T01:00:00Z",
            }
        )
    )
    result = cli("discover", "--config", selected, "--listing", listing)
    assert result.returncode == 0, result.stderr
    before = {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    with running("status", "--config", selected, "--watch", "--json") as process:
        first = json.loads(line(process.stdout))
        second = json.loads(line(process.stdout))
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=5)
    assert first == second
    assert first["status"] == "completed"
    assert process.returncode == 130
    after = {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
