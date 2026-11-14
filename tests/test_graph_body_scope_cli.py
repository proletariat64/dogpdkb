"""Retained full source inputs at the installed orchestration CLI seam."""

import json
from pathlib import Path

import pytest

from tests.test_orchestrator_graph_cli import VERSION, cli, fixture, write


def held_source(tmp_path, manifest, action="retain", *, witness=True, renamed=False):
    source = tmp_path / "corpus" / ("Renamed.md" if renamed else "Held.md")
    source.write_text("# New held source\nNew unverified wording.\n")
    old = tmp_path / "state/funds/inputs/held/graph-baseline.md"
    old.parent.mkdir(parents=True)
    old.write_text("# Old held source\nKnown historical wording.\n")
    published = tmp_path / "published-held.md"
    published.write_bytes(source.read_bytes())
    data = json.loads(manifest.read_text())
    row = {
        "document_id": "held",
        "version": VERSION,
        "path": str(source),
        "input_copy": str(published),
        "metadata": {"name": "Held"},
        "action": action,
    }
    data["documents"].append(row)
    write(manifest, data)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    old_graph = {"status": "unknown", "version": {"utc": "2026-11-11T10:00:00+00:00"}}
    state["documents"]["held"] = {
        "steps": {},
        "corpus": {k: row[k] for k in ("path", "input_copy", "version", "metadata")},
        "graph": old_graph,
        "graph_baseline": {
            "path": str(old),
            "source_file": "Held.md",
            "version": {"value": "old"},
            "metadata": {"name": "Held"},
        },
    }
    if not witness:
        old.unlink()
    write(state_path, state)
    native = tmp_path / "corpus/graphify-out"
    write(native / "graph.json", {"nodes": [{"id": "held"}], "links": []})
    write(native / "manifest.json", {"native": "unchanged"})
    return source, old_graph


@pytest.mark.parametrize("action", ["retain", "defer", "reuse"])
@pytest.mark.parametrize("renamed", [False, True])
def test_native_scan_uses_prior_held_body_and_keeps_held_graph_truth(
    tmp_path, action, renamed
):
    config, manifest, selected = fixture(tmp_path)
    source, old_graph = held_source(tmp_path, manifest, action, renamed=renamed)
    image = selected.parent / "assets/image.png"
    image.parent.mkdir()
    image.write_bytes(b"retained resource")
    tool = tmp_path / "graphify.py"
    tool.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "base=Path(sys.argv[1]); args=sys.argv[3:]; root=Path(args[0])\n"
        "out=Path(args[args.index('--out')+1])/'graphify-out'\n"
        "assert root!=base/'corpus'\n"
        "assert not root.is_relative_to(out.parent), 'native manifest anchor would change'\n"
        "assert (root/'Held.md').read_text()=='# Old held source\\nKnown historical wording.\\n'\n"
        "assert not (root/'Renamed.md').exists()\n"
        "assert (root/'assets/image.png').read_bytes()==b'retained resource'\n"
        f"assert (root/{selected.name!r}).read_text().startswith('# Complete current')\n"
        "assert json.loads((out/'manifest.json').read_text())=={'native':'unchanged'}\n"
        "graph=json.loads((out/'graph.json').read_text())\n"
        "graph['nodes'].append({'id':'fresh'})\n"
        "(out/'graph.json').write_text(json.dumps(graph))\n"
        "(base/'scope-checked').touch()\n"
    )

    result = cli(config, manifest)

    assert result.returncode == (2 if action == "defer" else 0), (
        result.stdout + result.stderr
    )
    assert (tmp_path / "scope-checked").exists()
    assert source.read_text().startswith("# New held source")
    state = json.loads((tmp_path / "state/funds/state.json").read_text())["documents"]
    assert state["held"]["graph"] == old_graph
    assert not state["held"]["steps"]
    assert state["42"]["steps"]["graph"]["status"] == "completed"
    assert {
        n["id"]
        for n in json.loads((selected.parent / "graphify-out/graph.json").read_text())[
            "nodes"
        ]
    } == {"held", "fresh"}
    assert state["42"]["graph_baseline"]["source_file"] == selected.name
    assert (
        Path(state["42"]["graph_baseline"]["path"]).read_bytes()
        == selected.read_bytes()
    )


def test_missing_retained_graph_input_stops_before_external_call(tmp_path):
    config, manifest, source = fixture(tmp_path)
    held_source(tmp_path, manifest, witness=False)
    graph = source.parent / "graphify-out/graph.json"
    before = graph.read_bytes()

    result = cli(config, manifest)

    assert result.returncode == 2, result.stdout + result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    assert graph.read_bytes() == before
    run = json.loads((manifest.parent / "run.json").read_text())
    assert any(
        f["reason"] == "GRAPHIFY_RETAINED_INPUT_REQUIRED" for f in run["failures"]
    )


def test_mirror_does_not_reinterpret_absolute_native_manifest_keys(tmp_path):
    config, manifest, source = fixture(tmp_path)
    held_source(tmp_path, manifest)
    native_manifest = source.parent / "graphify-out/manifest.json"
    write(native_manifest, {str(source): {"semantic_hash": "native-owned"}})
    before = native_manifest.read_bytes()

    result = cli(config, manifest)

    assert result.returncode == 2, result.stdout + result.stderr
    assert native_manifest.read_bytes() == before
    assert not (tmp_path / "calls.jsonl").exists()
    run = json.loads((manifest.parent / "run.json").read_text())
    assert any(
        f["reason"] == "GRAPHIFY_PORTABLE_BASELINE_REQUIRED" for f in run["failures"]
    )


def test_newer_held_failure_does_not_block_compiling_other_graph_inputs(tmp_path):
    config, manifest, _ = fixture(tmp_path)
    held_source(tmp_path, manifest)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    step = {"status": "failed", "version": VERSION, "run_id": "earlier-run"}
    state["documents"]["held"]["steps"]["graph"] = step
    write(state_path, state)

    result = cli(config, manifest)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "calls.jsonl").is_file()
    after = json.loads(state_path.read_text())["documents"]
    assert after["held"]["steps"]["graph"] == step
    assert after["42"]["steps"]["graph"]["status"] == "completed"


@pytest.mark.parametrize("action,code", [("retain", 0), ("defer", 2)])
def test_only_retained_sources_do_not_call_graphify(tmp_path, action, code):
    config, manifest, _ = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["documents"][0]["action"] = action
    write(manifest, data)

    result = cli(config, manifest)

    assert result.returncode == code, result.stdout + result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert "graph" not in state["documents"]["42"]["steps"]


def test_original_graphify_mirror_preserves_held_source_without_dispatch(tmp_path):
    import os
    import shutil
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    executable = shutil.which("graphify")
    if not executable:
        pytest.skip("original Graphify CLI is not installed")
    config, manifest, selected = fixture(tmp_path)
    held, _ = held_source(tmp_path, manifest)
    native = selected.parent / "graphify-out"
    shutil.rmtree(native)
    new_held = held.read_text()
    held.write_text("# Old held source\nKnown historical wording.\n")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = "\n".join(
                message["content"]
                for message in request["messages"]
                if message["role"] == "user"
            )
            calls.append(body)
            nodes = []
            for name in (selected.name, held.name):
                if name in body:
                    ident = (
                        "held"
                        if name == held.name
                        else "fresh"
                        if "Revised alpha" in body
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
            result = json.dumps(
                {
                    "id": "mock",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "mock",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"nodes": nodes, "edges": []}),
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "OPENAI_API_KEY": "offline-fixture",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        "OPENAI_MODEL": "mock",
        "NO_PROXY": "127.0.0.1,localhost",
        "GRAPHIFY_API_TIMEOUT": "5",
        "GRAPHIFY_MAX_RETRIES": "0",
    }
    try:
        baseline = subprocess.run(
            [
                executable,
                "extract",
                str(selected.parent),
                "--no-cluster",
                "--backend",
                "openai",
                "--model",
                "mock",
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert baseline.returncode == 0, baseline.stdout + baseline.stderr
        held.write_text(new_held)
        selected.write_text(
            "# Revised alpha\nNew business value in the selected complete source.\n"
        )
        data = json.loads(manifest.read_text())
        Path(data["documents"][0]["input_copy"]).write_bytes(selected.read_bytes())
        config.write_text(
            config.read_text().split("[tools]")[0]
            + "[tools]\ngraphify="
            + json.dumps(executable)
            + "\n"
        )

        result = cli(config, manifest, environment)

        assert result.returncode == 0, result.stdout + result.stderr
        graph = json.loads((native / "graph.json").read_text())
        assert {node["id"] for node in graph["nodes"]} == {"held", "fresh"}
        assert len(calls) == 2
        assert selected.name in calls[1]
        assert held.name not in calls[1]
        assert held.read_text() == new_held
    finally:
        server.shutdown()
        server.server_close()
