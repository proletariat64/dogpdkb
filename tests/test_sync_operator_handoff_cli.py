"""Operator acceptance via offline sync/status and their documented artifacts."""

import hashlib
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

from scripts.smoke_install import REMOTE_CLI
from tests.test_orchestrator_ocr_batch_cli import _native_instance
from tests.test_orchestrator_operations_cli import line, running
from tests.test_sync_phase_resume_cli import setup_local
from tests.test_sync_source_selection_cli import cli, run_data


def status(root, config, run, *, as_json=True) -> Any:
    result = cli(
        root,
        "status",
        "--config",
        config,
        "--run",
        run,
        *(["--json"] if as_json else []),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if as_json else result.stdout


def test_status_shows_phase_and_separate_publication_facts(tmp_path):
    config, source = setup_local(tmp_path)
    body = b"# Frozen document\n"
    (source / "note.md").write_bytes(body)
    run, _ = run_data(
        cli(
            tmp_path,
            "sync",
            "--config",
            config,
            "--dir",
            source,
            "--stop-after",
            "corpus",
        )
    )
    paused = status(tmp_path, config, run)
    assert paused["progress"]["last_completed_phase"] == "corpus"
    assert paused["publication"] == {"corpus": "published", "graph": "not_started"}
    assert paused["next_action"]["kind"] == "resume"
    before = run.read_bytes()
    screen = status(tmp_path, config, run, as_json=False)
    for phrase in (
        "completed=",
        "in_flight=",
        "pending=",
        "corpus=published",
        "graph=not_started",
        "next:",
    ):
        assert phrase in screen
    assert run.read_bytes() == before
    commands = (tmp_path / "commands.jsonl").read_bytes()
    rejected = cli(
        tmp_path, "sync", "--config", config, "--resume", run, "--retry-graph"
    )
    assert rejected.returncode == 1 and "no frozen graph input tree" in rejected.stderr
    assert (tmp_path / "commands.jsonl").read_bytes() == commands

    run_data(cli(tmp_path, "sync", "--config", config, "--resume", run))
    complete = status(tmp_path, config, run)
    assert complete["publication"] == {"corpus": "published", "graph": "published"}
    [fact] = complete["facts"].values()
    expected = hashlib.sha256(body).hexdigest()
    assert (
        fact["raw"]["sha256"]
        == fact["corpus"]["sha256"]
        == fact["graph"]["sha256"]
        == expected
    )
    assert fact["corpus_vs_graph"] == "same_bytes"
    assert Path(fact["graph"]["report"]).is_file()


def test_cleanup_handoff_binds_graph_retry_to_original_scope(tmp_path):
    config, source = setup_local(tmp_path)
    (source / "note.md").write_text("# Frozen document\n")
    tool = tmp_path / "tools.py"
    original = tool.read_text()
    tool.write_text(
        original.replace(
            "    graph=flag('--out')",
            "    raise SystemExit(1)\n    graph=flag('--out')",
        )
    )
    sync = ("sync", "--config", config)
    result = cli(tmp_path, *sync, "--dir", source)
    assert result.returncode == 2, result.stderr
    run = Path(json.loads(result.stdout)["run"])
    snapshot = status(tmp_path, config, run)
    assert snapshot["publication"] == {"corpus": "published", "graph": "retained"}
    handoff = snapshot["handoff"]
    [failure] = handoff["items"]
    assert failure["run_id"] == snapshot["run_id"]
    assert failure["source_key"] == snapshot["sources"][0]
    assert (
        failure["version"]
        == snapshot["facts"][failure["document_id"]]["corpus"]["version"]
    )
    assert failure["phase"] == "graph"
    assert (
        failure["send_state"] == "unknown"
    )  # A process exit does not attest transport.
    assert failure["input_sha256"] == hashlib.sha256(b"# Frozen document\n").hexdigest()
    assert failure["retry_scope"] == "graph_batch"
    assert all(Path(path).is_file() for path in failure["evidence"])
    manifest = Path(handoff["graph_retry"]["manifest"])
    before = manifest.read_bytes()
    tool.write_text(original)
    calls = (tmp_path / "commands.jsonl").read_bytes()
    recovery = (*sync, "--resume", run, "--retry-graph")
    widened = cli(tmp_path, *recovery, "--graphify-inputs", "all")
    assert widened.returncode == 1, widened.stderr
    assert "saved Graphify input mode" in widened.stderr
    altered = json.loads(before)
    altered["documents"][0]["action"] = "defer"
    manifest.write_text(json.dumps(altered))
    rejected = cli(tmp_path, *recovery)
    assert rejected.returncode == 1, rejected.stderr
    assert "manifest changed" in rejected.stderr
    assert (tmp_path / "commands.jsonl").read_bytes() == calls

    manifest.write_bytes(before)
    scan_control = tmp_path / "corpus/funds/.graphifyignore"
    scan_control.write_text("# A changed scan scope\n")
    rejected = cli(tmp_path, *recovery)
    assert rejected.returncode == 1, rejected.stderr
    assert "graph input tree changed" in rejected.stderr
    assert (tmp_path / "commands.jsonl").read_bytes() == calls
    scan_control.unlink()
    recovered = cli(tmp_path, *recovery)
    assert recovered.returncode == 0, recovered.stderr
    after = status(tmp_path, config, run)
    assert (
        after["status"] == "completed" and after["publication"]["graph"] == "published"
    )
    assert after["handoff"]["unresolved"] == 0
    assert after["handoff"]["items"][0]["resolution"] == "resolved"
    assert [
        json.loads(line)[:2]
        for line in (tmp_path / "commands.jsonl")
        .read_bytes()[len(calls) :]
        .splitlines()
    ] == [["graphify", "extract"]]


def ocr_instance(root, names, *, crash=False):
    config, _ = _native_instance(root, crash=crash)
    source = root / "inbox"
    source.mkdir()
    for name in names:
        image = f"diagram-{name}.png"
        (source / image).write_bytes(f"synthetic image bytes {name}".encode())
        (source / f"{name}.md").write_text(f"# {name}\n\n![diagram]({image})\n")
    tools = root / "graph-fixture.py"
    tools.write_text(REMOTE_CLI)
    config.write_text(
        config.read_text().replace(
            "[tools]\n",
            f"[tools]\ngraphify={json.dumps([sys.executable, str(tools), 'graphify'])}\n",
        )
    )
    return config, source


def test_unknown_send_is_visible_and_not_replayed(tmp_path):
    config, source = ocr_instance(tmp_path, ["One"], crash=True)
    sync = ("sync", "--config", config)
    first = cli(tmp_path, *sync, "--dir", source)
    assert first.returncode == 143, first.stderr
    [run] = (tmp_path / "runs/funds").glob("run-*/run.json")
    interrupted = status(tmp_path, config, run)
    [issue] = interrupted["handoff"]["items"]
    assert (issue["phase"], issue["send_state"], issue["retry_scope"]) == (
        "ocr",
        "unknown",
        "none",
    )
    resumed = cli(tmp_path, *sync, "--resume", run)
    assert resumed.returncode == 2, resumed.stderr
    partial = status(tmp_path, config, run)
    assert partial["status"] == "partial"
    assert partial["publication"] == {"corpus": "published", "graph": "published"}
    [issue] = partial["handoff"]["items"]
    assert issue["send_state"] == "unknown" and issue["retry_scope"] == "none"
    assert len((tmp_path / "requests").read_text().splitlines()) == 1


def test_verified_unsent_catchup_requires_intact_ocr_witness(tmp_path):
    config, source = ocr_instance(tmp_path, ["One", "Two"])
    sync = ("sync", "--config", config, "--dir", source)
    first = cli(tmp_path, *sync)
    assert first.returncode == 2, first.stderr
    run = Path(json.loads(first.stdout)["run"])
    partial = status(tmp_path, config, run)
    [issue] = partial["handoff"]["items"]
    assert (issue["send_state"], issue["retry_scope"]) == (
        "not_sent",
        "document_next_batch",
    )
    [seal] = [
        Path(path) for path in issue["evidence"] if Path(path).name == "settled.json"
    ]
    original = seal.read_bytes()
    seal.write_bytes(original + b"\n")
    repeat = cli(tmp_path, *sync)
    assert repeat.returncode == 2, repeat.stderr
    blocked = status(tmp_path, config, json.loads(repeat.stdout)["run"])
    [issue] = blocked["handoff"]["items"]
    assert (issue["reason"], issue["send_state"], issue["retry_scope"]) == (
        "OCR_PREVIOUS_EVIDENCE_INVALID",
        "unknown",
        "none",
    )
    assert len((tmp_path / "requests").read_text().splitlines()) == 1


def test_same_version_corpus_enrichment_is_visible_before_graph_catches_up(tmp_path):
    config, source = ocr_instance(tmp_path, ["One", "Two"])
    args = ("sync", "--config", config, "--dir", source)
    first = cli(tmp_path, *args)
    assert first.returncode == 2, first.stderr
    old = status(tmp_path, config, json.loads(first.stdout)["run"])
    [held] = old["handoff"]["items"]
    identifier = held["document_id"]
    original_graph = old["facts"][identifier]["graph"]
    run, _ = run_data(cli(tmp_path, *args, "--stop-after", "corpus"))
    facts = status(tmp_path, config, run)["facts"][identifier]
    assert (
        facts["raw"]["version"]
        == facts["corpus"]["version"]
        == facts["graph"]["version"]
    )
    assert facts["graph"] == original_graph
    assert facts["corpus"]["ocr_status"] == "completed"
    assert facts["corpus_vs_graph"] == "different_bytes"
    assert (
        facts["corpus"]["sha256"]
        == hashlib.sha256(Path(facts["corpus"]["path"]).read_bytes()).hexdigest()
    )
    assert len((tmp_path / "requests").read_text().splitlines()) == 2


def test_interrupting_live_watch_does_not_signal_sync(tmp_path):
    config, source = setup_local(tmp_path)
    (source / "note.md").write_text("# Frozen document\n")
    tool = tmp_path / "tools.py"
    tool.write_text(
        tool.read_text().replace(
            "    graph=flag('--out')",
            "    import time\n    (root/'waiting').touch()\n"
            "    while not (root/'continue').exists(): time.sleep(0.05)\n"
            "    graph=flag('--out')",
        )
    )
    with running("sync", "--config", config, "--dir", source) as sync:
        deadline = time.monotonic() + 10
        while not (tmp_path / "waiting").is_file():
            assert sync.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        with running("status", "--config", config, "--watch", "--json") as watch:
            observed = json.loads(line(watch.stdout))
            assert observed["alive"] and observed["progress"]["phase"] == "graph"
            assert observed["publication"] == {
                "corpus": "published",
                "graph": "pending",
            }
            run_path = Path(observed["run_directory"]) / "run.json"
            before = run_path.read_bytes()
            watch.send_signal(signal.SIGINT)
            watch.communicate(timeout=4)
            assert watch.returncode == 130
            assert run_path.read_bytes() == before
            assert sync.poll() is None
        (tmp_path / "continue").touch()
        stdout, stderr = sync.communicate(timeout=10)
        assert sync.returncode == 0, stderr
        assert json.loads(stdout)["status"] == "completed"
