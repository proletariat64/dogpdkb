import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import apply_sync_updates as apply_sync
from scripts import get_sync_updates as get_sync
from scripts import sync_knowledge_base as pipeline
from tests import test_apply_recovery as recovery

apply_run = recovery.run


def fake_runner(staging: Path, calls: list[str], fail_graph: bool = False):
    def run(command: list[str]):
        script = Path(command[1]).name
        calls.append(script)
        if script == "get_sync_updates.py":
            output = Path(command[command.index("-o") + 1])
            output.mkdir(parents=True, exist_ok=True)
            (output / "sync-updates-uat-20261102.json").write_text(
                json.dumps(
                    {
                        "new": [],
                        "updated": [],
                        "renamed": [],
                        "moved": [],
                        "deleted": [],
                    }
                )
            )
        elif script == "apply_sync_updates.py":
            assert command[command.index("--cutoff") + 1] == "2026-01-01"
            (staging / "manifest.json").write_text(
                json.dumps({"exported": [], "deleted": [], "skipped": []})
            )
        elif script == "update_graph.py":
            if fail_graph:
                raise RuntimeError("injected graph failure")
            target = staging / "graph-update" / "result.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"status": "completed"}))
        elif script == "publish_web.py":
            output = Path(command[command.index("-o") + 1])
            (output / "map").mkdir(parents=True, exist_ok=True)
            (output / "catalog.json").write_text(
                json.dumps({"stats": {"documents": 1, "nodes": 2, "edges": 3}})
            )
            (output / "map" / "map-data.json").write_text("{}")
        else:
            raise AssertionError(script)

    return run


def options(tmp_path: Path):
    return pipeline.PipelineOptions(
        input_dir=tmp_path / "corpus",
        source_url="https://example.invalid/book",
        staging=tmp_path / "staging",
        cutoff=None,
        dry_run=False,
        resume=False,
        graph_dir=tmp_path / "corpus" / "graphify-out",
        web_dir=tmp_path / "web",
        graphify_python=Path("/usr/local/python3/bin/python3.12"),
        skip_graph=False,
        skip_web=False,
    )


def test_pipeline_resumes_after_graph_failure(tmp_path: Path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("doc")
    calls = []

    try:
        pipeline.run_pipeline(
            config, runner=fake_runner(config.staging, calls, fail_graph=True)
        )
    except RuntimeError as error:
        assert "injected graph failure" in str(error)
    else:
        raise AssertionError("graph failure was not propagated")
    assert calls == [
        "get_sync_updates.py",
        "apply_sync_updates.py",
        "update_graph.py",
    ]

    calls.clear()
    config.resume = True
    result = pipeline.run_pipeline(
        config, runner=fake_runner(config.staging, calls, fail_graph=False)
    )

    assert calls == ["update_graph.py", "publish_web.py"]
    assert result["status"] == "completed"
    assert result["stages"] == {
        "plan": "completed",
        "corpus": "completed",
        "graph": "completed",
        "web": "completed",
    }


def test_pipeline_dry_run_stops_after_plan(tmp_path: Path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("doc")
    config.dry_run = True
    calls = []

    result = pipeline.run_pipeline(config, runner=fake_runner(config.staging, calls))

    assert calls == ["get_sync_updates.py"]
    assert result["status"] == "dry_run"
    assert not (config.web_dir / "catalog.json").exists()


def test_rebase_reuses_published_manifest_after_baseline_conflict(tmp_path: Path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("old")
    calls = []
    original = fake_runner(config.staging, calls)
    retained = {
        "exported": [{"id": 1, "file": "new-2026-11-05-[1].md"}],
        "deleted": [],
        "skipped": [],
    }

    def conflict(command):
        original(command) if Path(command[1]).name != "update_graph.py" else None
        if Path(command[1]).name == "apply_sync_updates.py":
            (config.staging / "manifest.json").write_text(json.dumps(retained))
            (config.input_dir / "new-2026-11-05-[1].md").write_text("new")
        if Path(command[1]).name == "update_graph.py":
            raise RuntimeError("graph artifact changed concurrently")

    with pytest.raises(RuntimeError, match="changed concurrently"):
        pipeline.run_pipeline(config, runner=conflict)
    config.resume = True
    config.rebase_graph = True
    calls.clear()

    def recover(command):
        if Path(command[1]).name == "update_graph.py":
            assert "--rebase" in command
            manifest = Path(command[command.index("--manifest") + 1])
            assert json.loads(manifest.read_text()) == retained
        original(command)

    result = pipeline.run_pipeline(config, runner=recover)
    assert calls == ["update_graph.py", "publish_web.py"]
    assert result["status"] == "completed"


def test_rebase_requires_existing_resume(tmp_path: Path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    config.rebase_graph = True
    with pytest.raises(ValueError, match="requires --resume"):
        pipeline.run_pipeline(config, runner=lambda command: pytest.fail(str(command)))


@pytest.mark.parametrize(
    "manifest",
    [
        {"exported": [], "skipped": [{"id": 2, "reason": "upstream unavailable"}]},
        {
            "status": "failed",
            "exported": [],
            "failures": [{"stage": "ocr", "file": "doc-2.md", "reason": "unavailable"}],
        },
        {"status": "staged", "exported": [], "failures": []},
    ],
)
def test_pipeline_rejects_incomplete_manifest_before_corpus_completion(
    tmp_path, manifest
):
    config = options(tmp_path)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("doc")
    calls = []
    original = fake_runner(config.staging, calls)

    def run(command):
        original(command)
        if Path(command[1]).name == "apply_sync_updates.py":
            (config.staging / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(RuntimeError):
        pipeline.run_pipeline(config, runner=run)

    state = json.loads((config.staging / "pipeline-state.json").read_text())
    assert state["status"] == "failed"
    assert state["stages"].get("corpus") != "completed"
    assert calls == ["get_sync_updates.py", "apply_sync_updates.py"]
    config.resume = True
    calls.clear()
    result = pipeline.run_pipeline(config, runner=original)
    assert result["status"] == "completed"
    assert calls == ["apply_sync_updates.py", "update_graph.py", "publish_web.py"]


@pytest.mark.parametrize("cancel", [False, True])
def test_plan_failure_reports_safe_resume_and_full_listing_refresh(
    tmp_path, capsys, cancel
):
    config = options(tmp_path)
    config.input_dir.mkdir()

    def fail(command):
        if cancel:
            raise KeyboardInterrupt()
        raise RuntimeError(
            "skylark_doc_list book_id=123 offset=50 Bearer credential-value "
            + "x" * 1000
        )

    with pytest.raises(KeyboardInterrupt if cancel else RuntimeError):
        pipeline.run_pipeline(config, runner=fail)

    state = json.loads((config.staging / "pipeline-state.json").read_text())
    output = capsys.readouterr().err
    assert "[plan] failed" in output
    assert "resume will refetch the entire listing" in output
    assert "Retry:" in output
    assert f"--staging {config.staging}" in output
    assert "credential-value" not in output + state["error"]
    assert len(state["error"]) <= 300
    assert state["status"] == "failed"
    if not cancel:
        assert "offset=50" in output


def test_pipeline_resumes_real_apply_without_repeating_plan_or_successful_exports(
    apply_run, tmp_path, monkeypatch, capsys
):
    _invoke, corpus, staging, failures, exports, ocr, plan = apply_run
    config = options(tmp_path)
    config.input_dir = corpus
    config.staging = staging
    before = recovery.snapshot(corpus)
    calls = []
    original = fake_runner(staging, calls)

    def run(command):
        script = Path(command[1]).name
        if script == "get_sync_updates.py":
            original(command)
            output = Path(command[command.index("-o") + 1])
            next(output.glob("sync-updates-*.json")).write_text(plan.read_text())
        elif script == "apply_sync_updates.py":
            calls.append(script)
            monkeypatch.setattr(sys, "argv", command[1:])
            apply_sync.main()
        else:
            original(command)

    failures.add(2)
    with pytest.raises(SystemExit) as error:
        pipeline.run_pipeline(config, runner=run)
    assert error.value.code != 0
    assert recovery.snapshot(corpus) == before
    assert calls == ["get_sync_updates.py", "apply_sync_updates.py"]
    state = json.loads((staging / "pipeline-state.json").read_text())
    assert state["stages"] == {"plan": "completed", "corpus": "retry_required"}
    output = capsys.readouterr().err
    assert "completed_exports=1 failed=1" in output
    assert "unit=2" in output
    assert "a completed plan will be reused" in output
    exports.clear()
    ocr.clear()
    calls.clear()
    failures.clear()
    config.resume = True

    result = pipeline.run_pipeline(config, runner=run)

    assert result["status"] == "completed"
    assert calls == ["apply_sync_updates.py", "update_graph.py", "publish_web.py"]
    assert exports == [2]
    assert ocr == ["Doc-2-2026-11-01-[2].md"]
    assert (corpus / "Doc-1-2026-11-01-[1].md").read_text() == "new 1"
    assert (corpus / "Doc-2-2026-11-01-[2].md").read_text() == "new 2"
    assert not (corpus / "Old-3-2026-01-01-[3].md").exists()
    assert set(get_sync.load_sync_state(corpus)) == {"1", "2"}


def test_emitted_resume_command_preserves_named_book_config_and_original_cutoff(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "operator's files"
    root.mkdir()
    corpus = root / "corpus files"
    corpus.mkdir()
    (corpus / "doc-2026-01-01-[1].md").write_text("doc")
    books = root / "books.json"
    books.write_text(
        json.dumps(
            [
                {
                    "key": "selected",
                    "name": "Selected book",
                    "url": "https://example.invalid/book",
                    "corpus": str(corpus),
                }
            ]
        )
    )
    settings = root / "sync config.toml"
    settings.write_text(
        '[source]\nbooks_file = "books.json"\n[skylark]\nmcp_url = "https://example.invalid/mcp"\n'
    )
    staging = root / "retained staging"
    calls = []
    original = fake_runner(staging, calls)
    run_pipeline = pipeline.run_pipeline
    fail = True
    observed = []

    def invoke(options):
        observed.append(options)

        def run(command):
            if fail and Path(command[1]).name == "apply_sync_updates.py":
                raise RuntimeError("export unavailable")
            original(command)

        return run_pipeline(options, runner=run)

    monkeypatch.setattr(pipeline, "run_pipeline", invoke)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync",
            "--config",
            str(settings),
            "--book",
            "selected",
            "--staging",
            str(staging),
            "--skip-graph",
            "--skip-web",
        ],
    )
    with pytest.raises(SystemExit) as error:
        pipeline.main()
    assert error.value.code == 1
    output = capsys.readouterr().err
    command = shlex.split(
        next(
            line.removeprefix("Retry: ")
            for line in output.splitlines()
            if line.startswith("Retry: ")
        )
    )
    assert command[command.index("--config") + 1] == str(settings)
    assert command[command.index("--book") + 1] == "selected"
    assert "--cutoff" not in command
    assert "--skip-graph" in command and "--skip-web" in command
    fail = False
    calls.clear()
    monkeypatch.setattr(sys, "argv", command[1:])

    pipeline.main()

    assert observed[1].book_name == "Selected book"
    assert observed[1].cutoff is None
    assert calls == ["apply_sync_updates.py"]
    assert (
        json.loads((staging / "pipeline-state.json").read_text())["status"]
        == "completed"
    )


@pytest.mark.parametrize("failure", ["ocr", "publish", "cancel"])
def test_pipeline_blocks_downstream_after_real_apply_failure(
    apply_run, tmp_path, monkeypatch, capsys, failure
):
    _invoke, corpus, staging, _failures, _exports, _ocr, plan = apply_run
    config = options(tmp_path)
    config.input_dir = corpus
    config.staging = staging
    before = recovery.snapshot(corpus)
    calls = []
    original = fake_runner(staging, calls)
    if failure == "ocr":
        monkeypatch.setattr(apply_sync, "run_mdocr", lambda files, out_dir: 1)
    elif failure == "publish":
        copy = apply_sync.shutil.copy2

        def fail_copy(source, destination):
            if destination.parent == corpus:
                raise OSError("disk unavailable")
            return copy(source, destination)

        monkeypatch.setattr(apply_sync.shutil, "copy2", fail_copy)
    else:
        export = apply_sync.export_doc

        def cancel(doc_id, doc_format=None):
            if doc_id == 2:
                raise KeyboardInterrupt()
            return export(doc_id, doc_format)

        monkeypatch.setattr(apply_sync, "export_doc", cancel)

    def run(command):
        script = Path(command[1]).name
        if script == "get_sync_updates.py":
            original(command)
            output = Path(command[command.index("-o") + 1])
            next(output.glob("sync-updates-*.json")).write_text(plan.read_text())
        elif script == "apply_sync_updates.py":
            calls.append(script)
            monkeypatch.setattr(sys, "argv", command[1:])
            apply_sync.main()
        else:
            pytest.fail(f"downstream stage ran after {failure}: {script}")

    with pytest.raises(KeyboardInterrupt if failure == "cancel" else SystemExit):
        pipeline.run_pipeline(config, runner=run)

    assert recovery.snapshot(corpus) == before
    state = json.loads((staging / "pipeline-state.json").read_text())
    assert state["status"] == "failed"
    assert state["stages"].get("corpus") != "completed"
    assert calls == ["get_sync_updates.py", "apply_sync_updates.py"]
    if failure == "cancel":
        progress = json.loads((staging / "export-progress.json").read_text())
        assert set(progress["documents"]) == {"1"}
        assert "completed_exports=1" in capsys.readouterr().err
    else:
        manifest = json.loads((staging / "manifest.json").read_text())
        assert manifest["status"] == "failed"
        assert manifest["failures"][0]["stage"] == failure


def test_resume_revalidates_legacy_completed_corpus_manifest(tmp_path):
    config = options(tmp_path)
    config.input_dir.mkdir()
    (config.input_dir / "doc-2026-01-01-[1].md").write_text("doc")
    calls = []
    with pytest.raises(RuntimeError, match="graph failure"):
        pipeline.run_pipeline(
            config, runner=fake_runner(config.staging, calls, fail_graph=True)
        )
    (config.staging / "manifest.json").write_text(
        json.dumps({"skipped": [{"id": 2, "reason": "export failed"}]})
    )
    config.resume = True
    calls.clear()

    with pytest.raises(RuntimeError, match="corpus incomplete"):
        pipeline.run_pipeline(config, runner=fake_runner(config.staging, calls))

    assert calls == []
    state = json.loads((config.staging / "pipeline-state.json").read_text())
    assert state["status"] == "failed"
    assert state["stages"]["corpus"] == "retry_required"


def test_sync_cli_listing_exhaustion_reports_page_budget_and_retained_resume(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    original = corpus / "doc-2026-01-01-[1].md"
    original.write_text("preserved")
    staging = tmp_path / "staging"
    proxy = tmp_path / "proxy"
    proxy.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'id':1,'result':{}}),flush=True)\n"
        "sys.stdin.readline()\n"
        "request=json.loads(sys.stdin.readline())\n"
        "tool=request['params']['name']\n"
        "if tool=='skylark_resolve_url': result={'ok':True,'data':{'type':'book','id':123,'slug':'test'}}\n"
        "elif tool=='skylark_book_detail': result={'ok':True,'data':{'name':'Test','items_count':1}}\n"
        "elif tool=='skylark_doc_list': result={'ok':False,'error_msg':'Request timeout for 15000 ms'}\n"
        "else: raise AssertionError(tool)\n"
        "print(json.dumps({'id':2,'result':{'content':[{'text':json.dumps(result)}]}}),flush=True)\n"
    )
    proxy.chmod(0o755)
    settings = tmp_path / "config.toml"
    settings.write_text(
        '[skylark]\nmcp_url="https://example.invalid/mcp"\n'
        f"proxy_bin={json.dumps(str(proxy))}\n"
        "max_retries=1\nconcurrency=1\ntimeout_seconds=2\n"
    )
    # Control time at the child-process boundary; retries must not really sleep.
    shim = tmp_path / "python-shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import time\ntime.sleep=lambda seconds: None\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DOGPDKB_", "SKYLARK_"))
        and key not in {"UTOO_PROXY_BIN", "GRAPHIFY_PYTHON"}
    }
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(shim), str(Path(__file__).resolve().parents[1] / "src")]
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "sync",
            "--config",
            str(settings),
            "--skylark-url",
            "https://example.invalid/book",
            "--input",
            str(corpus),
            "--staging",
            str(staging),
            "--skip-graph",
            "--skip-web",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "stage=list skylark_doc_list book_id=123 offset=0" in output
    assert "retry 1/1" in output and "attempt=2/2" in output
    assert "remote envelope" in output and "15000 ms" in output
    assert "resume will refetch the entire listing" in output
    assert f"--staging {staging}" in output and "Retry:" in output
    assert original.read_text() == "preserved"
    assert not (staging / "manifest.json").exists()
    assert not list((staging / "plan").glob("sync-updates-*.json"))
    state = json.loads((staging / "pipeline-state.json").read_text())
    assert state["status"] == "failed"
    assert state["stages"] == {"plan": "retry_required"}
