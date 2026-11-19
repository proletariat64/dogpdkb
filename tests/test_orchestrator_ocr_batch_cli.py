"""Offline OCR batch acceptance at the public apply/tool/artifact seams."""

import json
import shutil
import sys
from pathlib import Path

from tests.source_fixture import team_id
from tests.test_orchestrator_apply_cli import cli, instance, two_sources


def batch_instance(tmp_path, body):
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
                    prompt_sha256=sha(settings['prompt'].encode()), schema='mdocr-image-patches-1')))
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
        + "\n[ocr]\nrequest_budget=1\n"
    )
    return config, differences


def test_partial_batch_accepts_verified_peer_and_preserves_exact_output(tmp_path):
    config, differences = batch_instance(
        tmp_path,
        """first, second = plan['documents']
result.write_text(json.dumps(payload([completed(first), dict(
    document_id=second['document_id'], path=second['path'], status='partial', output=None,
    resources=[])], 'partial')))
raise SystemExit(2)
""",
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
    assert flags["--concurrency"] == "3"
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
    for row in remote.values():
        row["body"] += "![diagram](diagram.png)\n"
    remote_path.write_text(json.dumps(remote))
    exporter = tmp_path / "skylark.py"
    exporter.write_text(
        exporter.read_text().replace(
            "path.write_text(doc['body'])",
            "path.write_bytes(doc['body'].replace('\\n','\\r\\n').encode()); "
            "(path.parent/'diagram.png').write_bytes(b'synthetic image bytes')",
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
    def convert(self, body, images, prompt):
        with (root / 'requests').open('a') as log:
            log.write(images[0]['path'] + '\\n')
        if (root / 'crash').exists():
            (root / 'crash').unlink()
            Path(sys.argv[sys.argv.index('--result')+1]).unlink()
            os._exit(143)
        return dict(patches=[dict(id=image['id'], kind='text',
                                  markdown='Recognized offline.', uncertainty='')
                             for image in images], usage_tokens=12)
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
