"""Offline OCR batch acceptance at the public apply/tool/artifact seams."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from tests.source_fixture import team_id
from tests.test_orchestrator_apply_cli import cli, instance, two_sources


def batch_instance(tmp_path, body, *, concurrency=3):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    tool = tmp_path / "batch_ocr.py"
    tool.write_text(
        """import hashlib, json, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(__file__).parent
with (root / 'ocr-argv').open('a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'plan':
    raise SystemExit(subprocess.call([sys.executable, '-m', 'mdocr.cli', *args]))
plan_path = Path(args[args.index('--plan')+1])
result = Path(args[args.index('--result')+1])
plan = json.loads(plan_path.read_bytes())
def sha(data): return hashlib.sha256(data).hexdigest()
def completed(doc):
    output = Path(doc['output'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(Path(doc['path']).read_bytes() + b'\\r\\nOCR fixture.\\r\\n')
    settings = plan['settings']
    return dict(document_id=doc['document_id'], path=doc['path'], status='completed',
                output=doc['output'], output_sha256=sha(output.read_bytes()), resources=[],
                ocr=dict(contract='mdocr-ocr-2', images=[], configuration=dict(
                    mode=settings['mode'], model=settings['model'], effort=settings['effort'],
                    prompt_sha256=sha(settings['prompt'].encode()), schema=settings.get('llm_schema', 'mdocr-image-patches-1'))))
def payload(rows, status):
    return dict(schema_version='mdocr-result-2', plan_sha256=sha(plan_path.read_bytes()),
                result_path=str(result), status=status, documents=rows)
"""
        + body
    )
    config.write_text(
        config.read_text().replace(
            json.dumps([sys.executable, "-m", "mdocr.cli"]),
            json.dumps([sys.executable, str(tool)]),
        )
        + f"\n[ocr]\nrequest_budget=1\nconcurrency={concurrency}\n"
    )
    return config, differences


@pytest.mark.parametrize(("concurrency", "expected"), [(3, "3"), (100, "100")])
def test_partial_batch_accepts_verified_peer_and_preserves_exact_output(
    tmp_path, concurrency, expected
):
    config, differences = batch_instance(
        tmp_path,
        """first, second = plan['documents']
result.write_text(json.dumps(payload([completed(first), dict(
    document_id=second['document_id'], path=second['path'], status='partial', output=None,
    resources=[])], 'partial')))
raise SystemExit(2)
""",
        concurrency=concurrency,
    )
    applied = cli("apply", "--config", config, "--input", differences)
    assert applied.returncode == 2, applied.stderr
    calls = [
        json.loads(line) for line in (tmp_path / "ocr-argv").read_text().splitlines()
    ]
    assert [call[0] for call in calls] == ["plan", "run"]
    flags = dict(zip(calls[0][1::2], calls[0][2::2]))
    assert flags["--mode"] == "llm"
    assert flags["--model"] == "openai/gpt-6.1-terra"
    assert flags["--effort"] == "medium"
    assert flags["--concurrency"] == expected
    assert flags["--request-budget"] == "1"
    manifest = json.loads(Path(json.loads(applied.stdout)["manifest"]).read_text())
    docs = {doc["document_id"]: doc for doc in manifest["documents"]}
    assert b"OCR fixture.\r\n" in Path(docs[team_id("42")]["path"]).read_bytes()
    assert b"OCR fixture." not in Path(docs[team_id("43")]["path"]).read_bytes()
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert state["documents"][team_id("42")]["steps"]["ocr"]["status"] == "completed"
    run = Path(json.loads(applied.stdout)["run"])
    report = Path(json.loads(run.read_text())["artifacts"]["ocr_report"])
    exact_report = report.read_bytes()
    evidence = json.loads(exact_report)["documents"][0]
    assert evidence["document_id"] != team_id("42")  # No tool report ID rewriting.
    shutil.rmtree(run.parent)
    assert report.read_bytes() == exact_report
    assert Path(evidence["output"]).read_bytes().endswith(b"OCR fixture.\r\n")
    repeated = cli("apply", "--config", config, "--input", differences)
    assert repeated.returncode == 2, repeated.stderr
    assert len((tmp_path / "ocr-argv").read_text().splitlines()) == 2


def test_ocr_concurrency_above_maximum_rejected_before_any_tool_call(tmp_path):
    config, differences = batch_instance(tmp_path, "", concurrency=101)
    applied = cli("apply", "--config", config, "--input", differences)
    assert applied.returncode == 1
    assert "1 to 100" in applied.stderr
    assert not (tmp_path / "ocr-argv").exists()  # Config rejected before mdocr ran.


def test_missing_journal_does_not_allow_resume_or_next_run_to_redispatch(tmp_path):
    config, differences = batch_instance(
        tmp_path,
        """(root / 'accepted').write_text('request already accepted')
raise SystemExit(143)
""",
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 143, first.stderr
    (run,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    assert (tmp_path / "accepted").is_file()
    resumed = cli("apply", "--config", config, "--resume", run)
    assert resumed.returncode == 2, resumed.stderr
    repeated = cli("apply", "--config", config, "--input", differences)
    assert repeated.returncode == 2, repeated.stderr
    assert len((tmp_path / "ocr-argv").read_text().splitlines()) == 2
    manifest = json.loads(Path(json.loads(resumed.stdout)["manifest"]).read_text())
    assert len(manifest["documents"]) == 2
    assert {f["reason"] for f in manifest["failures"]} >= {"OCR_EXECUTION_UNVERIFIABLE"}


def test_enrolled_ocr_resume_does_not_send_newer_unknown_but_sends_peer(tmp_path):
    config, differences = batch_instance(
        tmp_path,
        """ids = [Path(row['path']).name for row in plan['documents']]
with (root / 'accepted').open('a') as stream:
    stream.write(json.dumps(ids) + '\\n')
if (root / 'unknown').exists():
    raise SystemExit(143)
result.write_text(json.dumps(payload([completed(d) for d in plan['documents']], 'completed')))
""",
    )
    tool = tmp_path / "batch_ocr.py"
    tool.write_text(
        tool.read_text().replace(
            "if args[0] == 'plan':",
            "if args[0] == 'plan':\n    if (root / 'pre_dispatch_loss').exists(): raise SystemExit(143)",
        )
    )
    args = ("apply", "--config", config)
    crash = tmp_path / "pre_dispatch_loss"
    crash.touch()
    assert cli(*args, "--input", differences).returncode == 143
    [old] = (tmp_path / "runs/funds").glob("run-*/run.json")
    crash.unlink()
    selection = json.loads(differences.read_bytes())
    selection["documents"].pop()
    differences.write_text(json.dumps(selection))
    unknown = tmp_path / "unknown"
    unknown.touch()
    assert cli(*args, "--input", differences).returncode == 143
    unknown.unlink()

    resumed = cli(*args, "--resume", old)
    assert resumed.returncode == 2, resumed.stderr
    first, second = map(json.loads, (tmp_path / "accepted").read_text().splitlines())
    assert len(first) == len(second) == 1 and first != second
    manifest = json.loads(Path(json.loads(resumed.stdout)["manifest"]).read_bytes())
    assert len(manifest["documents"]) == 2
    state = json.loads((tmp_path / "state/funds/state.json").read_bytes())
    docs = state["documents"]
    assert docs[team_id("42")]["steps"]["ocr"]["status"] == "unknown"
    assert docs[team_id("43")]["steps"]["ocr"]["status"] == "completed"


def test_ocr_does_not_dispatch_when_durable_book_admission_fails(tmp_path, monkeypatch):
    from dogpdkb import records
    from dogpdkb.apply import main

    config, differences = batch_instance(
        tmp_path, "raise AssertionError('unexpected run')\n"
    )
    save = records.Store.save

    def fail_durable_admission(store, *, durable=False):
        if durable and any(
            doc["steps"].get("ocr", {}).get("status") == "running"
            for doc in store.data["documents"].values()
        ):
            raise OSError("simulated lost durable book admission")
        save(store, durable=durable)

    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    monkeypatch.setattr(records.Store, "save", fail_durable_admission)
    assert main(["--config", str(config), "--input", str(differences)]) == 1
    calls = (tmp_path / "ocr-argv").read_text().splitlines()
    assert [json.loads(call)[0] for call in calls] == ["plan"]


def _native_instance(tmp_path, *, crash=False):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    remote_path = tmp_path / "remote.json"
    remote = json.loads(remote_path.read_text())
    for document_id, row in remote.items():
        row["body"] += f"![diagram](diagram-{document_id}.png)\n"
    remote_path.write_text(json.dumps(remote))
    exporter = tmp_path / "skylark.py"
    exporter.write_text(
        exporter.read_text().replace(
            "path.write_text(doc['body'])",
            "path.write_bytes(doc['body'].replace('\\n','\\r\\n').encode()); "
            "(path.parent/f\"diagram-{item['document_id']}.png\").write_bytes("
            "b'synthetic image bytes' + (b'' if item['document_id'] == '42' else b'-other'))",
        )
    )
    driver = tmp_path / "offline_mdocr.py"
    driver.write_text(
        """import os, sys
from pathlib import Path
from mdocr import runner
from mdocr.cli import main
root = Path(__file__).parent
with (root / 'commands').open('a') as log:
    log.write(sys.argv[1] + '\\n')
class SyntheticLLM:
    def __init__(self, model, effort):
        assert (model, effort) == ('openai/gpt-6.1-terra', 'medium')
    def preflight(self):
        return {}
    def convert(self, image, prompt, *, mermaid_diagnostics, repair):
        assert isinstance(image, bytes)
        with (root / 'requests').open('a') as log:
            log.write(repr(image) + '\\n')
        if (root / 'crash').exists():
            (root / 'crash').unlink()
            Path(sys.argv[sys.argv.index('--result')+1]).unlink()
            os._exit(143)
        return dict(image=dict(kind='text', markdown='Recognized offline.',
                               uncertainty=''), usage_tokens=12)
runner.LlmClient = SyntheticLLM
raise SystemExit(main())
"""
    )
    config.write_text(
        config.read_text().replace(
            json.dumps([sys.executable, "-m", "mdocr.cli"]),
            json.dumps([sys.executable, str(driver)]),
        )
        + "\n[ocr]\nrequest_budget=1\n"
    )
    if crash:
        (tmp_path / "crash").touch()
    return config, differences


@pytest.mark.parametrize("earlier_send", [False, True])
def test_cleanup_failure_proves_unsent_only_without_an_earlier_send(
    tmp_path, earlier_send
):
    from dogpdkb.ocr_batch import _load_plan, _request_history, _verified_result
    from mdocr.runner import Execution

    config, differences = _native_instance(tmp_path)
    config.write_text(
        config.read_text().replace("request_budget=1", "request_budget=3\nfix=1")
    )
    driver = tmp_path / "offline_mdocr.py"
    old = driver.read_text()
    marker = "        assert isinstance(image, bytes)"
    new = f"""
        if image == b'synthetic image bytes':
            marker = root / 'earlier-send'
            if {earlier_send!r} and not marker.exists():
                marker.touch()
                raise runner.LlmError('bad response', reason='response_json_invalid')
            raise runner.LlmError('local cleanup error', reason='svg_profile_cleanup_failed')
"""
    driver.write_text(
        old.replace(
            marker,
            "        (root / 'runtime-version').write_text(runner.__version__)\n"
            + marker
            + new,
        )
    )
    outcome = cli("apply", "--config", config, "--input", differences)
    assert outcome.returncode == 2, outcome.stderr
    (root,) = (tmp_path / "state/funds/ocr").glob("run-*")
    request = json.loads((root / "request.json").read_bytes())
    plan, docs = _load_plan(root, request)
    entry = next(
        row for row in request["documents"] if row["document_id"] == team_id("42")
    )
    report = json.loads((root / "result.json").read_bytes())
    row = next(row for row in report["documents"] if row["path"] == entry["path"])
    assert row["resources"][0].get("send_state") == (
        None if earlier_send else "not_sent"
    )
    expected = ("failed", "settled") if earlier_send else ("skipped", "not_sent")
    history = _request_history(report)
    assert history is not None
    execution = Execution(
        plan,
        report["plan_sha256"],
        root / "plan.json.execution.json",
        root / "result.json",
        root / "plan.json.evidence",
        False,
        version=(tmp_path / "runtime-version").read_text(),
    )
    peer = next(
        attempt
        for attempt in execution.state["requests"]
        if attempt["status"] == "success"
    )
    assert peer is not None
    peer_sha = peer.pop(
        "source_sha256"
    )  # Simulate a same-version historical admission.
    execution.plan_jobs()
    assert "source_sha256" not in peer
    assert execution.state["legacy_request_sources"] == {peer["key"]: peer_sha}
    legacy_history = _request_history(execution.state)
    assert legacy_history is not None
    assert legacy_history[peer_sha] == [peer]
    doc = docs[entry["path"]]
    assert (
        _verified_result(root, plan, doc, entry, row, lambda: history)[1:3] == expected
    )
    row["resources"][0]["send_state"] = "not_sent"
    if earlier_send:
        with pytest.raises(ValueError, match="Invalid OCR pre-send occurrence"):
            _verified_result(root, plan, doc, entry, row, lambda: history)
    else:
        other = next(d for d in docs.values() if d is not doc)
        (reused,) = other["images"]
        assert reused is not None
        doc["images"].append({**reused, "id": "cached-peer"})
        original = next(e for e in request["documents"] if e is not entry)
        entry["assets"].update(original["assets"])
        row["resources"].append(
            {
                "id": "cached-peer",
                "resource_id": reused["resource_id"],
                "status": "reused",
            }
        )
        assert _verified_result(root, plan, doc, entry, row, lambda: history)[1:3] == (
            "failed",
            "settled",
        )
        history.pop(reused["sha256"])
        assert _verified_result(root, plan, doc, entry, row, lambda: history)[1:3] == (
            "skipped",
            "not_sent",
        )
        with pytest.raises(ValueError, match="Invalid OCR pre-send occurrence"):
            _verified_result(root, plan, doc, entry, row)
    assert len((tmp_path / "requests").read_text().splitlines()) == 1


def test_malformed_partial_row_cannot_discard_independent_completed_peer(tmp_path):
    config, differences = _native_instance(tmp_path)
    driver = tmp_path / "offline_mdocr.py"
    driver.write_text(
        driver.read_text().replace(
            "raise SystemExit(main())",
            """code = main()
if sys.argv[1] == 'run':
    import json
    result = Path(sys.argv[sys.argv.index('--result')+1])
    plan = Path(sys.argv[sys.argv.index('--plan')+1])
    data = json.loads(result.read_bytes())
    good = next(row for row in data['documents'] if row['status'] == 'completed')
    bad = next(row for row in data['documents'] if row['status'] == 'partial')
    del bad['resources']
    data['documents'] = [bad, good]
    for path in (result, plan.with_name(plan.name + '.execution.json')):
        path.write_text(json.dumps(data))
raise SystemExit(code)""",
        )
    )
    outcome = cli("apply", "--config", config, "--input", differences)
    assert outcome.returncode == 2, outcome.stderr
    manifest = json.loads(Path(json.loads(outcome.stdout)["manifest"]).read_bytes())
    docs = {row["document_id"]: row for row in manifest["documents"]}
    assert docs[team_id("43")]["ocr_status"] == "unknown"
    assert docs[team_id("42")]["ocr_status"] == "completed"
    assert b"Recognized offline." in Path(docs[team_id("42")]["path"]).read_bytes()


def test_native_result_with_wrong_context_uses_raw_fallback(tmp_path):
    config, differences = _native_instance(tmp_path)
    driver = tmp_path / "offline_mdocr.py"
    driver.write_text(
        driver.read_text().replace(
            "raise SystemExit(main())",
            """code = main()
if sys.argv[1] == 'run':
    import json
    result = Path(sys.argv[sys.argv.index('--result') + 1])
    plan = Path(sys.argv[sys.argv.index('--plan') + 1])
    payload = json.loads(result.read_bytes())
    row = next(row for row in payload['documents'] if row['status'] == 'completed')
    row['ocr']['context_sha256'] = '0' * 64
    for path in (result, plan.with_name(plan.name + '.execution.json')):
        path.write_text(json.dumps(payload))
raise SystemExit(code)""",
        )
    )
    outcome = cli("apply", "--config", config, "--input", differences)
    assert outcome.returncode == 2, outcome.stderr
    manifest = json.loads(Path(json.loads(outcome.stdout)["manifest"]).read_bytes())
    docs = {row["document_id"]: row for row in manifest["documents"]}
    assert docs[team_id("42")]["ocr_status"] == "unknown"
    assert b"Recognized offline." not in Path(docs[team_id("42")]["path"]).read_bytes()
    assert docs[team_id("43")]["ocr_status"] == "unattempted"


def test_zero_frozen_budget_prevents_explicit_paddle_requests(tmp_path, monkeypatch):
    monkeypatch.delenv("PADDLEOCR_TOKEN", raising=False)
    config, differences = _native_instance(tmp_path)
    config.write_text(
        config.read_text().replace(
            "request_budget=1", 'mode="paddle"\nrequest_budget=0'
        )
    )
    paused = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert paused.returncode == 0, paused.stderr
    run_path = Path(json.loads(paused.stdout)["run"])
    config.write_text(
        config.read_text().replace("request_budget=0", "request_budget=9")
    )
    resumed = cli("apply", "--config", config, "--resume", run_path)
    assert resumed.returncode == 2, resumed.stderr
    run = json.loads(run_path.read_bytes())
    report = json.loads(Path(run["artifacts"]["ocr_report"]).read_bytes())
    assert report["budget"]["requests"] == 0
    assert report["causes"] == ["OCR_REQUEST_BUDGET_EXHAUSTED"]
    assert not (tmp_path / "requests").exists()


def test_mixed_batch_downloads_only_for_remote_sources(tmp_path):
    config, differences = _native_instance(tmp_path)
    source = tmp_path / "local"
    source.mkdir()
    (source / "note.md").write_text(
        "# Local\n\n![snapshot](https://example.invalid/local.png)\n"
    )
    books = tmp_path / "books.json"
    contents = json.loads(books.read_bytes())
    contents[0]["sources"].append(
        {"key": "local", "type": "directory", "path": str(source)}
    )
    books.write_text(json.dumps(contents))
    discovered = cli("discover", "--config", config, "--source", "local")
    assert discovered.returncode == 0, discovered.stderr
    local = json.loads(Path(json.loads(discovered.stdout)["differences"]).read_bytes())
    remote = json.loads(differences.read_bytes())
    remote["documents"].extend(local["documents"])
    remote["sources"].extend(local["sources"])
    remote["book"]["sources"].update(local["book"]["sources"])
    remote["run_id"] = local["run_id"]
    differences.write_text(json.dumps(remote))
    remote_path = tmp_path / "remote.json"
    docs = json.loads(remote_path.read_bytes())
    docs["42"]["body"] += "![live](https://example.invalid/remote.png)\n"
    remote_path.write_text(json.dumps(docs))
    driver = tmp_path / "offline_mdocr.py"
    driver.write_text(
        driver.read_text()
        .replace("from mdocr import runner", "from mdocr import plan, runner")
        .replace(
            "runner.LlmClient = SyntheticLLM",
            """def offline_fetch(url, session):
    with (root / 'downloads').open('a') as log:
        log.write(url + '\\n')
    return b'remote fixture image'
plan._fetch_remote = offline_fetch
runner.LlmClient = SyntheticLLM""",
        )
    )
    outcome = cli("apply", "--config", config, "--input", differences)
    assert outcome.returncode == 2, outcome.stderr
    assert (tmp_path / "downloads").read_text().splitlines() == [
        "https://example.invalid/remote.png"
    ]
    run = json.loads(Path(json.loads(outcome.stdout)["run"]).read_bytes())
    report = json.loads(Path(run["artifacts"]["ocr_report"]).read_bytes())
    assert len(report["documents"]) == 3
    assert any(
        row.get("reason") == "REMOTE_IMAGE_NOT_DOWNLOADED"
        for doc in report["documents"]
        for row in doc["resources"]
    )


def test_native_resume_keeps_original_admission_after_result_loss(tmp_path):
    config, differences = _native_instance(tmp_path, crash=True)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 143, first.stderr
    (run,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    config.write_text(
        config.read_text().replace("request_budget=1", "request_budget=99")
    )
    resumed = cli("apply", "--config", config, "--resume", run)
    assert resumed.returncode == 2, resumed.stderr
    assert (tmp_path / "commands").read_text().splitlines() == ["plan", "run", "run"]
    assert len((tmp_path / "requests").read_text().splitlines()) == 1
    report = json.loads(
        Path(json.loads(run.read_text())["artifacts"]["ocr_report"]).read_bytes()
    )
    assert report["budget"]["requests"] == 1
    assert report["requests"][0]["status"] == "unknown"
    assert {doc["resources"][0]["status"] for doc in report["documents"]} == {
        "unknown",
        "unattempted",
    }
    manifest = json.loads(Path(json.loads(resumed.stdout)["manifest"]).read_text())
    assert {doc["ocr_status"] for doc in manifest["documents"]} == {
        "unknown",
        "unattempted",
    }


def test_changed_mdocr_version_refuses_to_resend_unknown_old_admissions(tmp_path):
    config, differences = _native_instance(tmp_path, crash=True)
    config.write_text(
        config.read_text().replace("request_budget=1", "request_budget=3")
    )
    driver = tmp_path / "offline_mdocr.py"
    initial_driver = driver.read_text()
    driver.write_text(
        initial_driver.replace(
            "runner.LlmClient = SyntheticLLM",
            "runner.__version__ = '0.3.2'\nrunner.LlmClient = SyntheticLLM",
        )
    )
    assert cli("apply", "--config", config, "--input", differences).returncode == 143
    (run,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    (journal,) = (tmp_path / "state/funds/ocr").glob("*/plan.json.execution.json")
    before = journal.read_bytes()
    driver.write_text(
        initial_driver.replace(
            "runner.LlmClient = SyntheticLLM",
            "runner.__version__ = '0.3.3'\nrunner.LlmClient = SyntheticLLM",
        )
    )
    resumed = cli("apply", "--config", config, "--resume", run)
    # The running journal is not a bound terminal result. Preserve its
    # original admission and stop before handing anything to Corpus/Graphify.
    assert resumed.returncode == 1, resumed.stderr
    assert "OCR_RESULT_BINDING_INVALID" in resumed.stderr
    assert "saved OCR request identities do not match" in resumed.stderr
    assert journal.read_bytes() == before
    assert len((tmp_path / "requests").read_text().splitlines()) == 1


def test_batch_resume_rejects_retry_of_definitive_refusal():
    from dogpdkb.ocr_batch import _verify_running_journal

    state = {
        "status": "running",
        "budget": {"requests": 2},
        "requests": [
            {"key": "k", "status": "failed", "reason": "response_refusal"},
            {
                "key": "k",
                "status": "failed",
                "reason": "response_json_invalid",
                "fix_round": 1,
            },
        ],
    }
    with pytest.raises(ValueError, match="invalid retry round"):
        _verify_running_journal(state, 3, fix=2)


def test_running_journal_accepts_historical_paddle_fix_admission():
    from dogpdkb.ocr_batch import _verify_running_journal

    state = {
        "status": "running",
        "budget": {"requests": 2},
        "requests": [
            {"key": "k", "status": "failed", "reason": "OCR_PROVIDER_FAILED"},
            {"key": "k", "status": "admitted", "fix_round": 1},
        ],
    }
    _verify_running_journal(state, 3, fix=2)


@pytest.mark.parametrize(
    "reason", ["response_schema_invalid", "connection_outcome_unknown"]
)
def test_interrupted_batch_fix_stays_unknown_on_orchestrator_resume(tmp_path, reason):
    config, differences = _native_instance(tmp_path)
    config.write_text(
        config.read_text().replace("request_budget=1", "request_budget=4\nfix=2")
    )
    driver = tmp_path / "offline_mdocr.py"
    driver.write_text(
        driver.read_text().replace(
            "        if (root / 'crash').exists():",
            f"""        if image == b'synthetic image bytes':
            marker = root / 'once'
            if not marker.exists():
                marker.touch()
                raise runner.LlmError('synthetic failure', reason={reason!r})
            os._exit(143)
        if (root / 'crash').exists():""",
        )
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 143, first.stderr
    (run,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    (journal,) = (tmp_path / "state/funds/ocr").glob("*/plan.json.execution.json")
    initial = json.loads(journal.read_bytes())
    assert initial["budget"]["requests"] == 3
    fix = initial["requests"][-1]
    assert fix["status"] == "admitted" and fix["fix_round"] == 1
    resumed = cli("apply", "--config", config, "--resume", run)
    assert resumed.returncode == 2, resumed.stderr
    assert (tmp_path / "commands").read_text().splitlines() == ["plan", "run", "run"]
    assert len((tmp_path / "requests").read_text().splitlines()) == 3
    final = json.loads(journal.read_bytes())
    assert final["budget"] == initial["budget"]
    assert final["requests"][-1] == dict(
        fix, status="unknown", reason="OCR_OUTCOME_UNKNOWN"
    )
    manifest = json.loads(Path(json.loads(resumed.stdout)["manifest"]).read_bytes())
    assert {row["ocr_status"] for row in manifest["documents"]} == {
        "completed",
        "unknown",
    }


def test_legacy_unsettled_admission_cannot_resume_as_single_image_jobs(tmp_path):
    import hashlib

    config, differences = _native_instance(tmp_path, crash=True)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 143, first.stderr
    (run,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    (journal,) = (tmp_path / "state/funds/ocr").glob("*/plan.json.execution.json")
    plan = journal.with_name("plan.json")
    started = journal.with_name("started.json")
    legacy = json.loads(plan.read_bytes())
    del legacy["settings"]["llm_schema"]
    legacy_bytes = json.dumps(legacy).encode()
    plan.write_bytes(legacy_bytes)
    sha = hashlib.sha256(legacy_bytes).hexdigest()
    witness = json.loads(started.read_bytes())
    witness["plan.json"] = sha
    started.write_text(json.dumps(witness))
    admitted = json.loads(journal.read_bytes())
    admitted["plan_sha256"] = sha
    journal.write_text(json.dumps(admitted))
    before = journal.read_bytes()

    direct = cli("apply", "--config", config, "--resume", run)
    assert direct.returncode == 2, direct.stderr
    assert journal.read_bytes() == before
    assert (tmp_path / "commands").read_text().splitlines() == ["plan", "run"]
    manifest = json.loads(Path(json.loads(direct.stdout)["manifest"]).read_bytes())
    assert {row["ocr_status"] for row in manifest["documents"]} == {"unknown"}


def test_terminal_resume_checks_staged_damage_per_document(tmp_path):
    config, differences = _native_instance(tmp_path)
    config.write_text(
        config.read_text().replace("request_budget=1", "request_budget=2")
    )
    driver = tmp_path / "offline_mdocr.py"
    driver.write_text(
        driver.read_text().replace(
            "raise SystemExit(main())",
            """code = main()
raise SystemExit(143 if sys.argv[1] == 'run' and code == 0 else code)""",
        )
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 143, first.stderr
    (run,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    (report,) = (tmp_path / "state/funds/ocr").glob("*/result.json")
    first_row = json.loads(report.read_bytes())["documents"][0]
    Path(first_row["ocr"]["images"][0]["source_path"]).write_bytes(b"changed")

    resumed = cli("apply", "--config", config, "--resume", run)
    assert resumed.returncode == 2, resumed.stderr
    assert (tmp_path / "commands").read_text().splitlines() == ["plan", "run"]
    manifest = json.loads(Path(json.loads(resumed.stdout)["manifest"]).read_bytes())
    docs = {row["document_id"]: row for row in manifest["documents"]}
    assert docs[team_id("42")]["ocr_status"] == "unknown"
    assert docs[team_id("43")]["ocr_status"] == "completed"


def test_resume_rejects_journal_that_erases_budget_use(tmp_path):
    config, differences = _native_instance(tmp_path, crash=True)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 143, first.stderr
    (run,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    (journal,) = (tmp_path / "state/funds/ocr").glob("*/plan.json.execution.json")
    report = json.loads(journal.read_bytes())
    assert len(report["requests"]) == report["budget"]["requests"] == 1
    report["budget"]["requests"] = 0
    journal.write_text(json.dumps(report))

    resumed = cli("apply", "--config", config, "--resume", run)
    assert resumed.returncode == 2, resumed.stderr
    assert (tmp_path / "commands").read_text().splitlines() == ["plan", "run"]
    assert len((tmp_path / "requests").read_text().splitlines()) == 1
    manifest = json.loads(Path(json.loads(resumed.stdout)["manifest"]).read_bytes())
    assert {row["ocr_status"] for row in manifest["documents"]} == {"unknown"}


def test_native_exact_image_and_block_witnesses_survive_wash(tmp_path):
    config, differences = _native_instance(tmp_path)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 2, first.stderr
    run = Path(json.loads(first.stdout)["run"])
    artifacts = json.loads(run.read_bytes())["artifacts"]
    report = Path(artifacts["ocr_report"])
    plan = Path(artifacts["ocr_plan"])
    journal = Path(artifacts["ocr_journal"])
    originals = {path: path.read_bytes() for path in (report, plan, journal)}
    completed = next(
        row
        for row in json.loads(originals[report])["documents"]
        if row["status"] == "completed"
    )
    image = completed["ocr"]["images"][0]
    source, block, output = (
        Path(name)
        for name in (image["source_path"], image["artifact"], completed["output"])
    )
    originals.update({path: path.read_bytes() for path in (source, block, output)})
    assert originals[source] == b"synthetic image bytes"
    assert originals[block] in originals[output]
    assert originals[output].startswith(b"# Title\r\n")
    shutil.rmtree(run.parent)
    assert {path: path.read_bytes() for path in originals} == originals

    # A tampered OCR block cannot be reused, and the previous accepted request
    # must not be sent again. Only the previously unsent peer can be admitted.
    block.write_bytes(b"changed")
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 2, second.stderr
    manifest = json.loads(Path(json.loads(second.stdout)["manifest"]).read_text())
    docs = {row["document_id"]: row for row in manifest["documents"]}
    assert docs[team_id("42")]["ocr_status"] == "unknown"
    assert b"Recognized offline" not in Path(docs[team_id("42")]["path"]).read_bytes()
    assert docs[team_id("43")]["ocr_status"] == "completed"
    assert len((tmp_path / "requests").read_text().splitlines()) == 2


def test_global_invalid_binding_blocks_apply_and_resume_without_redispatch(tmp_path):
    config, differences = batch_instance(
        tmp_path, "result.write_text(json.dumps(payload([], 'completed')))\n"
    )
    args = ("apply", "--config", config)
    failed = cli(*args, "--input", differences)
    assert failed.returncode == 1 and "OCR_RESULT_BINDING_INVALID" in failed.stderr
    (run_path,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    run = json.loads(run_path.read_bytes())
    assert run["stage"] == "ocr" and run["status"] == "failed"
    assert "manifest" not in run["artifacts"] and "publication" not in run["artifacts"]
    assert not list((tmp_path / "corpus").glob("*.md"))
    (seal,) = (tmp_path / "state/funds/ocr").glob("*/settled.json")
    saved = seal.read_bytes()
    assert json.loads(saved)["reason"] == "OCR_RESULT_BINDING_INVALID"
    calls = (tmp_path / "ocr-argv").read_bytes()
    assert cli(*args, "--resume", run_path).returncode == 1
    assert (tmp_path / "ocr-argv").read_bytes() == calls
    assert seal.read_bytes() == saved


@pytest.mark.parametrize("mutation", ["wrong-plan", "missing-row", "running"])
def test_resume_checks_terminal_mirror_binding(tmp_path, mutation):
    config, differences = _native_instance(tmp_path)
    driver = tmp_path / "offline_mdocr.py"
    driver.write_text(
        driver.read_text().replace(
            "raise SystemExit(main())",
            f"""code = main()
if sys.argv[1] == 'run':
    import json
    result = Path(sys.argv[sys.argv.index('--result')+1])
    payload = json.loads(result.read_bytes())
    assert payload['status'] in {{'completed', 'partial'}}
    if {mutation!r} == 'wrong-plan': payload['plan_sha256'] = '0' * 64
    elif {mutation!r} == 'missing-row': payload['documents'].pop()
    else: payload.update(status='running', documents=[])
    result.write_text(json.dumps(payload))
    raise SystemExit(143)
raise SystemExit(code)""",
        )
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 143, first.stderr
    (run,) = (tmp_path / "runs/funds").glob("run-*/run.json")
    resumed = cli("apply", "--config", config, "--resume", run)
    if mutation == "running":
        assert resumed.returncode == 2, resumed.stderr
        assert json.loads(resumed.stdout)["manifest"]
        return
    assert resumed.returncode == 1 and "OCR_RESULT_BINDING_INVALID" in resumed.stderr
    assert not list((tmp_path / "corpus").glob("*.md"))
    (seal,) = (tmp_path / "state/funds/ocr").glob("*/settled.json")
    assert json.loads(seal.read_bytes())["reason"] == "OCR_RESULT_BINDING_INVALID"


def test_versioned_block_keeps_seal_reference_before_ocr_settlement(tmp_path):
    from dogpdkb.ocr_batch import InvalidOcrBatchBinding, require_prior_ocr_binding
    from dogpdkb.records import Store, atomic_json
    from dogpdkb.settings import load_config, select_book

    config, _ = instance(tmp_path)
    settings = load_config(config)
    store = Store(settings, select_book(settings, None))
    identity = team_id("42")
    old = "run-20261112-100000-11111111"
    request = store.path.parent / "ocr" / f"{old}-revision" / "request.json"
    store.attempt(identity, "ocr", "v2", old, input=str(request))
    atomic_json(
        request.with_name("settled.json"), {"reason": "OCR_RESULT_BINDING_INVALID"}
    )
    with pytest.raises(InvalidOcrBatchBinding):
        require_prior_ocr_binding(store, [{"document_id": identity, "version": "v2"}])
    store.attempt(identity, "ocr", "v3", "run-20261112-100100-22222222")
    with pytest.raises(InvalidOcrBatchBinding):
        require_prior_ocr_binding(store, [{"document_id": identity, "version": "v2"}])
    require_prior_ocr_binding(store, [{"document_id": identity, "version": "v3"}])
