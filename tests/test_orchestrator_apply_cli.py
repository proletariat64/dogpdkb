"""Apply/sync contracts through the installed CLI seam and real Markdown."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

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
                "book": {"key": "funds", "url": "https://example.invalid/team/book"},
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
    assert manifest["documents"][0]["action"] == "compile"
    # Reusing captured raw bytes does not invent a successful graph baseline.
    assert manifest["documents"][0]["reason_codes"] == ["NEW_DOCUMENT"]
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


def test_changed_images_are_advisory_for_equivalent_authored_body(tmp_path):
    config, differences = instance(tmp_path)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["body"] += "\n![image](https://example.invalid/old.svg)\n"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    seed_body_baseline(tmp_path, config, remote["42"]["body"])
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
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
    assert second.returncode == 0, second.stderr
    manifest = json.loads(Path(json.loads(second.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "reuse"


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
        "mdcompare semantic",
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
            "--concurrency",
            "1",
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
    assert manifest["documents"][1]["pending_steps"] == []
    assert any(
        f["step"] == "ocr" and f["document_id"] == "43" for f in manifest["failures"]
    )
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


@pytest.mark.parametrize("changed_document", [0, 1])
def test_input_changed_during_ocr_is_refused_before_publication(
    tmp_path, changed_document
):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    tool = tmp_path / "ocr_wait.py"
    tool.write_text("""import json,sys,time
from pathlib import Path
root=Path(__file__).parent; args=sys.argv[1:]
def option(name): return Path(args[args.index(name)+1])
doc=json.loads(option('--manifest').read_text())['documents'][0]
with (root/'ocr-calls').open('a') as log: log.write(doc['document_id']+'\\n')
(root/'ocr-started').touch()
deadline=time.monotonic()+5
while not (root/'ocr-release').exists():
 if time.monotonic()>deadline: raise SystemExit(1)
 time.sleep(.01)
output=option('--output-dir')/'document.md'; output.parent.mkdir(parents=True,exist_ok=True)
output.write_bytes(Path(doc['path']).read_bytes())
option('--report').write_text(json.dumps({'schema_version':'mdocr-batch-1.0','documents':[{'document_id':doc['document_id'],'status':'completed','output':str(output),'resources':[]}]}))
""")
    config.write_text(
        config.read_text().replace(
            "mdocr=" + json.dumps([sys.executable, "-m", "mdocr.cli"]),
            "mdocr=" + json.dumps([sys.executable, str(tool)]),
        )
    )
    paused = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert paused.returncode == 0, paused.stderr
    run_path = json.loads(paused.stdout)["run"]
    run = json.loads(Path(run_path).read_text())
    plan = json.loads(Path(run["artifacts"]["plan"]).read_text())
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "apply",
            "--config",
            str(config),
            "--resume",
            run_path,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        deadline = time.monotonic() + 5
        try:
            while not (tmp_path / "ocr-started").exists():
                assert time.monotonic() < deadline
                time.sleep(0.02)
            Path(plan["documents"][changed_document]["raw_path"]).write_text(
                "Changed after frozen plan.\n"
            )
        finally:
            (tmp_path / "ocr-release").touch()
        _stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 1, stderr
    assert "generate a new plan" in stderr
    assert not list((tmp_path / "corpus").glob("*.md"))
    assert (tmp_path / "ocr-calls").read_text().splitlines() == ["42"]


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


def test_frozen_success_facts_do_not_authorize_timestamp_rollback(tmp_path):
    config, differences = instance(tmp_path)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    selected = json.loads(differences.read_text())
    selected["documents"][0].update(
        local=state["documents"]["42"],
        version={"value": "2026-11-11T10:00:00Z"},
    )
    differences.write_text(json.dumps(selected))
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "newer corpus version" in result.stderr
    assert (tmp_path / "events").read_text().splitlines() == ["export"]


def test_registered_day_baseline_allows_same_local_day_timestamp(tmp_path):
    config, differences = instance(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "Title-2026-11-12-[42].md").write_text("Previous body\n")
    registered = cli("init-state", "--config", config)
    assert registered.returncode == 0, registered.stderr
    selected = json.loads(differences.read_text())
    selected["documents"][0]["version"] = {
        "value": "2026-11-12T01:00:00+08:00",
        "utc": "2026-11-11T17:00:00+00:00",
        "precision": "second",
    }
    differences.write_text(json.dumps(selected))
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["version"] = "2026-11-12T01:00:00+08:00"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "events").read_text().splitlines() == ["export"]


@pytest.mark.parametrize(
    ("schema", "code"), [("unsupported", 0), ("mdocr-batch-1.0", 1)]
)
def test_unreliable_ocr_report_is_never_reused_as_success(tmp_path, schema, code):
    config, differences = instance(tmp_path)
    tool = tmp_path / "ocr_unreliable.py"
    tool.write_text(
        "import json,sys\nfrom pathlib import Path\na=sys.argv[1:]\n"
        "def option(k): return Path(a[a.index(k)+1])\n"
        "doc=json.loads(option('--manifest').read_text())['documents'][0]\n"
        "out=option('--output-dir')/'output.md';out.parent.mkdir(parents=True,exist_ok=True)\n"
        "out.write_text('UNRELIABLE OCR OUTPUT')\n"
        f"option('--report').write_text(json.dumps({{'schema_version':{schema!r},'documents':[{{'document_id':doc['document_id'],'status':'completed','output':str(out)}}]}}))\n"
        f"raise SystemExit({code})\n"
    )
    config.write_text(
        config.read_text().replace(
            "mdocr=" + json.dumps([sys.executable, "-m", "mdocr.cli"]),
            "mdocr=" + json.dumps([sys.executable, str(tool)]),
        )
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 2, first.stderr
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 2, second.stdout + second.stderr
    manifest = json.loads(Path(json.loads(second.stdout)["manifest"]).read_text())
    assert "UNRELIABLE" not in Path(manifest["documents"][0]["path"]).read_text()


def test_missing_historical_ocr_input_does_not_abort_fresh_apply(tmp_path):
    config, differences = instance(tmp_path)
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    Path(state["documents"]["42"]["raw"]["path"]).unlink()
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 0, second.stdout + second.stderr


@pytest.mark.parametrize("suffix", ["#width=600", ""])
def test_image_selection_is_owned_by_mdocr(tmp_path, monkeypatch, suffix):
    from PIL import Image

    monkeypatch.delenv("PADDLEOCR_TOKEN", raising=False)
    config, differences = instance(tmp_path)
    image = tmp_path / "image space.png"
    Image.new("RGB", (8, 8), "red").save(image)
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["body"] += f"\n![image](../../../../image%20space.png{suffix})\n"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 2, result.stderr
    run = json.loads(Path(json.loads(result.stdout)["run"]).read_text())
    report = json.loads(Path(run["artifacts"]["ocr_report"]).read_text())
    assert report["documents"][0]["resources"][0]["reason"] == "OCR_AUTH_UNAVAILABLE"


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
            "--concurrency",
            "1",
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


@pytest.mark.parametrize("mutation", ["changed", "missing"])
def test_resume_before_plan_refuses_changed_prepared_input(tmp_path, mutation):
    config, differences = instance(tmp_path)
    original = config.read_text()
    tool = tmp_path / "compare_wait.py"
    tool.write_text(
        "from pathlib import Path\nimport time\n"
        "Path(__file__).with_name('compare-started').touch()\ntime.sleep(30)\n"
    )
    config.write_text(
        original.replace(
            "mdcompare=" + json.dumps([sys.executable, "-m", "mdcompare"]),
            "mdcompare=" + json.dumps([sys.executable, str(tool)]),
        )
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
        while not (tmp_path / "compare-started").exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        process.terminate()
        process.communicate(timeout=5)
        assert process.returncode == 143
    run_path = next((tmp_path / "runs/funds").glob("*/run.json"))
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    raw = Path(state["documents"]["42"]["raw"]["path"])
    if mutation == "missing":
        raw.unlink()
    else:
        raw.write_text("Edited prepared source.\n")
    config.write_text(original)
    result = cli("apply", "--config", config, "--resume", run_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "generate a new plan" in result.stderr
    assert (tmp_path / "events").read_text().splitlines() == ["export"]
    assert not list((tmp_path / "corpus").glob("*.md"))


def test_compare_contract_failure_and_valid_unknown_defer_without_compile(tmp_path):
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
    assert failed.returncode == 1
    assert "authoritative semantic comparison unavailable" in failed.stderr
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
    assert unknown.returncode == 2, unknown.stderr
    manifest = json.loads(Path(json.loads(unknown.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "defer"
    assert any(
        row["reason"] == "AUTHORED_BODY_UNAVAILABLE" for row in manifest["failures"]
    )


@pytest.mark.parametrize("change", ["corpus", "graph", "source"])
def test_paused_run_refuses_changed_source_or_write_target(tmp_path, change):
    config, differences = instance(tmp_path)
    paused = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert paused.returncode == 0, paused.stderr
    run_path = Path(json.loads(paused.stdout)["run"])
    before = run_path.read_bytes()
    if change in {"corpus", "source"}:
        books = tmp_path / "books.json"
        rows = json.loads(books.read_text())
        rows[0]["corpus" if change == "corpus" else "url"] = (
            "new-corpus"
            if change == "corpus"
            else "https://example.invalid/another-book"
        )
        books.write_text(json.dumps(rows))
    else:
        config.write_text(
            config.read_text().replace("[paths]\n", '[paths]\ngraph_dir="new-graph"\n')
        )
    rejected = cli("apply", "--config", config, "--resume", run_path)
    assert rejected.returncode == 1
    assert "source book" in rejected.stderr or "targets changed" in rejected.stderr
    assert (tmp_path / "events").read_text().splitlines() == ["export"]
    assert run_path.read_bytes() == before
    assert not list((tmp_path / "new-corpus").glob("*.md"))
    assert not (tmp_path / "new-graph" / "graph.json").exists()


def test_failed_version_history_survives_newer_failure_and_run_cleanup(tmp_path):
    config, differences = instance(tmp_path)
    frozen = tmp_path / "frozen-v1.json"
    frozen.write_bytes(differences.read_bytes())
    (tmp_path / "skylark.py").write_text("""import json, pathlib, sys
root=pathlib.Path(__file__).parent
args=sys.argv[1:]
with (root/'events').open('a') as stream: stream.write('failed-export\\n')
request=json.loads(pathlib.Path(args[args.index('--request')+1]).read_text())
report={'schema':'skylark-export-result-1','status':'partial','documents':[{**row,'status':'failed','error':'fixture failure'} for row in request['documents']],'counts':{'failed':len(request['documents'])}}
pathlib.Path(args[args.index('--output')+1]).write_text(json.dumps(report))
raise SystemExit(2)
""")
    first = cli("apply", "--config", config, "--input", frozen)
    assert first.returncode == 2, first.stderr
    changed = json.loads(differences.read_text())
    changed["documents"][0]["version"] = {
        "value": "2026-11-13T10:00:00Z",
        "utc": "2026-11-13T10:00:00+00:00",
        "precision": "second",
    }
    differences.write_text(json.dumps(changed))
    newer = cli("apply", "--config", config, "--input", differences)
    assert newer.returncode == 2, newer.stderr
    import shutil

    shutil.rmtree(tmp_path / "runs")
    repeated = cli("apply", "--config", config, "--input", frozen)
    assert repeated.returncode == 2, repeated.stderr
    assert (tmp_path / "events").read_text().splitlines() == [
        "failed-export",
        "failed-export",
    ]


def test_resumed_run_status_clears_pause_end_time_and_elapsed_keeps_advancing(tmp_path):
    config, differences = instance(tmp_path)
    paused = cli(
        "apply", "--config", config, "--input", differences, "--stop-after", "plan"
    )
    assert paused.returncode == 0, paused.stderr
    run_path = json.loads(paused.stdout)["run"]
    tool = tmp_path / "slow_ocr.py"
    tool.write_text(
        'import pathlib,time\npathlib.Path(__file__).with_suffix(".started").touch()\ntime.sleep(60)\n'
    )
    config.write_text(
        config.read_text().replace(
            "mdocr=" + json.dumps([sys.executable, "-m", "mdocr.cli"]),
            "mdocr=" + json.dumps([sys.executable, str(tool)]),
        )
    )
    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "apply",
            "--config",
            str(config),
            "--resume",
            run_path,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            deadline = time.monotonic() + 5
            while (
                not tool.with_suffix(".started").exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert tool.with_suffix(".started").exists()
            first = json.loads(
                cli("status", "--config", config, "--run", run_path, "--json").stdout
            )
            time.sleep(1.1)
            second = json.loads(
                cli("status", "--config", config, "--run", run_path, "--json").stdout
            )
        finally:
            process.terminate()
            process.communicate(timeout=5)
    assert first["alive"] is True
    assert first["ended_at"] is None
    assert first["exit_code"] is None
    assert second["elapsed_seconds"] >= first["elapsed_seconds"] + 1


def test_imported_difference_rejects_another_source_book_before_export(tmp_path):
    config, differences = instance(tmp_path)
    payload = json.loads(differences.read_text())
    payload["book"]["url"] = "https://example.invalid/other/book"
    differences.write_text(json.dumps(payload))
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 1
    assert "book mismatch" in result.stderr
    assert not (tmp_path / "events").exists()
    assert not list((tmp_path / "corpus").glob("*.md"))


@pytest.mark.parametrize("include_subscribed", [False, True])
def test_apply_rejects_unsubscribed_difference_before_export_or_saved_inputs(
    tmp_path, include_subscribed
):
    config, differences = instance(tmp_path)
    books_path = tmp_path / "books.json"
    books = json.loads(books_path.read_text())
    books[0]["document_ids"] = ["43"]
    books_path.write_text(json.dumps(books))
    data = json.loads(differences.read_text())
    if include_subscribed:
        subscribed = {**data["documents"][0], "document_id": "43"}
        data["documents"].insert(0, subscribed)
        remote_path = tmp_path / "remote.json"
        remote = json.loads(remote_path.read_text())
        remote["43"] = {**remote["42"], "body": "# Subscribed source\n"}
        remote_path.write_text(json.dumps(remote))
    differences.write_text(json.dumps(data))
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "outside subscription" in result.stderr
    assert not (tmp_path / "events").exists()
    assert not (tmp_path / "state/funds/state.json").exists()
    assert not list((tmp_path / "corpus").glob("*.md"))
    assert not list((tmp_path / "runs").rglob("differences.json"))
    assert not list((tmp_path / "runs").rglob("prepared-inputs.json"))
    assert not list((tmp_path / "runs").rglob("export-*-request.json"))


def seed_body_baseline(tmp_path, config, text):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "Title-2026-11-11-[42].md").write_text(text)
    result = cli("init-state", "--config", config)
    assert result.returncode == 0, result.stderr


def test_complete_hold_retains_graph_without_manual_gate(tmp_path):
    config, differences = instance(tmp_path)
    seed_body_baseline(
        tmp_path,
        config,
        "# Title\n\nFull current knowledge.\n\nHistorical old-only paragraph.\n",
    )
    result = cli("apply", "--config", config, "--input", differences)
    assert result.returncode == 0, result.stderr
    manifest = json.loads(Path(json.loads(result.stdout)["manifest"]).read_text())
    row = manifest["documents"][0]
    assert row["action"] == "compile"
    assert "AUTHORED_BODY_CHANGED" in row["reason_codes"]
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert "version" not in state["documents"]["42"]["graph"]
    assert state["documents"]["42"]["steps"]["ocr"]["status"] == "completed"


def test_unavailable_body_defers_without_ocr_and_keeps_old_comparison_basis(tmp_path):
    config, differences = instance(tmp_path)
    seed_body_baseline(
        tmp_path,
        config,
        "# Title\n\nOld knowledge.\n\n<!-- mdocr:ocr-begin -->\nunknown tail\n",
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 2, first.stderr
    manifest = json.loads(Path(json.loads(first.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "defer"
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    doc = state["documents"]["42"]
    assert doc["steps"]["compare"]["status"] == "unknown"
    assert "ocr" not in doc["steps"]
    assert "unknown tail" in Path(doc["graph_baseline"]["path"]).read_text()
    # A newer raw version must not become its own successful graph baseline.
    remote = json.loads((tmp_path / "remote.json").read_text())
    remote["42"]["version"] = "2026-11-13T10:00:00Z"
    (tmp_path / "remote.json").write_text(json.dumps(remote))
    changed = json.loads(differences.read_text())
    changed["documents"][0]["version"] = {"value": "2026-11-13T10:00:00Z"}
    differences.write_text(json.dumps(changed))
    second = cli("apply", "--config", config, "--input", differences)
    assert second.returncode == 2, second.stderr
    result = json.loads(Path(json.loads(second.stdout)["manifest"]).read_text())
    assert result["documents"][0]["action"] == "defer"
    state = json.loads(state_path.read_text())
    assert "version" not in state["documents"]["42"]["graph"]


def test_explicit_graph_replan_preserves_original_reports_and_published_bytes(tmp_path):
    config, differences = instance(tmp_path)
    seed_body_baseline(tmp_path, config, "# Title\n\nPrevious different knowledge.\n")
    graph = tmp_path / "graphify.py"
    graph.write_text(
        "from pathlib import Path\nPath(__file__).with_suffix('.called').touch()\nraise SystemExit(2)\n"
    )
    config.write_text(
        config.read_text()
        + "graphify="
        + json.dumps([sys.executable, str(graph)])
        + "\n"
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    output = json.loads(first.stdout)
    run = json.loads(Path(output["run"]).read_text())
    original_manifest = Path(output["manifest"])
    original_plan = Path(run["artifacts"]["plan"])
    original_report = Path(run["artifacts"]["knowledge"])
    frozen = {
        p: p.read_bytes() for p in [original_manifest, original_plan, original_report]
    }
    document = json.loads(original_manifest.read_text())["documents"][0]
    published = Path(document["path"])
    before = published.read_bytes()
    recovered = cli(
        "sync",
        "--config",
        config,
        "--resume",
        output["run"],
        "--retry-graph",
        "--replan",
    )
    assert recovered.returncode == 2, recovered.stderr
    assert graph.with_suffix(".called").exists()
    assert published.read_bytes() == before
    assert all(p.read_bytes() == content for p, content in frozen.items())
    latest = json.loads(Path(output["run"]).read_text())
    assert latest["replans"][0]["previous_artifacts"]["manifest"] == str(
        original_manifest
    )
    refreshed = json.loads(Path(latest["artifacts"]["manifest"]).read_text())
    assert refreshed["documents"][0]["action"] == "compile"
    assert refreshed["documents"][0]["input_copy"] == document["input_copy"]
    assert (tmp_path / "events").read_text().splitlines() == ["export"]


def test_graph_replan_rejects_missing_original_baseline_before_tools(tmp_path):
    config, differences = instance(tmp_path)
    seed_body_baseline(tmp_path, config, "# Title\n\nPrevious different knowledge.\n")
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    output = json.loads(first.stdout)
    run = json.loads(Path(output["run"]).read_text())
    plan = json.loads(Path(run["artifacts"]["plan"]).read_text())
    Path(plan["documents"][0]["baseline_input_copy"]).unlink()
    recovered = cli(
        "sync",
        "--config",
        config,
        "--resume",
        output["run"],
        "--retry-graph",
        "--replan",
    )
    assert recovered.returncode == 1
    assert "original baseline snapshot unavailable" in recovered.stderr
    assert not list(Path(output["run"]).parent.glob("replan-*"))


@pytest.mark.parametrize("args", [["--replan"], ["--resume", "missing", "--replan"]])
def test_graph_replan_requires_explicit_owned_recovery_flags(args):
    result = cli("sync", *args)
    assert result.returncode == 64
    assert "--replan requires" in result.stderr


def test_changed_body_stays_pending_until_actual_graph_compilation(tmp_path):
    config, _ = instance(tmp_path)
    seed_body_baseline(
        tmp_path,
        config,
        "# Title\n\nFull current knowledge.\n\nHistorical old-only paragraph.\n",
    )
    listing = tmp_path / "listing.json"
    listing.write_text(
        json.dumps(
            {
                "schema": "skylark-listing-1",
                "complete": True,
                "book": {
                    "url": "https://example.invalid/team/book",
                    "book_id": 8,
                    "items_count": 1,
                },
                "documents": [
                    {
                        "document_id": "42",
                        "title": "Title",
                        "path": "folder",
                        "source_url": "https://example.invalid/doc/42",
                        "format": "md",
                        "version": "2026-11-12T10:00:00Z",
                    }
                ],
                "toc": [],
                "captured_at": "2026-11-12T12:00:00Z",
            }
        )
    )
    first = cli("discover", "--config", config, "--listing", listing)
    assert first.returncode == 0, first.stderr
    selection = Path(json.loads(first.stdout)["differences"])
    assert len(json.loads(selection.read_text())["documents"]) == 1
    applied = cli("apply", "--config", config, "--input", selection)
    assert applied.returncode == 0, applied.stderr
    manifest = json.loads(Path(json.loads(applied.stdout)["manifest"]).read_text())
    assert manifest["documents"][0]["action"] == "compile"
    next_day = cli("discover", "--config", config, "--listing", listing)
    assert next_day.returncode == 0, next_day.stderr
    # Corpus publication is not successful graph compilation. A now-compiled
    # authored deletion must keep its pending graph work instead of being
    # acknowledged as the former HOLD/retain decision.
    selected = json.loads(Path(json.loads(next_day.stdout)["differences"]).read_text())[
        "documents"
    ]
    assert [row["document_id"] for row in selected] == ["42"]
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert "pending" in state["documents"]["42"]
    assert "version" not in state["documents"]["42"]["graph"]
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    document = state["documents"]["42"]
    assert "pending" in document
    assert "version" not in document["graph"]
    assert (
        "Historical old-only paragraph."
        in Path(document["graph_baseline"]["path"]).read_text()
    )


def test_retry_after_interrupted_replan_preserves_active_selection_and_inputs(tmp_path):
    config, differences = instance(tmp_path)
    seed_body_baseline(tmp_path, config, "# Title\n\nPrevious different knowledge.\n")
    graph = tmp_path / "graphify.py"
    graph.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "flag=Path(__file__).with_suffix('.called')\n"
        "if not flag.exists():\n    flag.touch()\n    raise SystemExit(130)\n"
        "out=Path(sys.argv[sys.argv.index('--out')+1])/'graphify-out'\n"
        "out.mkdir(parents=True,exist_ok=True)\n"
        "(out/'graph.json').write_text(json.dumps({'nodes':[], 'links':[]}))\n"
        "(out/'manifest.json').write_text('{}')\n"
    )
    config.write_text(
        config.read_text()
        + "graphify="
        + json.dumps([sys.executable, str(graph)])
        + "\n"
    )
    first = cli("apply", "--config", config, "--input", differences)
    assert first.returncode == 0, first.stderr
    run_path = Path(json.loads(first.stdout)["run"])
    interrupted = cli(
        "sync", "--config", config, "--resume", run_path, "--retry-graph", "--replan"
    )
    assert interrupted.returncode == 130, interrupted.stderr
    saved = json.loads(run_path.read_text())
    assert "cleanup" not in saved
    manifest = Path(saved["artifacts"]["manifest"])
    assert manifest.parent.name.startswith("replan-")
    dependencies = {
        Path(saved["artifacts"][key]) for key in ["plan", "knowledge", "compare"]
    }
    plan = json.loads(Path(saved["artifacts"]["plan"]).read_text())
    dependencies.update(Path(row["input_copy"]) for row in plan["documents"])
    dependencies.update(
        Path(row["baseline_input_copy"])
        for row in plan["documents"]
        if row.get("baseline_input_copy")
    )
    succeeded = cli("sync", "--config", config, "--resume", run_path, "--retry-graph")
    assert succeeded.returncode == 0, succeeded.stderr
    latest = json.loads(run_path.read_text())
    assert latest["cleanup"]["status"] == "completed"
    assert latest["artifacts"]["manifest"] == str(manifest)
    assert manifest.is_file()
    assert all(path.is_file() for path in dependencies)
    assert json.loads(manifest.read_text())["documents"][0]["action"] == "compile"
