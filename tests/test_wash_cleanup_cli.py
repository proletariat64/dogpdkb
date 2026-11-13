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
                    "url": "https://example.invalid/team/book",
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
        """if tool == 'mdocr':
    def flag(name): return pathlib.Path(args[args.index(name)+1])
    doc=json.loads(flag('--manifest').read_text())['documents'][0]
    if doc['document_id']=='2':
        output=flag('--output-dir')/'failed.md'; output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(pathlib.Path(doc['path']).read_text())
        flag('--report').write_text(json.dumps({'schema_version':'mdocr-batch-1.0','status':'partial','documents':[{'document_id':'2','status':'partial','output':str(output),'resources':[{'resource_id':'2.png','status':'failed','source_path':str(pathlib.Path(doc['path']).parent/'2.png')}]}]}))
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
    assert (
        json.loads((tmp_path / "corpus/graphify-out/graph.json").read_text())["nodes"]
        == []
    )


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
        assert report["candidate"] is None
    assert not list(run.parent.glob("graphify-*/graphify-out"))


@pytest.mark.parametrize("fault", ["blocked-directory", "malformed-plan"])
def test_cleanup_failure_reports_stderr_without_changing_sync_result(tmp_path, fault):
    config, tool = instance(tmp_path)
    injection = (
        "    blocked=flag('--out').parent/'blocked';blocked.mkdir();(blocked/'file').write_text('keep');blocked.chmod(0o555)\n"
        if fault == "blocked-directory"
        else "    (flag('--out').parent/'plan.json').write_text('invalid')\n"
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


@pytest.mark.parametrize("mode,expected", [("invalid", 1), ("interrupt", 130)])
def test_whole_batch_failure_or_interruption_keeps_run_untouched(
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
    assert "cleanup" not in record
    assert (run.parent / "raw/0.md").is_file()
    assert list(run.parent.glob("graphify-*"))


def test_failed_export_keeps_existing_markdown_picture_and_report(tmp_path):
    from PIL import Image

    config, tool = instance(tmp_path)
    Image.new("RGB", (2, 2), "red").save(tmp_path / "fixture.png")
    script = tool.read_text().replace(
        "        rows.append({**doc,",
        """        if doc['document_id']=='2':
            (path.parent/'2.png').write_bytes((root/'fixture.png').read_bytes())
            path.write_text(path.read_text()+'\\n![failed](2.png)\\n')
        rows.append({**doc,""",
    )
    script = script.replace(
        "    write(flag('--output'), {'schema':'skylark-export-result-1'",
        """    if rows[0]['document_id']=='2': rows[0]['status']='failed'
    write(flag('--output'), {'schema':'skylark-export-result-1'""",
    )
    tool.write_text(script)
    result = cli("sync", "--config", config)
    assert result.returncode == 2, result.stderr
    run = Path(json.loads(result.stdout)["run"])
    record = json.loads(run.read_text())
    failure = next(row for row in record["failures"] if row["step"] == "export")
    assert Path(failure["input"]).is_file()
    assert Path(failure["report"]).is_file()
    assert Path(failure["input"]).with_name("2.png").is_file()
    assert not (run.parent / "raw/0.md").exists()
