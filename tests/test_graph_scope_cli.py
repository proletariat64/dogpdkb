"""Frozen cross-run holds must never become Graphify work or acknowledgments."""

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from dogpdkb.graph import (
    _graph_install_sha256,
    _mapping_sha256,
    _native_extract,
    _scoped_graph_environment,
    _snapshot,
)
from dogpdkb.settings import document_identity
from tests.test_orchestrator_graph_cli import ROOT, VERSION, fixture, write


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path):
    return json.loads(path.read_bytes())


def _call(config, manifest, *options):
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
            *options,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


def _fixture_runtime(config, tmp_path, public):
    launcher = tmp_path / "venv/bin/graphify"
    launcher.parent.mkdir(parents=True)
    python = launcher.with_name("python")
    python.symlink_to(sys.executable)
    (launcher.parent.parent / "pyvenv.cfg").write_text(
        f"home = {Path(sys.executable).parent}\n"
    )
    script = (tmp_path / "graphify.py").read_text()
    launcher.write_text(
        f"#!{python}\nimport sys\nsys.argv.insert(1, {str(tmp_path)!r})\n" + script
    )
    launcher.chmod(0o700)
    config.write_text(
        config.read_text().split("graphify=")[0]
        + "graphify="
        + json.dumps([str(launcher)])
        + "\n"
    )
    site = tmp_path / "venv/lib/python3/site-packages/graphify"
    site.mkdir(parents=True)
    module = site / "__init__.py"
    module.write_text("# approved wheel payload\n")
    wheel = tmp_path / "approved.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.write(module, "graphify/__init__.py")
    check = tmp_path / "cache-check.json"
    write(check, {"scanned": 2, "cache_hits": 1, "model_misses": 1})
    command = [str(launcher)]
    return {
        "argv": _native_extract(command, "{corpus}", "{workspace}", None, None, 3),
        "environment_sha256": _mapping_sha256(_scoped_graph_environment()),
        "installation_sha256": _graph_install_sha256(command, wheel),
        "native_tree_sha256": _mapping_sha256(_snapshot(public)),
        "wheel": {"path": str(wheel), "sha256": _sha(wheel)},
        "cache_check": {"path": str(check), "sha256": _sha(check)},
    }


def _case(tmp_path):
    config, compile_manifest, new = fixture(tmp_path)
    corpus = new.parent
    old = corpus / "retained.md"
    old_witness = tmp_path / "known-old.md"
    old_witness.write_bytes(b"# Known old body\n")
    old.write_bytes(b"# Later body not covered by the graph\n")
    held = document_identity("team", "43")
    held_path = corpus / f"source-[{held}].md"
    held_path.write_bytes(b"# Historical unresolved body\n")
    state_file = tmp_path / "state/funds/state.json"
    state = _read(state_file)
    hold_record = {"status": "unknown", "run_id": "run-original"}
    state["documents"].update(
        {
            held: {
                "source_key": "team",
                "source_document_id": "43",
                "corpus": {"path": str(held_path), "version": VERSION},
                "steps": {"graph": {**hold_record, "version": VERSION}},
                "blocked_versions": {"graph": {VERSION["utc"]: hold_record}},
            },
            "99": {
                "corpus": {"path": str(old), "version": VERSION},
                "graph_baseline": {
                    "path": str(old_witness),
                    "source_file": old.name,
                    "version": VERSION,
                },
            },
        }
    )
    write(state_file, state)
    public = corpus / "graphify-out"
    write(
        public / "graph.json",
        {"nodes": [{"id": "known", "source_file": old.name}], "links": []},
    )
    write(
        public / "manifest.json",
        {
            old.name: {
                "semantic_hash": hashlib.md5(
                    old_witness.read_bytes(), usedforsecurity=False
                ).hexdigest()
            }
        },
    )
    cache = public / "cache/semantic/entry.json"
    write(cache, {"trusted": True})
    scope = {
        "schema": "dogpdkb-graph-scope-1",
        "book": "funds",
        "run_id": "run-graph",
        "compile_sha256": _sha(compile_manifest),
        "runtime": _fixture_runtime(config, tmp_path, public),
        "baseline": {
            name: _sha(public / name) for name in ("graph.json", "manifest.json")
        },
        "holds": {held: {"version": VERSION, "run_id": hold_record["run_id"]}},
        "inputs": {old.name: _sha(old_witness), new.name: _sha(new)},
        "misses": {new.name: _sha(new)},
        "semantic_cache": {cache.name: _sha(cache)},
    }
    return config, compile_manifest, state_file, held, held_path, public, scope


def _approved_call(config, manifest, scope, tmp_path, *, sha=None):
    path = tmp_path / "approved.json"
    write(path, scope)
    return _call(
        config,
        manifest,
        "--graph-scope",
        str(path),
        "--scope-sha256",
        sha or _sha(path),
    )


def test_scope_excludes_only_approved_hold_and_keeps_historical_witness(tmp_path):
    config, manifest, state_file, held, held_path, public, scope = _case(tmp_path)
    # The approved public pair may contain opaque native edge data; the new
    # hold gate consults the native manifest, not relationship metadata.
    graph = _read(public / "graph.json")
    graph["links"] = [
        {"source": "known", "target": "known", "source_file": held_path.name}
    ]
    write(public / "graph.json", graph)
    scope["baseline"]["graph.json"] = _sha(public / "graph.json")
    scope["runtime"]["native_tree_sha256"] = _mapping_sha256(_snapshot(public))
    before = _read(state_file)["documents"]
    first = _call(config, manifest)
    assert first.returncode == 2
    assert "SAME_VERSION_NOT_RETRIED" in (manifest.parent / "run.json").read_text()
    assert not (tmp_path / "calls.jsonl").exists()
    result = _approved_call(config, manifest, scope, tmp_path)
    assert result.returncode == 0, result.stderr
    report = _read(Path(json.loads(result.stdout)["report"]))
    assert report["omitted_historical_sources"] == [held]
    assert report["graph_scope_sha256"] == _sha(tmp_path / "approved.json")
    assert report["input_sha256"] == scope["inputs"]
    current = _read(state_file)["documents"]
    assert current[held] == before[held] and current["99"] == before["99"]
    assert current["42"]["graph"]["status"] == "completed"
    assert held_path.read_bytes() == b"# Historical unresolved body\n"
    assert set(_read(public / "manifest.json")) == scope["inputs"].keys()
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "defect",
    [
        "approval",
        "compile-binding",
        "canonical",
        "version",
        "owner",
        "suppression",
        "selected",
        "native-manifest",
        "baseline",
        "scan-controls",
        "cache",
        "misses",
    ],
)
def test_unproven_graph_scope_refuses_without_dispatch(tmp_path, defect):
    config, manifest, state_file, held, held_path, public, scope = _case(tmp_path)
    state = _read(state_file)
    doc = state["documents"][held]
    if defect == "compile-binding":
        scope["compile_sha256"] = "0" * 64
    elif defect == "canonical":
        doc["source_document_id"] = "44"
    elif defect == "version":
        scope["holds"][held]["version"] = "other"
    elif defect == "owner":
        scope["holds"][held]["run_id"] = "run-other"
    elif defect == "suppression":
        doc["blocked_versions"]["graph"][VERSION["utc"]]["run_id"] = "run-other"
    elif defect == "selected":
        data = _read(manifest)
        data["documents"].append(
            {
                "document_id": held,
                "action": "compile",
                "version": VERSION,
                "path": str(held_path),
                "input_copy": str(held_path),
            }
        )
        write(manifest, data)
        scope["compile_sha256"] = _sha(manifest)
    elif defect == "native-manifest":
        path = public / "manifest.json"
        write(path, {**_read(path), held_path.name: {}})
        scope["baseline"][path.name] = _sha(path)
    elif defect == "baseline":
        scope["baseline"]["graph.json"] = "0" * 64
    elif defect == "scan-controls":
        (held_path.parent / ".graphifyignore").write_text("*.md\n")
    elif defect == "cache":
        (public / "cache/semantic/entry.json").write_text("changed")
        scope["runtime"]["native_tree_sha256"] = _mapping_sha256(_snapshot(public))
    elif defect == "misses":
        scope["misses"] = {}
    write(state_file, state)
    before = state_file.read_bytes()
    baseline = {
        p.name: p.read_bytes()
        for p in (public / "graph.json", public / "manifest.json")
    }
    result = _approved_call(
        config,
        manifest,
        scope,
        tmp_path,
        sha="0" * 64 if defect == "approval" else None,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    assert state_file.read_bytes() == before
    assert all(
        (public / name).read_bytes() == content for name, content in baseline.items()
    )


def test_scope_does_not_clear_another_historical_suppression(tmp_path):
    config, manifest, state_file, held, held_path, _public, scope = _case(tmp_path)
    state = _read(state_file)
    other = document_identity("team", "80")
    other_path = held_path.with_name(f"source-[{other}].md")
    other_path.write_bytes(held_path.read_bytes())
    doc = state["documents"][held]
    state["documents"][other] = {
        **doc,
        "source_document_id": "80",
        "corpus": {**doc["corpus"], "path": str(other_path)},
    }
    write(state_file, state)
    before = state_file.read_bytes()
    result = _approved_call(config, manifest, scope, tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "SAME_VERSION_NOT_RETRIED" in (manifest.parent / "run.json").read_text()
    assert state_file.read_bytes() == before
    assert not (tmp_path / "calls.jsonl").exists()


@pytest.mark.parametrize(
    "drift",
    [
        "banner-replacement",
        "wheel",
        "installed-module",
        "argv",
        "env",
        "cache-check",
        "native-settings",
    ],
)
def test_scoped_native_runtime_drift_refuses_before_dispatch(
    tmp_path, monkeypatch, drift
):
    config, manifest, state_file, _held, _path, public, scope = _case(tmp_path)
    if drift == "banner-replacement":
        launcher = tmp_path / "venv/bin/graphify"
        launcher.write_text(
            launcher.read_text() + "\n# different build, same version banner\n"
        )
    elif drift == "wheel":
        with (tmp_path / "approved.whl").open("ab") as stream:
            stream.write(b"different bytes")
    elif drift == "installed-module":
        (tmp_path / "venv/lib/python3/site-packages/graphify/__init__.py").write_text(
            "# different installation\n"
        )
    elif drift == "argv":
        with config.open("a") as stream:
            stream.write('\n[graphify]\nmodel = "different-model"\n')
    elif drift == "env":
        monkeypatch.setenv("GRAPHIFY_NO_INCREMENTAL_CACHE", "1")
    elif drift == "cache-check":
        write(tmp_path / "cache-check.json", {"model_misses": 2})
    else:
        write(public / ".graphify_build.json", {"excludes": ["retained.md"]})
    before = state_file.read_bytes()
    result = _approved_call(config, manifest, scope, tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "frozen Graphify" in result.stderr or "approved wheel" in result.stderr
    assert state_file.read_bytes() == before
    assert not (tmp_path / "calls.jsonl").exists()
