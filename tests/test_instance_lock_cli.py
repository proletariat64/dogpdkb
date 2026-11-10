"""Updater exclusivity through real installed and direct CLI entry points."""

import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def environment(tmp_path):
    env = os.environ.copy()
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env.pop("DOGPDKB_UPDATE_LOCK_FD", None)
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def run(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def start_blocked_publisher(tmp_path, env):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    graph = tmp_path / "graph.json"
    os.mkfifo(graph)
    books = tmp_path / "books.json"
    books.write_text(
        json.dumps([{"key": "one", "url": "https://example.invalid/book"}])
    )
    config = tmp_path / "dogpdkb.toml"
    config.write_text(
        '[source]\nbooks_file = "books.json"\n[skylark]\nmcp_url = "https://example.invalid/mcp"\nproxy_bin = "/bin/false"\n'
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "publish-web",
            "--config",
            str(config),
            "--input",
            str(corpus),
            "--graph",
            str(graph),
            "--output",
            str(tmp_path / "web"),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    return process


def wait_until_busy(process, env):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("publisher exited: " + process.communicate()[1])
        lock = Path(env["XDG_STATE_HOME"]) / "dogpdkb/sync.lock"
        if not lock.exists():
            time.sleep(0.02)
            continue
        with lock.open() as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                time.sleep(0.02)
                continue
        result = run(env, "sync", "--config", "/missing-lock-test-config.toml")
        if "UPDATE_ALREADY_RUNNING" in result.stderr:
            return result
        time.sleep(0.02)
    pytest.fail("another updater was never rejected")


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    process.communicate(timeout=10)


def test_updater_rejects_other_plans_but_keeps_read_only_cli_available(tmp_path):
    env = environment(tmp_path)
    process = start_blocked_publisher(tmp_path, env)
    try:
        result = wait_until_busy(process, env)
        assert result.returncode != 0
        lock = Path(env["XDG_STATE_HOME"]) / "dogpdkb/sync.lock"
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600
        assert stat.S_IMODE(lock.parent.stat().st_mode) == 0o700
        initialized = run(
            env, "init-state", "--config", "/another-config.toml", "--book", "other"
        )
        assert "UPDATE_ALREADY_RUNNING" in initialized.stderr
        compared = subprocess.run(
            [sys.executable, "-m", "mdcompare", "--version"],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert compared.returncode == 0
        assert run(env, "doctor", "--package", "--json").returncode == 0
        assert run(env, "sync", "--help").returncode == 0
    finally:
        stop_group(process)
    result = run(env, "sync", "--config", "/missing-lock-test-config.toml")
    assert "UPDATE_ALREADY_RUNNING" not in result.stderr


@pytest.mark.parametrize(
    "workflow",
    [
        "get_sync_updates",
        "apply_sync_updates",
        "sync_knowledge_base",
        "update_graph",
        "publish_web",
    ],
)
def test_direct_workflow_and_forged_context_cannot_bypass_busy_lock(tmp_path, workflow):
    env = environment(tmp_path)
    process = start_blocked_publisher(tmp_path, env)
    try:
        wait_until_busy(process, env)
        env["DOGPDKB_UPDATE_LOCK_FD"] = "0"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / (workflow + ".py")),
                "--config",
                "/missing-direct-lock-test.toml",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode != 0
        assert "UPDATE_ALREADY_RUNNING" in result.stderr
    finally:
        stop_group(process)


@pytest.mark.skipif(
    not Path("/proc/self/task").exists(), reason="Linux child process inspection"
)
def test_workflow_child_retains_lock_after_cli_parent_is_killed(tmp_path):
    env = environment(tmp_path)
    process = start_blocked_publisher(tmp_path, env)
    try:
        wait_until_busy(process, env)
        children = Path(f"/proc/{process.pid}/task/{process.pid}/children")
        deadline = time.monotonic() + 5
        while not children.read_text().strip():
            assert time.monotonic() < deadline, "CLI did not start its workflow child"
            time.sleep(0.02)
        process.kill()
        process.wait(timeout=5)
        result = run(env, "apply", "--config", "/different-config.toml")
        assert result.returncode != 0
        assert "UPDATE_ALREADY_RUNNING" in result.stderr
    finally:
        stop_group(process)
    result = run(env, "sync", "--config", "/missing-lock-test-config.toml")
    assert "UPDATE_ALREADY_RUNNING" not in result.stderr
