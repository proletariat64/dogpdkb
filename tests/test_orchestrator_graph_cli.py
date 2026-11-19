"""Graph orchestration contracts through the executable CLI and public artifacts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.smoke_install import GRAPHIFY_PREFLIGHT, GRAPHIFY_PROOF
from tests.source_fixture import graphify_offline_env, graphify_sse, yuque_binding

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
            "schema": "dogpdkb-run-2",
            "run_id": run.name,
            "targets": {
                "sources": yuque_binding("https://example.invalid/book"),
                "corpus": str(corpus),
                "graph": str(corpus / "graphify-out"),
            },
            "book": {
                "key": "funds",
                "sources": yuque_binding("https://example.invalid/book"),
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
            "schema": "dogpdkb-state-2",
            "book": {
                "key": "funds",
                "sources": yuque_binding("https://example.invalid/book"),
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
                        "metadata": document["metadata"],
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
                "sources": [
                    {
                        "key": "team",
                        "type": "yuque",
                        "url": "https://example.invalid/book",
                    }
                ],
                "corpus": "corpus",
            }
        ],
    )
    tool = tmp_path / "graphify.py"
    tool.write_text(
        GRAPHIFY_PREFLIGHT
        + """import json,os,sys
from pathlib import Path
root=Path(sys.argv[1]); command=sys.argv[2]; args=sys.argv[3:]
def value(flag): return args[args.index(flag)+1]
with (root/'calls.jsonl').open('a') as log: log.write(json.dumps(sys.argv[2:])+'\\n')
assert command=='extract', command
assert '--no-cluster' in args
assert value('--backend')=='openai-oauth'
assert value('--max-concurrency')=='3'
assert os.environ['GRAPHIFY_OPENAI_OAUTH_PARALLEL']=='1'
assert os.environ['GRAPHIFY_OUT']=='graphify-out'
output=Path(value('--out'))/'graphify-out'; output.mkdir(parents=True,exist_ok=True)
mode=(root/'mode').read_text() if (root/'mode').exists() else 'success'
if mode=='interrupt': sys.exit(130)
if mode in ('failed','partial'):
 (output/'graph.json').write_text('{"nodes":[{"id":"unpublished"}],"links":[]}')
 sys.exit(1)
if mode=='unknown':sys.exit(1)
if mode=='invalid': (output/'graph.json').write_text('invalid'); sys.exit(0)
if mode=='mutate': next(Path(args[0]).glob('*.md')).write_text('changed during extraction')
if mode=='grow-incomplete':
 (output/'graph.json').write_text(json.dumps({'nodes':[{'id':'old'}, {'id':'new'}], 'links':[]}))
 (output/'cache').mkdir(exist_ok=True)
 (output/'cache/entry').write_text('unpublishable')
 (output/'manifest.json').write_text('{}')
 print('[graphify extract] chunk 1/2 done')
 print('[graphify] WARNING: 1/2 semantic chunk(s) failed — see errors above. Partial results returned.')
 sys.exit(0)
(output/'graph.json').write_text(json.dumps({'nodes':[], 'links':[], 'graph':{}, 'hyperedges':[]}))
(output/'manifest.json').write_text('{}')
"""
        + GRAPHIFY_PROOF
    )
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\n[tools]\ngraphify='
        + json.dumps([sys.executable, str(tool), str(tmp_path)])
        + "\n"
    )
    return config, manifest, source


def cli(config, manifest, environment=None, *, retry_graph=False, graphify_inputs=None):
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
            *(["--retry-graph"] if retry_graph else []),
            *(["--graphify-inputs", graphify_inputs] if graphify_inputs else []),
        ],
        env=environment or {**os.environ, "PYTHONPATH": str(ROOT / "src")},
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
    assert [row[0] for row in calls] == ["extract"]
    assert Path(calls[0][1]) != source.parent
    assert (Path(calls[0][1]) / source.name).read_bytes() == source.read_bytes()
    assert "--no-cluster" in calls[0]
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


@pytest.mark.parametrize(
    "mode,code", [("failed", 2), ("invalid", 1), ("interrupt", 130)]
)
def test_explicit_graph_retry_recovers_same_run_and_keeps_failure_evidence(
    tmp_path, mode, code
):
    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text(mode)
    assert cli(config, manifest).returncode == code
    run_path = manifest.parent / "run.json"
    before = json.loads(run_path.read_text())
    report = Path(before["artifacts"]["graph_report"])
    evidence = report.read_bytes()
    (tmp_path / "mode").write_text("success")

    result = cli(config, manifest, retry_graph=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 2
    assert report.read_bytes() == evidence
    after = json.loads(run_path.read_text())
    assert after["failures"] == before["failures"]
    assert after["artifacts"]["graph_report"] != str(report)
    document = json.loads((tmp_path / "state/funds/state.json").read_text())[
        "documents"
    ]["42"]
    assert document["graph"]["status"] == "completed"
    assert document["steps"]["graph"]["status"] == "completed"
    assert not document["blocked_versions"]["graph"]
    assert (source.parent / "graphify-out/graph.json").is_file()


def test_graph_retry_refuses_another_runs_failure(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["42"]["steps"]["graph"]["run_id"] = "another-run"
    write(state_path, state)
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest, retry_graph=True).returncode == 2
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_graph_retry_refuses_changed_source_before_tool_call(tmp_path):
    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    source.write_text("Changed after failure")
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest, retry_graph=True).returncode == 1
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_graph_retry_does_not_authorize_unselected_blocked_sources(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["43"] = {
        "corpus": {"version": VERSION},
        "steps": {
            "graph": {
                "status": "failed",
                "version": VERSION,
                "run_id": manifest.parent.name,
            }
        },
    }
    write(state_path, state)
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest, retry_graph=True).returncode == 2
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_graph_retry_cannot_bypass_target_lock(tmp_path):
    import fcntl

    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    before = (tmp_path / "calls.jsonl").read_bytes()
    with (source.parent / ".dogpdkb.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = cli(config, manifest, retry_graph=True)
    assert result.returncode == 1
    assert "another writer" in result.stderr
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_graph_retry_recovers_running_attempt_after_owner_exits(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["42"]["steps"]["graph"] = {
        "status": "running",
        "version": VERSION,
        "run_id": manifest.parent.name,
    }
    write(state_path, state)
    result = cli(config, manifest, retry_graph=True)
    assert result.returncode == 0, result.stderr


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


def test_failed_directory_batch_advances_no_source(tmp_path):
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
            "metadata": second["metadata"],
        },
    }
    write(state_path, state)
    (tmp_path / "mode").write_text("partial")
    result = cli(config, manifest)
    assert result.returncode == 2, result.stdout + result.stderr
    documents = json.loads(state_path.read_text())["documents"]
    assert "graph" not in documents["42"]
    assert documents["42"]["steps"]["graph"]["status"] == "failed"
    assert documents["43"]["graph"] == old
    assert documents["43"]["steps"]["graph"]["status"] == "failed"


def test_interrupt_marks_directory_batch_unknown(tmp_path):
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
            "metadata": second["metadata"],
        },
    }
    write(state_path, state)
    (tmp_path / "mode").write_text("interrupt")
    result = cli(config, manifest)
    assert result.returncode == 130, result.stdout + result.stderr
    documents = json.loads(state_path.read_text())["documents"]
    assert documents["42"]["steps"]["graph"]["status"] == "unknown"
    assert documents["43"]["steps"]["graph"]["status"] == "unknown"
    assert documents["43"]["pending"]["version"] == VERSION
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert len(calls) == 1


def test_old_manifest_cannot_replace_a_newer_successful_graph(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    graph = {"status": "completed", "version": {"utc": "2026-11-13T10:00:00+00:00"}}
    data["documents"]["42"]["graph"] = graph
    write(state_path, data)
    result = cli(config, manifest)
    assert result.returncode == 1
    assert "newer graph version exists" in result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    assert json.loads(state_path.read_text())["documents"]["42"]["graph"] == graph


@pytest.mark.parametrize("current_graph, expected", [("v2", 0), ("v11", 1)])
def test_opaque_graph_versions_use_frozen_success_facts(
    tmp_path, current_graph, expected
):
    config, manifest, _source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["documents"][0].update(
        version={"value": "v10"},
        previous_graph={"status": "completed", "version": {"value": "v2"}},
    )
    write(manifest, data)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    document = state["documents"]["42"]
    document["corpus"]["version"] = {"value": "v10"}
    document["graph"] = {
        "status": "completed",
        "version": {"value": current_graph},
    }
    write(state_path, state)
    result = cli(config, manifest)
    assert result.returncode == expected, result.stdout + result.stderr
    if expected == 0:
        assert json.loads(state_path.read_text())["documents"]["42"]["graph"][
            "version"
        ] == {"value": "v10"}
    else:
        assert "generate a new plan" in result.stderr
        assert not (tmp_path / "calls.jsonl").exists()


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


def test_changed_input_in_same_run_and_version_is_compiled_again(tmp_path):
    config, manifest, source = fixture(tmp_path)
    assert cli(config, manifest).returncode == 0
    before = (tmp_path / "calls.jsonl").read_text().splitlines()
    source.write_text("Full source with newly completed OCR.\n")
    data = json.loads(manifest.read_text())
    Path(data["documents"][0]["input_copy"]).write_bytes(source.read_bytes())
    assert cli(config, manifest).returncode == 0
    calls = (tmp_path / "calls.jsonl").read_text().splitlines()
    assert len(calls) == len(before) + 1


def test_unknown_pending_version_does_not_lose_acknowledged_graph_success(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    data["documents"]["42"]["pending"]["version"] = None
    write(state_path, data)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(state_path.read_text())["documents"]["42"]
    assert document["graph"]["version"] == VERSION
    assert document["pending"]["version"] is None


def test_superseded_metadata_manifest_is_refused(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["42"]["corpus"]["metadata"] = {"logical_path": "New folder"}
    write(state_path, state)
    result = cli(config, manifest)
    assert result.returncode == 1
    assert "generate a new plan" in result.stderr
    assert not (tmp_path / "calls.jsonl").exists()


def test_existing_graph_without_manifest_stops_before_extraction(tmp_path):
    config, manifest, source = fixture(tmp_path)
    graph = source.parent / "graphify-out/graph.json"
    write(graph, {"nodes": [], "links": []})
    before = graph.read_bytes()
    result = cli(config, manifest)
    assert result.returncode == 1, result.stderr
    assert graph.read_bytes() == before
    assert not (tmp_path / "calls.jsonl").exists()


def test_failed_candidate_keeps_public_graph_and_manifest_bytes(tmp_path):
    config, manifest, source = fixture(tmp_path)
    graph = source.parent / "graphify-out/graph.json"
    native_manifest = graph.with_name("manifest.json")
    write(graph, {"nodes": [{"id": "old"}], "links": []})
    write(native_manifest, {"preserved": True})
    before = graph.read_bytes(), native_manifest.read_bytes()
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    assert (graph.read_bytes(), native_manifest.read_bytes()) == before


def test_unsupported_graphify_is_stopped_before_extracting_corpus(tmp_path):
    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text("unsupported")

    result = cli(config, manifest)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    assert not (source.parent / "graphify-out/graph.json").exists()
    run = json.loads((manifest.parent / "run.json").read_text())
    assert run["failures"][-1]["reason"] == "GRAPHIFY_NATIVE_PROOF_UNSUPPORTED"


def test_growing_partial_candidate_keeps_entire_generation_and_suppresses(tmp_path):
    config, manifest, source = fixture(tmp_path)
    graph = source.parent / "graphify-out/graph.json"
    native_manifest = graph.with_name("manifest.json")
    cache = graph.parent / "cache/entry"
    write(graph, {"nodes": [{"id": "old"}], "links": []})
    write(native_manifest, {})
    cache.parent.mkdir()
    cache.write_bytes(b"previous trusted entry")
    generation = [graph, native_manifest, cache]
    before = [path.read_bytes() for path in generation]
    (tmp_path / "mode").write_text("grow-incomplete")

    first = cli(config, manifest)
    assert first.returncode == 2, first.stdout + first.stderr
    assert [path.read_bytes() for path in generation] == before
    assert cli(config, manifest).returncode == 2
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 1


def test_successful_exit_without_native_current_byte_coverage_is_partial(tmp_path):
    config, manifest, source = fixture(tmp_path)
    tool = tmp_path / "graphify.py"
    tool.write_text(
        tool.read_text().removesuffix(GRAPHIFY_PROOF)
        + "print('[graphify extract] chunk 1/1 done')\n"
    )

    result = cli(config, manifest)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (source.parent / "graphify-out/graph.json").exists()


def test_changed_input_during_extraction_does_not_publish(tmp_path):
    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text("mutate")
    assert cli(config, manifest).returncode == 1
    assert not (source.parent / "graphify-out/graph.json").exists()


@pytest.mark.parametrize("folder", ["", "archive"])
def test_unregistered_historical_sources_are_not_backfilled(tmp_path, folder):
    config, manifest, source = fixture(tmp_path)
    historical = source.parent / folder / "Historical-2020-01-01-[99].md"
    historical.parent.mkdir(parents=True, exist_ok=True)
    historical.write_text("Historical")
    result = cli(config, manifest)
    assert result.returncode == 1
    assert not (tmp_path / "calls.jsonl").exists()


def test_original_graphify_incremental_directory_preserves_other_sources(tmp_path):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    executable = shutil.which("graphify")
    if not executable:
        pytest.skip("original Graphify CLI is not installed")
    config, manifest, source = fixture(tmp_path)
    kept = source.with_name("Unchanged-[99].md")
    kept.write_text("# Unchanged beta source\nBeta stays unchanged.\n")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/codex/responses"
            assert self.headers["chatgpt-account-id"] == "offline-fixture"
            body = request["input"][0]["content"][0]["text"]
            calls.append(body)
            nodes = []
            for name in (source.name, kept.name):
                if name in body:
                    ident = (
                        "kept"
                        if name == kept.name
                        else "fresh"
                        if "Changed alpha" in body
                        else "old"
                    )
                    nodes.append(
                        {
                            "id": ident,
                            "label": ident,
                            "file_type": "concept",
                            "source_file": name,
                        }
                    )
            result = graphify_sse(nodes)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = graphify_offline_env(tmp_path, server.server_port)
    try:
        before = subprocess.run(
            [
                executable,
                "extract",
                str(source.parent),
                "--no-cluster",
                "--backend",
                "openai-oauth",
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert before.returncode == 0, before.stdout + before.stderr
        source.write_text("# Changed alpha\nThis is the revised full alpha source.\n")
        data = json.loads(manifest.read_text())
        Path(data["documents"][0]["input_copy"]).write_bytes(source.read_bytes())
        config.write_text(
            config.read_text().split("[tools]")[0]
            + "[tools]\ngraphify="
            + json.dumps(executable)
            + "\n"
        )
        result = cli(config, manifest, environment)
        assert result.returncode == 0, result.stdout + result.stderr
        graph = json.loads((source.parent / "graphify-out/graph.json").read_text())
        assert {n["id"] for n in graph["nodes"]} == {"kept", "fresh"}
        assert len(calls) == 2
        assert source.name in calls[1]
        assert kept.name not in calls[1]
        count = len(calls)
        assert cli(config, manifest, environment).returncode == 0
        assert len(calls) == count
    finally:
        server.shutdown()
        server.server_close()


def test_usable_corpus_compiles_despite_separate_apply_failures(tmp_path):
    config, manifest, _ = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["failures"] = [{"document_id": "43", "step": "ocr", "reason": "fixture"}]
    write(manifest, data)
    result = cli(config, manifest)
    assert result.returncode == 2, result.stdout + result.stderr
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert state["documents"]["42"]["graph"]["version"] == VERSION
