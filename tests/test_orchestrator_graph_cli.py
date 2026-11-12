"""Graph orchestration contracts through the executable CLI and public artifacts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = {
    "utc": "2026-11-12T10:00:00+00:00",
    "value": "2026-11-12T10:00:00Z",
    "precision": "second",
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    run = tmp_path / "runs/funds/run-graph"
    run.mkdir(parents=True)
    source = corpus / "Title-2026-11-12-[42].md"
    source.write_text(
        "# Complete current source\n\nReal full Markdown, never a diff fragment.\n"
    )
    witness = run / "published/0.md"
    witness.parent.mkdir()
    witness.write_bytes(source.read_bytes())
    document = {
        "document_id": "42",
        "version": VERSION,
        "path": str(source),
        "input_copy": str(witness),
        "metadata": {"name": "Title", "source_ref": "https://example.invalid/42"},
        "action": "compile",
        "reason_codes": ["BODY_CHANGED"],
    }
    manifest = run / "manifest.json"
    write(
        manifest,
        {
            "schema": "dogpdkb-compile-1",
            "book": "funds",
            "run_id": run.name,
            "documents": [document],
            "failures": [],
        },
    )
    write(
        run / "run.json",
        {
            "schema": "dogpdkb-run-1",
            "run_id": run.name,
            "targets": {
                "book_url": "https://example.invalid/book",
                "corpus": str(corpus),
                "graph": str(corpus / "graphify-out"),
            },
            "book": {
                "key": "funds",
                "url": "https://example.invalid/book",
                "name": "Funds",
            },
            "pid": 999999999,
            "status": "completed",
            "stage": "corpus",
            "started_at": "2026-11-12T10:00:00+08:00",
            "updated_at": "2026-11-12T10:00:00+08:00",
            "counts": {},
            "artifacts": {"manifest": str(manifest)},
            "failures": [],
        },
    )
    write(
        tmp_path / "state/funds/state.json",
        {
            "schema": "dogpdkb-state-1",
            "book": {
                "key": "funds",
                "url": "https://example.invalid/book",
                "name": "Funds",
            },
            "documents": {
                "42": {
                    "pending": {"version": VERSION, "metadata": document["metadata"]},
                    "steps": {},
                    "corpus": {
                        "path": str(source),
                        "version": VERSION,
                        "input_copy": str(witness),
                    },
                }
            },
        },
    )
    write(
        tmp_path / "books.json",
        [
            {
                "key": "funds",
                "name": "Funds",
                "url": "https://example.invalid/book",
                "corpus": "corpus",
            }
        ],
    )
    tool = tmp_path / "graphify.py"
    tool.write_text("""import json,sys
from pathlib import Path
root=Path(sys.argv[1]); command=sys.argv[2]; args=sys.argv[3:]
def value(flag): return args[args.index(flag)+1]
with (root/'calls.jsonl').open('a') as log: log.write(json.dumps(sys.argv[2:])+'\\n')
output=Path(value('--output'))
if command=='source-inventory':
 data=json.loads((root/'inventory-fixture.json').read_text()) if (root/'inventory-fixture.json').exists() else {'schema':'graphify-source-inventory-1.0','sources':[]}
else:
 request=json.loads(Path(value('--request')).read_text()); rows=[]
 mode=(root/'mode').read_text() if (root/'mode').exists() else 'success'
 if mode=='unknown': sys.exit(1)
 if mode=='interrupt': sys.exit(130)
 for item in request['sources']:
  rows.append({**{k:item[k] for k in ['source_id','source_file','action']},'status':'failed' if mode=='failed' or (mode=='partial' and item['source_id'].endswith(':43')) else 'completed','error':'fixture failure' if mode=='failed' else ''})
 graph=Path(value('--graph-dir'))/'graph.json'; graph.parent.mkdir(parents=True,exist_ok=True)
 if mode!='failed': graph.write_text('opaque Graphify graph: orchestrator must not parse this')
 failed=sum(r['status']=='failed' for r in rows)
 data={'schema':'graphify-source-result-1.0','status':('partial' if failed<len(rows) else 'failed') if failed else 'completed','graph':str(graph),'sources':rows,'counts':{'total':len(rows),'completed':len(rows)-failed,'failed':failed}}
 if mode=='invalid': data['sources']=[]
output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(data))
sys.exit(2 if command!='source-inventory' and data.get('counts',{}).get('failed') else 0)
""")
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\n[tools]\ngraphify='
        + json.dumps([sys.executable, str(tool), str(tmp_path)])
        + "\n"
    )
    return config, manifest, source


def cli(config, manifest):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "update-graph",
            "--config",
            str(config),
            "--input",
            str(manifest),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_complete_source_calls_only_public_graphify_and_records_success(tmp_path):
    config, manifest, source = fixture(tmp_path)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert [row[0] for row in calls] == ["source-inventory", "update-sources"]
    request = json.loads(Path(calls[1][calls[1].index("--request") + 1]).read_text())
    assert request["sources"][0]["path"] == str(source)
    assert request["sources"][0]["source_id"] == "funds:42"
    state = json.loads((tmp_path / "state/funds/state.json").read_text())["documents"][
        "42"
    ]
    assert state["graph"]["version"] == VERSION
    assert state["steps"]["graph"]["status"] == "completed"
    assert not state.get("pending")


def test_source_failure_keeps_graph_version_and_is_not_retried(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    old = {
        "status": "completed",
        "version": {"utc": "2026-11-10T10:00:00+00:00"},
        "source_file": "old.md",
    }
    data["documents"]["42"]["graph"] = old
    write(state_path, data)
    (tmp_path / "mode").write_text("failed")
    first = cli(config, manifest)
    assert first.returncode == 2, first.stdout + first.stderr
    doc = json.loads(state_path.read_text())["documents"]["42"]
    assert doc["graph"] == old
    assert doc["corpus"]["version"] == VERSION
    assert doc["steps"]["graph"]["status"] == "failed"
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest).returncode == 2
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_invalid_report_is_unknown_and_does_not_advance_graph_truth(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    (tmp_path / "mode").write_text("invalid")
    result = cli(config, manifest)
    assert result.returncode == 1, result.stdout + result.stderr
    doc = json.loads((tmp_path / "state/funds/state.json").read_text())["documents"][
        "42"
    ]
    assert "graph" not in doc
    assert doc["steps"]["graph"]["status"] == "unknown"
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest).returncode == 2
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_changed_source_refuses_old_manifest_before_tool_call(tmp_path):
    config, manifest, source = fixture(tmp_path)
    source.write_text("Changed after planning")
    result = cli(config, manifest)
    assert result.returncode == 1
    assert "generate a new plan" in result.stderr
    assert not (tmp_path / "calls.jsonl").exists()


def test_resuming_a_completed_graph_does_not_repeat_tool_work(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    assert cli(config, manifest).returncode == 0
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest).returncode == 0
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_reuse_without_successful_graph_version_compiles_complete_source(tmp_path):
    config, manifest, source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["documents"][0]["action"] = "reuse"
    write(manifest, data)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    request = json.loads((manifest.parent / "graph/0/request.json").read_text())
    assert request["sources"][0]["action"] == "compile"
    assert request["sources"][0]["path"] == str(source)


def test_success_keeps_a_newer_discovery_pending(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    pending = {
        "version": {"utc": "2026-11-13T10:00:00+00:00"},
        "metadata": {"name": "New title"},
    }
    data["documents"]["42"]["pending"] = pending
    write(state_path, data)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(state_path.read_text())["documents"]["42"]["pending"] == pending


def test_verified_reuse_maintains_source_metadata_without_new_extraction(tmp_path):
    config, manifest, source = fixture(tmp_path)
    baseline = {"utc": "2026-11-11T10:00:00+00:00"}
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["42"]["graph"] = {
        "status": "completed",
        "version": baseline,
        "source_file": "old-name.md",
    }
    write(state_path, state)
    data = json.loads(manifest.read_text())
    data["documents"][0].update(action="reuse", baseline_version=baseline)
    write(manifest, data)
    write(
        tmp_path / "inventory-fixture.json",
        {
            "schema": "graphify-source-inventory-1.0",
            "sources": [{"source_id": "funds:42", "source_file": "old-name.md"}],
        },
    )
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    request = json.loads((manifest.parent / "graph/0/request.json").read_text())[
        "sources"
    ][0]
    assert request["action"] == "metadata"
    assert request["replace"] == ["old-name.md", source.name]
    assert request["metadata"]["source_url"] == "https://example.invalid/42"


def test_partial_publication_advances_only_acknowledged_source(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    second = dict(
        data["documents"][0],
        document_id="43",
        path=str(tmp_path / "corpus/Second-2026-11-12-[43].md"),
        input_copy=str(manifest.parent / "published/1.md"),
    )
    Path(second["path"]).write_text("# Full second source\n")
    Path(second["input_copy"]).write_text("# Full second source\n")
    data["documents"].append(second)
    write(manifest, data)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    old = {
        "status": "completed",
        "version": {"utc": "2026-11-11T10:00:00+00:00"},
        "source_file": "prior.md",
    }
    state["documents"]["43"] = {
        "steps": {},
        "graph": old,
        "corpus": {
            "path": second["path"],
            "version": VERSION,
            "input_copy": second["input_copy"],
        },
    }
    write(state_path, state)
    (tmp_path / "mode").write_text("partial")
    result = cli(config, manifest)
    assert result.returncode == 2, result.stdout + result.stderr
    documents = json.loads(state_path.read_text())["documents"]
    assert documents["42"]["graph"]["version"] == VERSION
    assert documents["43"]["graph"] == old
    assert documents["43"]["steps"]["graph"]["status"] == "failed"


def test_interrupt_marks_only_launched_source_unknown(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    second = dict(
        data["documents"][0],
        document_id="43",
        path=str(tmp_path / "corpus/Second.md"),
        input_copy=str(manifest.parent / "published/1.md"),
    )
    Path(second["path"]).write_text("# Second full source\n")
    Path(second["input_copy"]).write_text("# Second full source\n")
    data["documents"].append(second)
    write(manifest, data)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["43"] = {
        "steps": {},
        "pending": {"version": VERSION, "metadata": second["metadata"]},
        "corpus": {
            "path": second["path"],
            "version": VERSION,
            "input_copy": second["input_copy"],
        },
    }
    write(state_path, state)
    (tmp_path / "mode").write_text("interrupt")
    result = cli(config, manifest)
    assert result.returncode == 130, result.stdout + result.stderr
    documents = json.loads(state_path.read_text())["documents"]
    assert documents["42"]["steps"]["graph"]["status"] == "unknown"
    assert "graph" not in documents["43"]["steps"]
    assert documents["43"]["pending"]["version"] == VERSION
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert [row[0] for row in calls] == ["source-inventory", "update-sources"]


def test_old_manifest_cannot_replace_a_newer_successful_graph(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    graph = {"status": "completed", "version": {"utc": "2026-11-13T10:00:00+00:00"}}
    data["documents"]["42"]["graph"] = graph
    write(state_path, data)
    result = cli(config, manifest)
    assert result.returncode == 1
    assert "newer graph already published" in result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    assert json.loads(state_path.read_text())["documents"]["42"]["graph"] == graph


def test_graph_success_keeps_never_started_ocr_pending(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["documents"][0]["pending_steps"] = ["ocr"]
    write(manifest, data)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads((tmp_path / "state/funds/state.json").read_text())[
        "documents"
    ]["42"]
    assert document["graph"]["version"] == VERSION
    assert document["pending"]["version"] == VERSION
