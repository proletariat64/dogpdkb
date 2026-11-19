"""Bounded downloads through public commands and controlled tool processes."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.source_fixture import team_id, team_row
from tests.test_orchestrator_apply_cli import ROOT, cli, instance


def downloads(tmp_path, count=6):
    config, differences = instance(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())["42"]
    row = json.loads(differences.read_text())["documents"][0]
    ids = [str(index + 1) for index in range(count)]
    (tmp_path / "remote.json").write_text(
        json.dumps({key: dict(remote) for key in ids})
    )
    data = json.loads(differences.read_text())
    data["documents"] = [{**row, **team_row(key)} for key in ids]
    differences.write_text(json.dumps(data))
    (tmp_path / "started").mkdir()
    (tmp_path / "release").mkdir()
    (tmp_path / "skylark.py").write_text(TOOL)
    return config, differences, ids


TOOL = r"""
import fcntl, json, os, subprocess, sys, time
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
def option(name): return Path(args[args.index(name) + 1])
if args[0] == 'listing':
    remote = json.loads((root / 'remote.json').read_text())
    option('--output').write_text(json.dumps({'schema':'skylark-listing-1','complete':True,'book':{'url':'https://example.invalid/team/book','items_count':len(remote)},'documents':[{'document_id':key,'title':'Title','version':doc['version']} for key,doc in remote.items()],'toc':[]}))
    raise SystemExit(0)
item = json.loads(option('--request').read_text())['documents'][0]
identity = item['document_id']
with (root / 'counter.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    stats = json.loads((root / 'counter.json').read_text()) if (root / 'counter.json').exists() else {'active': 0, 'maximum': 0}
    stats['active'] += 1
    stats['maximum'] = max(stats['maximum'], stats['active'])
    (root / 'counter.json').write_text(json.dumps(stats))
(root / 'started' / identity).write_text(str(os.getpid()))
if (root / 'descendants').exists() and identity != '1':
    code = 'import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('+repr(str(root/'descendant-ready'/identity))+').write_text(str(os.getpid()));time.sleep(60)'
    subprocess.Popen([sys.executable, '-c', code])
while not (root / 'release' / identity).exists(): time.sleep(.01)
remote = json.loads((root / 'remote.json').read_text())[identity]
if remote.get('missing_report'):
    with (root / 'counter.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stats = json.loads((root / 'counter.json').read_text()); stats['active'] -= 1
        (root / 'counter.json').write_text(json.dumps(stats))
    raise SystemExit(2)
path = Path(item['path']); path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(remote['body'])
rows = [{'document_id': identity, 'path': str(path), 'status': 'failed' if remote.get('failure') else 'completed', 'version': remote['version']}]
option('--output').write_text(json.dumps({'schema': 'skylark-export-result-1', 'status': 'completed', 'documents': rows}))
with (root / 'counter.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    stats = json.loads((root / 'counter.json').read_text()); stats['active'] -= 1
    (root / 'counter.json').write_text(json.dumps(stats))
raise SystemExit(2 if remote.get('failure') else 0)
"""


def start(config, differences, *options, command="apply"):
    scope = ["--input", str(differences)] if command == "apply" else []
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            command,
            "--config",
            str(config),
            *scope,
            "--stop-after",
            "plan",
            *options,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for(predicate, process, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, (
            "public CLI did not reach the controlled tool boundary"
        )
        time.sleep(0.01)


@pytest.mark.parametrize("command", ["apply", "sync"])
@pytest.mark.parametrize("value", ["0", "4", "-1", "2.5", "many"])
def test_invalid_concurrency_has_no_instance_side_effects(tmp_path, command, value):
    config, differences, _ = downloads(tmp_path)
    arguments = [command, "--config", config, "--concurrency", value]
    if command == "apply":
        arguments += ["--input", differences]
    result = cli(*arguments)
    assert result.returncode == 64
    assert "--concurrency" in result.stderr
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "runs").exists()
    assert not list((tmp_path / "started").iterdir())


@pytest.mark.parametrize("value, expected", [(None, 3), ("1", 1), ("2", 2), ("3", 3)])
def test_downloads_overlap_with_a_hard_configured_bound(tmp_path, value, expected):
    config, differences, ids = downloads(tmp_path)
    process = start(
        config, differences, *([] if value is None else ["--concurrency", value])
    )
    try:
        wait_for(
            lambda: len(list((tmp_path / "started").iterdir())) == expected, process
        )
        assert json.loads((tmp_path / "counter.json").read_text())["active"] == expected
        for identity in ids:
            (tmp_path / "release" / identity).touch()
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        assert json.loads((tmp_path / "counter.json").read_text()) == {
            "active": 0,
            "maximum": expected,
        }
        result = json.loads(stdout)
        run = json.loads(Path(result["run"]).read_text())
        prepared = json.loads(Path(run["artifacts"]["prepared_inputs"]).read_text())
        assert set(prepared) == {team_id(i) for i in ids}
        assert all(
            Path(row["input_copy"]).read_text().startswith("# Title")
            for row in prepared.values()
        )
        assert run["status"] == "paused"
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=5)


def state(tmp_path):
    return json.loads((tmp_path / "state/funds/state.json").read_text())


def saved_run(tmp_path):
    return next((tmp_path / "runs/funds").glob("*/run.json"))


def process_exists(pid):
    # A reparented killed descendant may briefly remain an unreaped zombie.
    path = Path(f"/proc/{pid}/stat")
    return path.exists() and path.read_text().split(") ", 1)[1].split()[0] != "Z"


def check_comparison_gate(config, tmp_path, count):
    wrapper = tmp_path / "compare-gate.py"
    wrapper.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "root=Path(__file__).parent\n"
        "assert json.loads((root/'counter.json').read_text())['active']==0\n"
        f"assert len(list((root/'started').iterdir()))=={count}\n"
        "(root/'compare-called').touch()\n"
        "os.execv(sys.executable,[sys.executable,'-m','mdcompare',*sys.argv[1:]])\n"
    )
    config.write_text(
        config.read_text().replace(
            json.dumps([sys.executable, "-m", "mdcompare"]),
            json.dumps([sys.executable, str(wrapper)]),
        )
    )


def test_sync_defaults_to_three_exports_and_waits_for_download_batch(tmp_path):
    config, differences, ids = downloads(tmp_path)
    check_comparison_gate(config, tmp_path, len(ids))
    process = start(config, differences, command="sync")
    try:
        wait_for(lambda: len(list((tmp_path / "started").iterdir())) == 3, process)
        assert not (tmp_path / "compare-called").exists()
        for identity in ids:
            (tmp_path / "release" / identity).touch()
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        assert (tmp_path / "compare-called").exists()
        assert json.loads((tmp_path / "counter.json").read_text())["maximum"] == 3
        assert (
            json.loads(Path(json.loads(stdout)["run"]).read_text())["command"] == "sync"
        )
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)


def test_mixed_export_failure_and_version_drift_preserve_healthy_checkpoints(tmp_path):
    config, differences, ids = downloads(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["2"]["failure"] = True
    remote["3"]["version"] = "2026-11-13T10:00:00Z"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    for identity in ids:
        (tmp_path / "release" / identity).touch()
    result = cli(
        "apply",
        "--config",
        config,
        "--input",
        differences,
        "--concurrency",
        "3",
        "--stop-after",
        "plan",
    )
    assert result.returncode == 0, result.stderr
    run = json.loads(Path(json.loads(result.stdout)["run"]).read_text())
    prepared = json.loads(Path(run["artifacts"]["prepared_inputs"]).read_text())
    assert set(prepared) == {team_id(i) for i in (1, 4, 5, 6)}
    assert {
        item["document_id"] for item in run["failures"] if item["step"] == "export"
    } == {team_id(2), team_id(3)}
    assert (
        state(tmp_path)["documents"][team_id("2")]["steps"]["export"]["status"]
        == "failed"
    )
    assert (
        state(tmp_path)["documents"][team_id("3")]["steps"]["export"]["status"]
        == "failed"
    )
    assert len(list((tmp_path / "started").iterdir())) == len(ids)


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_interruption_stops_every_group_and_resume_keeps_settled_inputs(
    tmp_path, signum
):
    config, differences, ids = downloads(tmp_path)
    (tmp_path / "descendants").touch()
    (tmp_path / "descendant-ready").mkdir()
    process = start(config, differences, "--concurrency", "2")
    try:
        wait_for(lambda: (tmp_path / "descendant-ready" / "2").exists(), process)
        (tmp_path / "release" / "1").touch()
        wait_for(lambda: (tmp_path / "descendant-ready" / "3").exists(), process)
        prepared_path = saved_run(tmp_path).parent / "prepared-inputs.json"
        assert set(json.loads(prepared_path.read_text())) == {team_id("1")}
        assert (
            state(tmp_path)["documents"][team_id("1")]["steps"]["export"]["status"]
            == "completed"
        )
        frozen = Path(
            json.loads(prepared_path.read_text())[team_id("1")]["input_copy"]
        ).read_bytes()
        process.send_signal(signum)
        process.communicate(timeout=8)
        assert process.returncode == 128 + signum
        assert {p.name for p in (tmp_path / "started").iterdir()} == {"1", "2", "3"}
        facts = state(tmp_path)["documents"]
        assert facts[team_id("1")]["steps"]["export"]["status"] == "completed"
        assert all(
            facts[team_id(key)]["steps"]["export"]["status"] == "unknown"
            for key in ("2", "3")
        )
        assert all(
            not process_exists(int((tmp_path / "started" / key).read_text()))
            for key in ("2", "3")
        )
        assert all(
            not process_exists(int((tmp_path / "descendant-ready" / key).read_text()))
            for key in ("2", "3")
        )
        (tmp_path / "descendants").unlink()
        for identity in ids:
            (tmp_path / "release" / identity).touch()
        resumed = cli(
            "apply",
            "--config",
            config,
            "--resume",
            saved_run(tmp_path),
            "--concurrency",
            "3",
            "--stop-after",
            "plan",
        )
        assert resumed.returncode == 0, resumed.stderr
        run = json.loads(Path(json.loads(resumed.stdout)["run"]).read_text())
        prepared = json.loads(Path(run["artifacts"]["prepared_inputs"]).read_text())
        assert set(prepared) == {team_id(i) for i in (1, 4, 5, 6)}
        assert Path(prepared[team_id("1")]["input_copy"]).read_bytes() == frozen
        assert {
            item["document_id"]
            for item in run["failures"]
            if item["reason"] == "VERSION_ATTEMPT_SUPPRESSED"
        } == {team_id(2), team_id(3)}
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGKILL)
            process.communicate(timeout=5)


def test_checkpoint_error_stops_active_peers_and_records_unknown_attempts(tmp_path):
    config, differences, _ids = downloads(tmp_path)
    (tmp_path / "descendants").touch()
    (tmp_path / "descendant-ready").mkdir()
    process = start(config, differences, "--concurrency", "3")
    try:
        wait_for(
            lambda: len(list((tmp_path / "descendant-ready").iterdir())) == 2, process
        )
        # Poison only the forthcoming checkpoint destination, after attempts
        # have begun, to exercise an actual coordinator I/O failure.
        (saved_run(tmp_path).parent / "prepared-inputs.json").mkdir()
        (tmp_path / "release" / "1").touch()
        _, stderr = process.communicate(timeout=8)
        assert process.returncode == 1, stderr
        assert "directory" in stderr.lower()
        assert {path.name for path in (tmp_path / "started").iterdir()} == {
            "1",
            "2",
            "3",
        }
        for identity in ("2", "3"):
            assert not process_exists(
                int((tmp_path / "started" / identity).read_text())
            )
            assert not process_exists(
                int((tmp_path / "descendant-ready" / identity).read_text())
            )
            assert (
                state(tmp_path)["documents"][team_id(identity)]["steps"]["export"][
                    "status"
                ]
                == "unknown"
            )
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=5)


def test_export_children_retain_write_locks_after_parent_is_killed(tmp_path):
    config, differences, _ids = downloads(tmp_path)
    process = start(config, differences, "--concurrency", "2")
    children = []
    try:
        wait_for(lambda: len(list((tmp_path / "started").iterdir())) == 2, process)
        children = [int(path.read_text()) for path in (tmp_path / "started").iterdir()]
        run = saved_run(tmp_path)
        process.kill()
        process.wait(timeout=5)
        refused = cli("purge", "--config", config, "--run", run, "--force")
        assert refused.returncode == 1
        assert run.is_file()
        assert all(process_exists(pid) for pid in children)
    finally:
        if process.poll() is None:
            process.kill()
        for pid in children:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)


def test_missing_export_report_is_unknown_without_discarding_healthy_exports(tmp_path):
    config, differences, ids = downloads(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["2"]["missing_report"] = True
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    for identity in ids:
        (tmp_path / "release" / identity).touch()
    result = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert result.returncode == 0, result.stderr
    run = json.loads(Path(json.loads(result.stdout)["run"]).read_text())
    prepared = json.loads(Path(run["artifacts"]["prepared_inputs"]).read_text())
    assert set(prepared) == {team_id(i) for i in (1, 3, 4, 5, 6)}
    assert (
        state(tmp_path)["documents"][team_id("2")]["steps"]["export"]["status"]
        == "unknown"
    )
    assert len(list((tmp_path / "started").iterdir())) == len(ids)
