"""Apply/sync contracts through the installed CLI seam and real Markdown."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = {
    "value": "2026-11-12T10:00:00Z",
    "utc": "2026-11-12T10:00:00+00:00",
    "precision": "second",
}


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", *map(str, args)],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def instance(tmp_path):
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
    (tmp_path / "remote.json").write_text(
        json.dumps(
            {
                "42": {
                    "body": "# Title\n\nFull current knowledge.\n",
                    "version": "2026-11-12T10:00:00Z",
                }
            }
        )
    )
    tool = tmp_path / "skylark.py"
    tool.write_text(
        "import json, sys\nfrom pathlib import Path\nroot=Path(__file__).parent\nargs=sys.argv[1:]\nwith (root/'events').open('a') as f: f.write(args[0]+'\\n')\ndef option(name): return Path(args[args.index(name)+1])\nremote=json.loads((root/'remote.json').read_text())\nrows=[]\nfor item in json.loads(option('--request').read_text())['documents']:\n    doc=remote[item['document_id']]\n    path=Path(item['path']); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(doc['body'])\n    rows.append({'document_id':item['document_id'],'path':str(path),'status':'completed','version':doc['version'],'metadata':{'title':'Title'}})\noption('--output').write_text(json.dumps({'schema':'skylark-export-result-1','status':'completed','documents':rows,'counts':{'completed':len(rows)}}))\n"
    )
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\nocr_cache="cache"\n[tools]\n'
        + "skylark="
        + json.dumps([sys.executable, str(tool)])
        + "\n"
        + "mdcompare="
        + json.dumps([sys.executable, "-m", "mdcompare"])
        + "\n"
        + "mdocr="
        + json.dumps([sys.executable, "-m", "mdocr.cli"])
        + "\n"
    )
    differences = tmp_path / "differences.json"
    differences.write_text(
        json.dumps(
            {
                "schema": "dogpdkb-differences-1",
                "book": {"key": "funds"},
                "documents": [
                    {
                        "document_id": "42",
                        "version": VERSION,
                        "title": "Title",
                        "path": "folder",
                        "source_url": "https://example.invalid/doc/42",
                        "format": "md",
                        "metadata": {
                            "name": "Title",
                            "logical_path": "folder",
                            "source_ref": "https://example.invalid/doc/42",
                        },
                    }
                ],
            }
        )
    )
    return config, differences


def test_apply_stops_at_plan_with_real_comparison_and_no_corpus_write(tmp_path):
    config, differences = instance(tmp_path)
    result = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["paused"] is True
    run = json.loads(Path(output["run"]).read_text())
    assert run["status"] == "paused"
    assert Path(run["artifacts"]["plan"]).is_file()
    assert not list((tmp_path / "corpus").glob("*.md"))
    assert (tmp_path / "events").read_text().splitlines() == ["export"]
    assert "ocr_report" not in run["artifacts"]


def test_apply_resume_publishes_full_source_without_claiming_graph_success(tmp_path):
    config, differences = instance(tmp_path)
    paused = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    run_path = json.loads(paused.stdout)["run"]
    result = cli("apply", "--config", config, "--resume", run_path)
    assert result.returncode == 0, result.stderr
    manifest = json.loads(Path(json.loads(result.stdout)["manifest"]).read_text())
    assert manifest["schema"] == "dogpdkb-compile-1"
    assert manifest["documents"][0]["action"] == "compile"
    published = Path(manifest["documents"][0]["path"])
    assert published.read_text() == "# Title\n\nFull current knowledge.\n"
    assert published.parent == tmp_path / "corpus"
    state = json.loads((tmp_path / "state" / "funds" / "state.json").read_text())
    assert state["documents"]["42"]["corpus"]["version"] == VERSION
    assert "version" not in state["documents"]["42"].get("graph", {})
    assert (tmp_path / "events").read_text().splitlines() == ["export"]


def test_resume_rejects_changed_or_missing_plan_input(tmp_path):
    config, differences = instance(tmp_path)
    paused = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    run_path = json.loads(paused.stdout)["run"]
    run = json.loads(Path(run_path).read_text())
    plan = json.loads(Path(run["artifacts"]["plan"]).read_text())
    Path(plan["documents"][0]["raw_path"]).write_text("different current input")
    result = cli("apply", "--config", config, "--resume", run_path)
    assert result.returncode == 1
    assert "generate a new plan" in result.stderr
    assert not list((tmp_path / "corpus").glob("*.md"))
    assert (tmp_path / "events").read_text().splitlines() == ["export"]


def test_ocr_failure_keeps_body_and_is_not_retried_for_same_version(tmp_path):
    config, differences = instance(tmp_path)
    remote = tmp_path / "remote.json"
    data = json.loads(remote.read_text())
    data["42"]["body"] += "\n![unsupported](https://example.invalid/diagram.svg)\n"
    remote.write_text(json.dumps(data))
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 2, first.stderr
    first_result = json.loads(first.stdout)
    first_run = json.loads(Path(first_result["run"]).read_text())
    manifest = json.loads(Path(first_result["manifest"]).read_text())
    assert (
        "Full current knowledge." in Path(manifest["documents"][0]["path"]).read_text()
    )
    assert manifest["documents"][0]["action"] == "compile"
    assert any(f["step"] == "ocr" for f in manifest["failures"])
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 2, second.stderr
    second_run = json.loads(Path(json.loads(second.stdout)["run"]).read_text())
    assert "ocr_report" not in second_run["artifacts"]
    assert first_run["artifacts"]["ocr_report"]
    assert (tmp_path / "events").read_text().splitlines() == ["export"]
    changed = json.loads(differences.read_text())
    changed["documents"][0]["version"] = {
        "value": "2026-11-13T10:00:00Z",
        "utc": "2026-11-13T10:00:00+00:00",
        "precision": "second",
    }
    differences.write_text(json.dumps(changed))
    data["42"]["version"] = "2026-11-13T10:00:00Z"
    remote.write_text(json.dumps(data))
    third = cli("apply", "--config", config, "--input", differences)
    assert third.returncode == 2, third.stderr
    third_run = json.loads(Path(json.loads(third.stdout)["run"]).read_text())
    assert "ocr_report" in third_run["artifacts"]
    assert (tmp_path / "events").read_text().splitlines() == ["export", "export"]


def test_same_raw_is_reused_and_old_plan_cannot_replace_newer_corpus(tmp_path):
    config, differences = instance(tmp_path)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 0, second.stderr
    manifest = json.loads(Path(json.loads(second.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "reuse"
    assert (tmp_path / "events").read_text().splitlines() == ["export"]
    paused = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    old_run = json.loads(paused.stdout)["run"]
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"].update(
        body="Newer complete knowledge.\n", version="2026-11-13T10:00:00Z"
    )
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    changed = json.loads(differences.read_text())
    changed["documents"][0]["version"] = {
        "value": "2026-11-13T10:00:00Z",
        "utc": "2026-11-13T10:00:00+00:00",
        "precision": "second",
    }
    differences.write_text(json.dumps(changed))
    newer = cli("apply", "--config", config, "--input", differences)
    assert newer.returncode == 0, newer.stderr
    rejected = cli("apply", "--config", config, "--resume", old_run)
    assert rejected.returncode == 1
    assert "generate a new plan" in rejected.stderr
    manifest = json.loads(Path(json.loads(newer.stdout)["manifest"]).read_text())
    assert (
        Path(manifest["documents"][0]["path"]).read_text()
        == "Newer complete knowledge.\n"
    )


def test_changed_images_require_compile_even_with_reusable_body(tmp_path):
    config, differences = instance(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["body"] += "\n![image](https://example.invalid/old.svg)\n"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 2, first.stderr
    remote["42"]["body"] = remote["42"]["body"].replace("old.svg", "new.svg")
    remote["42"]["version"] = "2026-11-13T10:00:00Z"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    changed = json.loads(differences.read_text())
    changed["documents"][0]["version"] = {
        "value": "2026-11-13T10:00:00Z",
        "utc": "2026-11-13T10:00:00+00:00",
        "precision": "second",
    }
    differences.write_text(json.dumps(changed))
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 2, second.stderr
    manifest = json.loads(Path(json.loads(second.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "compile"


def test_sync_uses_discover_and_apply_in_one_run_and_stops_before_ocr(tmp_path):
    config, _ = instance(tmp_path)
    tool = tmp_path / "skylark.py"
    script = tool.read_text()
    script = script.replace(
        "rows=[]",
        """if args[0] == 'listing':
    option('--output').write_text(json.dumps({'schema':'skylark-listing-1','complete':True,'book':{'url':'https://example.invalid/team/book','items_count':len(remote)},'documents':[{'document_id':key,'title':'Title','version':doc['version']} for key,doc in remote.items()],'toc':[]}))
    raise SystemExit(0)
rows=[]""",
    )
    tool.write_text(script)
    result = cli("sync", "--config", config, "--stop-after", "plan")
    assert result.returncode == 0, result.stderr
    run = json.loads(Path(json.loads(result.stdout)["run"]).read_text())
    assert run["command"] == "sync"
    assert run["status"] == "paused"
    assert set(run["artifacts"]) >= {"listing", "differences", "plan"}
    assert "ocr_report" not in run["artifacts"]
    assert not list((tmp_path / "corpus").glob("*.md"))
    assert (tmp_path / "events").read_text().splitlines() == ["listing", "export"]


def test_apply_finishes_each_whole_batch_stage_before_next_tool(tmp_path):
    config, differences = instance(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["43"] = {
        "body": "# Second\n\nAnother full source.\n",
        "version": "2026-11-12T10:00:00Z",
    }
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    selection = json.loads(differences.read_text())
    selection["documents"].append(
        {**selection["documents"][0], "document_id": "43", "title": "Second"}
    )
    differences.write_text(json.dumps(selection))
    bridge = tmp_path / "tool_cli.py"
    bridge.write_text("""import json, subprocess, sys
from pathlib import Path
root = Path(__file__).parent
name, args = sys.argv[1], sys.argv[2:]
with (root/'events').open('a') as stream:
    stream.write(name+' '+args[0]+'\\n')
if name == 'mdcompare' and args[0] == 'catalog' and 'incoming' in args[args.index('--input')+1]:
    documents = json.loads(Path(args[args.index('--input')+1]).read_text())['documents']
    assert len(documents) == 2 and all(Path(d['path']).is_file() for d in documents)
if name == 'mdocr':
    request = Path(args[args.index('--manifest')+1])
    assert (request.parent/'plan.json').is_file()
    assert len(json.loads(request.read_text())['documents']) == 1
module = 'mdcompare' if name == 'mdcompare' else 'mdocr.cli'
raise SystemExit(subprocess.call([sys.executable,'-m',module,*args]))
""")
    text = config.read_text()
    for name, module in [("mdcompare", "mdcompare"), ("mdocr", "mdocr.cli")]:
        text = text.replace(
            name + "=" + json.dumps([sys.executable, "-m", module]),
            name + "=" + json.dumps([sys.executable, str(bridge), name]),
        )
    config.write_text(text)
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "events").read_text().splitlines() == [
        "export",
        "export",
        "mdcompare catalog",
        "mdcompare catalog",
        "mdcompare knowledge",
        "mdcompare compare",
        "mdocr batch",
        "mdocr batch",
    ]


def two_sources(tmp_path, differences):
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["43"] = {
        "body": "# Second\n\nUnstarted source.\n",
        "version": "2026-11-12T10:00:00Z",
    }
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    selected = json.loads(differences.read_text())
    selected["documents"].append(
        {**selected["documents"][0], "document_id": "43", "title": "Second"}
    )
    differences.write_text(json.dumps(selected))


def test_interrupted_export_does_not_suppress_later_unstarted_source(tmp_path):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    tool = tmp_path / "skylark.py"
    original = tool.read_text()
    tool.write_text(
        "from pathlib import Path\nimport time\nPath(__file__).with_name('started').touch()\ntime.sleep(30)\n"
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "apply",
            "--config",
            str(config),
            "--input",
            str(differences),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        deadline = time.monotonic() + 5
        while not (tmp_path / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "started").exists()
        process.terminate()
        process.communicate(timeout=5)
        assert process.returncode == 143
    state = json.loads((tmp_path / "state" / "funds" / "state.json").read_text())
    assert state["documents"]["42"]["steps"]["export"]["status"] == "unknown"
    assert "export" not in state["documents"]["43"]["steps"]
    tool.write_text(original)
    resumed = cli("apply", "--config", config, "--input", differences)
    assert resumed.returncode == 2, resumed.stderr
    manifest = json.loads(Path(json.loads(resumed.stdout)["manifest"]).read_text())
    assert [row["document_id"] for row in manifest["documents"]] == ["43"]
    assert (tmp_path / "events").read_text().splitlines() == ["export"]


def test_shared_ocr_stop_preserves_unattempted_sources_for_later_run(tmp_path):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    original = config.read_text()
    tool = tmp_path / "ocr_shared.py"
    tool.write_text("""import json,sys
from pathlib import Path
args=sys.argv[1:]
def option(name): return Path(args[args.index(name)+1])
doc=json.loads(option('--manifest').read_text())['documents'][0]
output=option('--output-dir')/'document.md'; output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(Path(doc['path']).read_text())
option('--report').write_text(json.dumps({'schema_version':'mdocr-batch-1.0','status':'partial','causes':['OCR_AUTH_UNAVAILABLE'],'documents':[{'document_id':doc['document_id'],'status':'partial','output':str(output),'resources':[]}]}))
raise SystemExit(2)
""")
    config.write_text(
        original.replace(
            "mdocr=" + json.dumps([sys.executable, "-m", "mdocr.cli"]),
            "mdocr=" + json.dumps([sys.executable, str(tool)]),
        )
    )
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 2, result.stderr
    manifest = json.loads(Path(json.loads(result.stdout)["manifest"]).read_text())
    assert manifest["documents"][1]["pending_steps"] == ["ocr"]
    state = json.loads((tmp_path / "state" / "funds" / "state.json").read_text())
    assert state["documents"]["42"]["steps"]["ocr"]["status"] == "failed"
    assert "ocr" not in state["documents"]["43"]["steps"]
    config.write_text(original)
    next_run = cli("apply", "--config", config, "--input", differences)
    assert next_run.returncode == 2, next_run.stderr
    manifest = json.loads(Path(json.loads(next_run.stdout)["manifest"]).read_text())
    assert manifest["documents"][1]["pending_steps"] == []
    state = json.loads((tmp_path / "state" / "funds" / "state.json").read_text())
    assert state["documents"]["43"]["steps"]["ocr"]["status"] == "completed"


def test_opaque_versions_advance_by_frozen_facts_and_old_plan_is_refused(tmp_path):
    config, differences = instance(tmp_path)
    data = json.loads(differences.read_text())
    data["documents"][0]["version"] = {"value": "v2", "precision": "opaque"}
    differences.write_text(json.dumps(data))
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["version"] = "v2"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    old_plan = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert old_plan.returncode == 0, old_plan.stderr
    old_run = json.loads(old_plan.stdout)["run"]
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    state = json.loads((tmp_path / "state" / "funds" / "state.json").read_text())
    data["documents"][0]["local"] = state["documents"]["42"]
    data["documents"][0]["version"] = {"value": "v10", "precision": "opaque"}
    differences.write_text(json.dumps(data))
    remote["42"].update(version="v10", body="# Advanced opaque version\n")
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    advanced = cli("apply", "--config", config, "--input", differences)
    assert advanced.returncode == 0, advanced.stderr
    stale = cli("apply", "--config", config, "--resume", old_run)
    assert stale.returncode == 1
    assert (
        "newer corpus version" in stale.stderr
        or "changed corpus version" in stale.stderr
    )


def test_resume_before_plan_keeps_original_scope_and_processes_unstarted_source(
    tmp_path,
):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    tool = tmp_path / "skylark.py"
    original = tool.read_text()
    tool.write_text(
        "from pathlib import Path\nimport time\nPath(__file__).with_name('started').touch()\ntime.sleep(30)\n"
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "apply",
            "--config",
            str(config),
            "--input",
            str(differences),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        deadline = time.monotonic() + 5
        while not (tmp_path / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "started").exists()
        process.terminate()
        process.communicate(timeout=5)
        assert process.returncode == 143
    run_path = next((tmp_path / "runs" / "funds").glob("*/run.json"))
    # New remote data and a changed caller file cannot expand the saved scope.
    differences.write_text('{"not":"the frozen original"}')
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["44"] = {"body": "New unrelated source", "version": "2026-11-12T10:00:00Z"}
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    tool.write_text(original)
    result = cli("apply", "--config", config, "--resume", run_path)
    assert result.returncode == 2, result.stderr
    manifest = json.loads(Path(json.loads(result.stdout)["manifest"]).read_text())
    assert [row["document_id"] for row in manifest["documents"]] == ["43"]
    assert any(row["document_id"] == "42" for row in manifest["failures"])
    assert not any("44" in path.name for path in (tmp_path / "corpus").glob("*.md"))


def test_compare_contract_failure_is_partial_but_valid_unknown_can_compile(tmp_path):
    config, differences = instance(tmp_path)
    original = config.read_text()
    broken = tmp_path / "compare_broken.py"
    broken.write_text("raise SystemExit(1)\n")
    config.write_text(
        original.replace(
            "mdcompare=" + json.dumps([sys.executable, "-m", "mdcompare"]),
            "mdcompare=" + json.dumps([sys.executable, str(broken)]),
        )
    )
    failed = cli("apply", "--config", config, "--input", differences)
    assert failed.returncode == 2, failed.stderr
    manifest = json.loads(Path(json.loads(failed.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "compile"
    assert any(
        row["reason"] == "COMPARISON_REPORT_UNAVAILABLE" for row in manifest["failures"]
    )
    # A separate source version with actual public UNKNOWN reports remains usable.
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"].update(
        version="2026-11-13T10:00:00Z",
        body="# Full larger source\n\nKnowledge available despite comparison budget.\n",
    )
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    selection = json.loads(differences.read_text())
    selection["documents"][0]["version"] = {
        "value": "2026-11-13T10:00:00Z",
        "utc": "2026-11-13T10:00:00+00:00",
        "precision": "second",
    }
    differences.write_text(json.dumps(selection))
    bounded = tmp_path / "compare_bounded.py"
    bounded.write_text(
        "import subprocess,sys\na=sys.argv[1:]\nif a[0] != 'catalog': a += ['--max-file-bytes','1']\nraise SystemExit(subprocess.call([sys.executable,'-m','mdcompare',*a]))\n"
    )
    config.write_text(
        original.replace(
            "mdcompare=" + json.dumps([sys.executable, "-m", "mdcompare"]),
            "mdcompare=" + json.dumps([sys.executable, str(bounded)]),
        )
    )
    unknown = cli("apply", "--config", config, "--input", differences)
    assert unknown.returncode == 0, unknown.stderr
    manifest = json.loads(Path(json.loads(unknown.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "compile"
    assert manifest["failures"] == []
