"""Offline sealed recovery through public commands and native proof fixtures."""

import fcntl
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_orchestrator_apply_cli import cli
from tests.test_wash_cleanup_cli import instance


def read(path):
    return json.loads(Path(path).read_bytes())


def recovery_case(tmp_path, *, pre_send=False):
    config, tool = instance(tmp_path)
    body = (
        tool.read_text()
        .replace("'items_count':2", "'items_count':3")
        .replace("for i in (1,2)", "for i in (1,2,3)")
    )
    tool.write_text(body)
    before = cli("sync", "--config", config)
    assert before.returncode == 0, before.stderr
    body = body.replace("2026-11-12T01:00:00Z", "2026-11-13T01:00:00Z")
    body = body.replace(
        "        rows.append({**doc,",
        """        path.write_text(path.read_text()+'\\nChanged body for recovery.\\n')
        if doc['document_id'] in ('1', '3'):
            image = path.parent / (doc['document_id'] + '.png')
            image.write_bytes(doc['document_id'].encode())
            path.write_text(path.read_text() + f'\\n![photo]({image.name})\\n[download]({image.name})\\n')
        rows.append({**doc,""",
    )
    body = body.replace(
        "if tool in ('mdcompare', 'mdocr'):",
        """if tool == 'mdocr' and args[0] == 'run':
    from mdocr import runner
    from mdocr.plan import read_plan
    plan_path = pathlib.Path(args[args.index('--plan') + 1]); p = read_plan(plan_path)
    result = pathlib.Path(args[args.index('--result') + 1])
    ledger = plan_path.with_name(plan_path.name + '.execution.json')
    e = runner.Execution(p, runner._sha(plan_path.read_bytes()), ledger, result,
                         plan_path.with_name('original-evidence'), False, version='0.3.4')
    jobs, _, _ = e.plan_jobs()
    e.state['budget']['requests'] = len(jobs)
    for key, job in jobs.items():
        item = {'key': key, 'status': 'admitted', 'reserved_tokens': 0}
        if pathlib.Path(job['source_path']).read_bytes() == b'1':
            item.update(status='success', patches=[{'kind':'text','markdown':'Known OCR phrase.', 'uncertainty':''}])
        e.state['requests'].append(item)
    e.save()
    raise SystemExit(1)
if tool in ('mdcompare', 'mdocr'):""",
    )
    if pre_send:
        body = body.replace(
            "        e.state['requests'].append(item)",
            """        item['source_sha256'] = job['sha256']
        if item['status'] == 'admitted':
            item.update(status='failed', reason='svg_profile_cleanup_failed')
        e.state['requests'].append(item)""",
        )
    tool.write_text(body)
    interrupted = cli("sync", "--config", config, "--stop-after", "corpus")
    assert interrupted.returncode == 0, interrupted.stderr
    old = read(json.loads(interrupted.stdout)["run"])
    original = tmp_path / "state/funds/ocr" / old["run_id"]
    assert read(original / "settled.json")["reason"] == "OCR_RESULT_BINDING_INVALID"
    return config, old["run_id"], original


@pytest.mark.parametrize("pre_send", [False, True])
def test_offline_recovery_freezes_new_manifest_and_never_replays(tmp_path, pre_send):
    config, original_id, original = recovery_case(tmp_path, pre_send=pre_send)
    witnesses = {p: p.read_bytes() for p in original.glob("*.json")}
    graph = tmp_path / "corpus/graphify-out/graph.json"
    graph_before = graph.read_bytes()
    native_manifest = graph.with_name("manifest.json")
    native_before = read(native_manifest)
    md_before = {p: p.read_bytes() for p in (tmp_path / "corpus").glob("*.md")}
    prepared = cli(
        "recover-ocr",
        "--config",
        config,
        "--run",
        original_id,
        "--execution-version",
        "0.3.4",
        "--output-dir",
        tmp_path / "private",
    )
    assert prepared.returncode == 0, prepared.stderr
    approval = json.loads(prepared.stdout)
    assert approval["requests"] == {
        "success": 1,
        "failed" if pre_send else "admitted": 1,
    }
    assert approval["documents"] == {"replace": 1, "unchanged": 1, "partial": 1}
    assert all(p.read_bytes() == data for p, data in md_before.items())
    assert graph.read_bytes() == graph_before
    wrong = cli(
        "recover-ocr",
        "--config",
        config,
        "--publish",
        approval["receipt"],
        "--receipt-sha256",
        "0" * 64,
    )
    assert wrong.returncode == 1
    assert "SHA-256 mismatch" in wrong.stderr
    assert all(p.read_bytes() == data for p, data in md_before.items())
    with (tmp_path / "corpus/graphify-out/.dogpdkb.lock").open("a+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = cli(
            "recover-ocr",
            "--config",
            config,
            "--publish",
            approval["receipt"],
            "--receipt-sha256",
            approval["receipt_sha256"],
        )
    assert blocked.returncode == 1 and "another writer" in blocked.stderr
    assert all(p.read_bytes() == data for p, data in md_before.items())
    staged = cli(
        "recover-ocr",
        "--config",
        config,
        "--publish",
        approval["receipt"],
        "--receipt-sha256",
        approval["receipt_sha256"],
    )
    assert staged.returncode == 2, staged.stderr
    proved = json.loads(staged.stdout)
    manifest = read(proved["manifest"])
    assert manifest["run_id"] != original_id
    assert (
        hashlib.sha256(Path(proved["manifest"]).read_bytes()).hexdigest()
        == proved["manifest_sha256"]
    )
    actions = sorted(row["action"] for row in manifest["documents"])
    assert actions == ["compile", "defer", "defer"]
    changed = [p for p, data in md_before.items() if p.read_bytes() != data]
    assert len(changed) == 1 and b"Known OCR phrase." in changed[0].read_bytes()
    assert graph.read_bytes() == graph_before
    link = changed[0].read_text().split("[download](", 1)[1].split(")", 1)[0]
    assert Path(link).is_absolute() and Path(link).read_bytes() == b"1"
    assert all(p.read_bytes() == data for p, data in witnesses.items())
    commands = [
        json.loads(line)[:2]
        for line in (tmp_path / "commands.jsonl").read_text().splitlines()
    ]
    assert commands.count(["mdocr", "run"]) == 2
    assert commands.count(["mdocr", "assemble"]) == 2
    assert commands.count(["graphify", "extract"]) == 1
    replay = cli("recover-ocr", "--config", config, "--resume", manifest["run_id"])
    assert replay.returncode == 2, replay.stderr
    assert json.loads(replay.stdout)["manifest_sha256"] == proved["manifest_sha256"]
    extracted = cli("update-graph", "--config", config, "--input", proved["manifest"])
    assert extracted.returncode == 2, extracted.stderr
    run = read(proved["run"])
    native_after = read(native_manifest)
    assert native_after[changed[0].name] != native_before[changed[0].name]
    mirror = Path(run["artifacts"]["graph_corpus"])
    deferred = next(row for row in manifest["documents"] if row["action"] == "defer")
    assert (
        "Changed body for recovery."
        not in (mirror / Path(deferred["path"]).name).read_text()
    )
    assert "Known OCR phrase." in (mirror / changed[0].name).read_text()
    relative = Path(deferred["path"]).name
    assert native_after[relative] == native_before[relative]
    assert run["counts"]["completed"] == 1
    replay = cli("recover-ocr", "--config", config, "--resume", manifest["run_id"])
    assert replay.returncode == 2, replay.stderr
    assert read(proved["run"]) == run
    assert all(p.read_bytes() == data for p, data in witnesses.items())


def test_source_witness_tamper_is_rejected_before_assembly(tmp_path):
    config, original_id, original = recovery_case(tmp_path)
    journal = original / "plan.json.execution.json"
    journal.write_bytes(journal.read_bytes() + b"\n")
    result = cli(
        "recover-ocr",
        "--config",
        config,
        "--run",
        original_id,
        "--execution-version",
        "0.3.4",
        "--output-dir",
        tmp_path / "private",
    )
    assert result.returncode == 1
    assert "OCR witness changed" in result.stderr
    assert not (tmp_path / "private").exists()


def test_recovery_help_without_configuration():
    result = cli("recover-ocr", "--help", "--full")
    assert result.returncode == 0, result.stderr
    assert "Recovery does not dispatch OCR" in result.stdout
