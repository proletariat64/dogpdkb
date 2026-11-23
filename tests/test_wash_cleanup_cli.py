"""Completed sync cleanup through public CLI and on-disk artifacts."""

import json
import sys
from pathlib import Path

import pytest

from scripts.smoke_install import REMOTE_CLI
from tests.test_orchestrator_apply_cli import cli


def instance(tmp_path):
    tool = tmp_path / "remote.py"
    tool.write_text(REMOTE_CLI)
    (tmp_path / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": "funds",
                    "name": "Funds",
                    "sources": [
                        {
                            "key": "team",
                            "type": "yuque",
                            "url": "https://example.invalid/team/book",
                        }
                    ],
                    "corpus": "corpus",
                }
            ]
        )
    )
    config = tmp_path / "instance.toml"
    commands = "\n".join(
        f"{name}={json.dumps([sys.executable, str(tool), name])}"
        for name in ("skylark", "graphify", "mdcompare", "mdocr")
    )
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\nocr_cache="cache"\n[tools]\n'
        + commands
        + "\n"
    )
    return config, tool


def test_completed_sync_cleans_bulk_and_keeps_status_and_next_sync(tmp_path):
    config, _ = instance(tmp_path)
    result = cli("sync", "--config", config)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    run = Path(output["run"])
    record = json.loads(run.read_text())
    assert record["cleanup"]["status"] == "completed"
    assert not (run.parent / "raw").exists()
    assert not list(run.parent.glob("graphify-*"))
    assert record["status"] == "completed"
    documents = json.loads((tmp_path / "state/funds/state.json").read_text())[
        "documents"
    ]
    for document in documents.values():
        witness = document["corpus"]["input_copy"]
        assert document["steps"]["graph"]["input_copy"] == witness
        assert Path(witness).is_file()
    status = cli("status", "--config", config, "--json")
    assert json.loads(status.stdout)["status"] == "completed"
    before = (tmp_path / "commands.jsonl").read_text().splitlines()
    again = cli("sync", "--config", config)
    assert again.returncode == 0, again.stderr
    after = (tmp_path / "commands.jsonl").read_text().splitlines()
    assert [json.loads(line)[:2] for line in after[len(before) :]] == [
        ["skylark", "listing"]
    ]


def test_partial_ocr_keeps_failed_markdown_image_report_but_cleans_success(tmp_path):
    from PIL import Image

    config, tool = instance(tmp_path)
    Image.new("RGB", (2, 2), "red").save(tmp_path / "fixture.png")
    script = tool.read_text()
    script = script.replace(
        "if tool in ('mdcompare', 'mdocr'):",
        """if tool == 'mdocr' and args[0] == 'run':
    import hashlib
    def flag(name): return pathlib.Path(args[args.index(name)+1])
    plan=flag('--plan'); doc=json.loads(plan.read_text())['documents'][0]
    wrapper=plan.with_name(plan.name.replace('-plan.json','-request.json'))
    requested=json.loads(wrapper.read_text())['documents'][0]
    if requested['document_id']=='2':
        flag('--result').write_text(json.dumps({'schema_version':'mdocr-result-2','plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest(),'status':'partial','documents':[{'document_id':doc['document_id'],'status':'partial','output':None,'resources':[{'resource_id':'2.png','status':'failed','source_path':str(pathlib.Path(doc['path']).parent/'2.png')}]}]}))
        raise SystemExit(2)
if tool in ('mdcompare', 'mdocr'):""",
    )
    script = script.replace(
        "        rows.append({**doc,",
        """        if doc['document_id']=='2':
            image=path.parent/'2.png';image.write_bytes((root/'fixture.png').read_bytes())
            path.write_text(path.read_text()+'\\n![failed](2.png)\\n')
        rows.append({**doc,""",
    )
    tool.write_text(script)
    result = cli("sync", "--config", config)
    assert result.returncode == 2, result.stderr
    run = Path(json.loads(result.stdout)["run"])
    record = json.loads(run.read_text())
    assert record["cleanup"]["status"] == "completed"
    assert record["status"] == "partial"
    assert not (run.parent / "raw/0.md").exists()
    assert (run.parent / "raw/1.md").is_file()
    assert (run.parent / "raw/2.png").read_bytes() == (
        tmp_path / "fixture.png"
    ).read_bytes()
    failure = next(row for row in record["failures"] if row["step"] == "ocr")
    assert Path(failure["input"]).is_file()
    assert Path(failure["report"]).is_file()
    assert not list(run.parent.glob("graphify-*"))
    corpus = tmp_path / "corpus"
    graph = json.loads((corpus / "graphify-out/graph.json").read_text())
    published = {p.name for p in corpus.glob("*.md")}
    assert len(published) == 2
    assert {node["source_file"] for node in graph["nodes"]} == published


def test_paused_run_is_preserved_and_cleaned_run_cannot_resume(tmp_path):
    config, _ = instance(tmp_path)
    paused = cli("sync", "--config", config, "--stop-after", "plan")
    assert paused.returncode == 0, paused.stderr
    run = Path(json.loads(paused.stdout)["run"])
    assert "cleanup" not in json.loads(run.read_text())
    assert (run.parent / "raw/0.md").is_file()
    completed = cli("sync", "--config", config, "--resume", run)
    assert completed.returncode == 0, completed.stderr
    before = run.read_bytes()
    rejected = cli("sync", "--config", config, "--resume", run)
    assert rejected.returncode == 1
    assert "cleaned" in rejected.stderr
    assert run.read_bytes() == before


def test_failed_graph_keeps_input_and_report_without_candidate_graph(tmp_path):
    config, tool = instance(tmp_path)
    tool.write_text(
        tool.read_text().replace(
            "    graph=flag('--out')",
            "    raise SystemExit(1)\n    graph=flag('--out')",
        )
    )
    result = cli("sync", "--config", config)
    assert result.returncode == 2, result.stderr
    run = Path(json.loads(result.stdout)["run"])
    record = json.loads(run.read_text())
    assert record["cleanup"]["status"] == "completed"
    assert record["status"] == "partial"
    failures = [row for row in record["failures"] if row["step"] == "graph"]
    assert len(failures) == 2
    for failure in failures:
        assert Path(failure["input"]).is_file()
        report = json.loads(Path(failure["report"]).read_text())
        assert "candidate" not in report
        expected = [
            sys.executable,
            str(tool),
            "graphify",
            "extract",
            report["corpus"],
            "--out",
            report["corpus"],
            "--no-cluster",
            "--no-gitignore",
            "--backend",
            "openai-oauth",
            "--max-concurrency",
            "3",
        ]
        assert failure["argv"] == expected
        assert report["argv"] == expected
        assert Path(report["native_log"]).is_file()
    assert not list(run.parent.glob("graphify-*/graphify-out"))

    second = cli("sync", "--config", config)
    assert second.returncode == 2, second.stderr
    next_run = Path(json.loads(second.stdout)["run"])
    assert next_run != run
    assert any(
        row["reason"] == "SAME_VERSION_NOT_RETRIED"
        for row in json.loads(next_run.read_text())["failures"]
    )
    calls = [
        json.loads(line)
        for line in (tmp_path / "commands.jsonl").read_text().splitlines()
    ]
    assert sum(row[:2] == ["graphify", "extract"] for row in calls) == 1


@pytest.mark.parametrize("tool_exit,expected", [(1, 2), (130, 130)])
def test_sync_graph_retry_uses_retained_manifest_without_repeating_other_tools(
    tmp_path, tool_exit, expected
):
    config, tool = instance(tmp_path)
    original = tool.read_text()
    tool.write_text(
        original.replace(
            "    graph=flag('--out')",
            f"    raise SystemExit({tool_exit})\n    graph=flag('--out')",
        )
    )
    first = cli("sync", "--config", config)
    assert first.returncode == expected, first.stderr
    run = Path(json.loads(first.stdout)["run"])
    before = json.loads(run.read_text())
    calls_before = (tmp_path / "commands.jsonl").read_text().splitlines()
    tool.write_text(original)

    result = cli("sync", "--config", config, "--resume", run, "--retry-graph")

    assert result.returncode in {0, 2}, result.stderr
    calls_after = (tmp_path / "commands.jsonl").read_text().splitlines()
    assert [json.loads(line)[:2] for line in calls_after[len(calls_before) :]] == [
        ["graphify", "extract"]
    ]
    after = json.loads(run.read_text())
    assert [row["reason"] for row in after["failures"]] == [
        row["reason"] for row in before["failures"]
    ]
    assert all(Path(row["report"]).is_file() for row in after["failures"])
    assert after["counts"] == {"total": 2, "completed": 2, "failed": 0}
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert all(
        doc["graph"]["status"] == "completed" for doc in state["documents"].values()
    )


def test_sync_graph_retry_requires_explicit_saved_run(tmp_path):
    config, _ = instance(tmp_path)
    result = cli("sync", "--config", config, "--retry-graph")
    assert result.returncode == 64
    assert "requires --resume" in result.stderr
    assert not (tmp_path / "commands.jsonl").exists()


def test_graph_recovery_preserves_cleaned_unknown_ocr_failure_bundle(tmp_path):
    from PIL import Image

    config, tool = instance(tmp_path)
    Image.new("RGB", (8, 8), "red").save(tmp_path / "fixture.png")
    original = tool.read_text()
    script = (
        original.replace(
            "        rows.append({**doc,",
            "        if doc['document_id']=='2':\n            image=path.parent/'2.png';image.write_bytes((root/'fixture.png').read_bytes());path.write_text(path.read_text()+'\\n![failed](2.png)\\n')\n        rows.append({**doc,",
        )
        .replace(
            "if tool in ('mdcompare', 'mdocr'):",
            "if tool == 'mdocr' and args[0] == 'run': raise SystemExit(1)\nif tool in ('mdcompare', 'mdocr'):",
        )
        .replace(
            "    graph=flag('--out')",
            "    raise SystemExit(1)\n    graph=flag('--out')",
        )
    )
    tool.write_text(script)
    first = cli("sync", "--config", config)
    assert first.returncode == 2, first.stderr
    run = Path(json.loads(first.stdout)["run"])
    assert json.loads(run.read_text())["cleanup"]["status"] == "completed"
    retained = {
        path: path.read_bytes()
        for path in run.parent.rglob("*")
        if path.is_file() and path.name not in {"run.json", "manifest.json"}
    }
    assert (run.parent / "raw/2.png") in retained
    snapshot = json.loads(
        cli("status", "--config", config, "--run", run, "--json").stdout
    )
    [failure] = [
        item
        for item in snapshot["handoff"]["items"]
        if item["phase"] == "ocr" and item["source_document_id"] == "2"
    ]
    assert failure["send_state"] == "unknown" and failure["retry_scope"] == "none"
    [request] = [
        Path(path) for path in failure["evidence"] if Path(path).name == "request.json"
    ]
    assert request.is_relative_to(tmp_path / "state/funds/ocr")
    retained[request] = request.read_bytes()
    tool.write_text(original)

    result = cli("sync", "--config", config, "--resume", run, "--retry-graph")

    assert result.returncode in {0, 2}, result.stderr
    for path, content in retained.items():
        assert path.is_file(), f"recovery discarded failure evidence: {path}"
        assert path.read_bytes() == content
    assert not list(run.parent.glob("graphify-*/graphify-out"))


@pytest.mark.parametrize("fault", ["blocked-directory", "malformed-plan"])
def test_cleanup_failure_reports_stderr_without_changing_sync_result(tmp_path, fault):
    config, tool = instance(tmp_path)
    injection = (
        "    blocked=next((root/'runs/funds').glob('run-*'))/'blocked';blocked.mkdir();(blocked/'file').write_text('keep');blocked.chmod(0o555)\n"
        if fault == "blocked-directory"
        else "    (next((root/'runs/funds').glob('run-*'))/'plan.json').write_text('invalid')\n"
    )
    tool.write_text(
        tool.read_text().replace(
            "    graph=flag('--out')", injection + "    graph=flag('--out')"
        )
    )
    try:
        result = cli("sync", "--config", config)
        assert result.returncode == 0, result.stderr
        run = Path(json.loads(result.stdout)["run"])
        record = json.loads(run.read_text())
        assert record["status"] == "completed"
        assert record["cleanup"]["status"] == "failed"
        assert "cleanup" in result.stderr
    finally:
        for directory in (tmp_path / "runs").glob("funds/run-*/blocked"):
            directory.chmod(0o755)


@pytest.mark.parametrize("mode,expected", [("invalid", 2), ("interrupt", 130)])
def test_invalid_or_interrupted_graph_retains_failure_evidence(
    tmp_path, mode, expected
):
    config, tool = instance(tmp_path)
    injection = (
        "    raise SystemExit(130)\n"
        if mode == "interrupt"
        else "    flag('--out').joinpath('graphify-out').mkdir(exist_ok=True);flag('--out').joinpath('graphify-out/graph.json').write_text('invalid');raise SystemExit(0)\n"
    )
    tool.write_text(
        tool.read_text().replace(
            "    graph=flag('--out')", injection + "    graph=flag('--out')"
        )
    )
    result = cli("sync", "--config", config)
    assert result.returncode == expected, result.stderr
    run = (
        Path(json.loads(result.stdout)["run"])
        if result.stdout.strip()
        else next((tmp_path / "runs/funds").glob("run-*/run.json"))
    )
    record = json.loads(run.read_text())
    assert (tmp_path / "corpus/graphify-out/graph.json").exists() == (mode == "invalid")
    if mode == "interrupt":
        assert "cleanup" not in record
        assert (run.parent / "raw/0.md").is_file()
    else:
        assert (
            record["status"] == "partial" and record["cleanup"]["status"] == "completed"
        )
    [report] = {row["report"] for row in record["failures"] if row["step"] == "graph"}
    assert json.loads(Path(report).read_bytes())["status"] == "unknown"


@pytest.mark.parametrize("image_target", ["2.png", "../images/2.png"])
@pytest.mark.parametrize("catalog_mode", ["available", "unavailable", "interrupted"])
def test_failed_export_keeps_existing_markdown_picture_and_report(
    tmp_path, image_target, catalog_mode
):
    from PIL import Image

    config, tool = instance(tmp_path)
    Image.new("RGB", (2, 2), "red").save(tmp_path / "fixture.png")
    script = tool.read_text().replace(
        "        rows.append({**doc,",
        """        if doc['document_id']=='2':
            image=path.parent/IMAGE_TARGET; image.parent.mkdir(parents=True,exist_ok=True); image.write_bytes((root/'fixture.png').read_bytes())
            path.write_text(path.read_text()+'\\n![failed]('+IMAGE_TARGET+')\\n')
        rows.append({**doc,""",
    )
    script = script.replace(
        "    write(flag('--output'), {'schema':'skylark-export-result-1'",
        """    if rows[0]['document_id']=='2': rows[0]['status']='failed'
    write(flag('--output'), {'schema':'skylark-export-result-1'""",
    )
    script = script.replace("IMAGE_TARGET", repr(image_target))
    if catalog_mode != "available":
        script = script.replace(
            "if tool in ('mdcompare', 'mdocr'):",
            "if tool == 'mdcompare' and args[0] == 'catalog' and any('failed-export-descriptors' in arg for arg in args): raise SystemExit(EXIT_CODE)\nif tool in ('mdcompare', 'mdocr'):",
        )
    script = script.replace(
        "EXIT_CODE", "130" if catalog_mode == "interrupted" else "1"
    )
    tool.write_text(script)
    result = cli("sync", "--config", config)
    assert result.returncode == (130 if catalog_mode == "interrupted" else 2), (
        result.stderr
    )
    run = (
        Path(json.loads(result.stdout)["run"])
        if result.stdout.strip()
        else next((tmp_path / "runs/funds").glob("run-*/run.json"))
    )
    record = json.loads(run.read_text())
    failure = next(row for row in record["failures"] if row["step"] == "export")
    assert Path(failure["input"]).is_file()
    assert Path(failure["report"]).is_file()
    assert (Path(failure["input"]).parent / image_target).is_file()
    if catalog_mode == "interrupted":
        assert record["status"] == "interrupted"
        assert "cleanup" not in record
    elif catalog_mode == "available":
        assert not (run.parent / "raw/0.md").exists()
        assert record["cleanup"]["status"] == "completed"
    else:
        assert record["cleanup"]["status"] == "failed"
        assert (run.parent / "raw/0.md").exists()
        assert "cleanup" in result.stderr


def test_failed_catalog_preserves_uncertain_image_bundle_and_original_exit(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PADDLEOCR_TOKEN", raising=False)
    from PIL import Image

    config, tool = instance(tmp_path)
    Image.new("RGB", (8, 8), "red").save(tmp_path / "fixture.png")
    script = tool.read_text().replace(
        "        rows.append({**doc,",
        "        if doc['document_id']=='2':\n            image=path.parent.parent/'images'/'2.png';image.parent.mkdir(parents=True,exist_ok=True);image.write_bytes((root/'fixture.png').read_bytes());path.write_text(path.read_text()+'\\n![failed](../images/2.png)\\n')\n        rows.append({**doc,",
    )
    script = script.replace(
        "if tool in ('mdcompare', 'mdocr'):",
        "if tool == 'mdcompare' and args[0] == 'catalog' and any('incoming-descriptors' in arg for arg in args): raise SystemExit(1)\nif tool in ('mdcompare', 'mdocr'):",
    )
    tool.write_text(script)
    result = cli("sync", "--config", config)
    assert result.returncode == 2, result.stderr
    run = Path(json.loads(result.stdout)["run"])
    record = json.loads(run.read_text())
    assert record["cleanup"]["status"] == "failed"
    assert "resource catalog unavailable" in record["cleanup"]["reason"]
    assert (run.parent / "raw/1.md").is_file()
    assert (run.parent / "images/2.png").is_file()
