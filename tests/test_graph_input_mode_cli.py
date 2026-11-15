"""Graphify receives only Markdown by default through both public entry points."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_graph_body_scope_cli import held_source
from tests.test_orchestrator_graph_cli import ROOT, cli, fixture


@pytest.mark.parametrize("command", ["sync", "update-graph"])
@pytest.mark.parametrize("mode", [None, "text", "all"])
def test_graphify_input_mode_at_public_subprocess_boundary(tmp_path, command, mode):
    config, manifest, source = fixture(tmp_path)
    corpus = source.parent
    files = {
        "assets/image.png": b"image pixels",
        "assets/diagram.svg": b"<svg/>",
        "attachment.pdf": b"%PDF attachment",
        "script.py": b"print('not a document')",
        "notes.txt": b"not Markdown",
        "ocr/result.MD": b"# OCR text\nRecognized image content.\n",
        ".gitignore": b"ignored.md\n",
        ".graphifyignore": b"ignored.md\n",
    }
    for name, content in files.items():
        path = corpus / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    # Supply a native baseline so extra historical Markdown does not require
    # selection for a new full extraction.
    native = corpus / "graphify-out"
    native.mkdir()
    (native / "graph.json").write_text('{"nodes":[],"links":[]}')
    (native / "manifest.json").write_text("{}")
    tool = tmp_path / "graphify.py"
    tool.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "scan=Path(sys.argv[3])\n"
        "files=sorted(str(p.relative_to(scan)) for p in scan.rglob('*') if p.is_file())\n"
        "(Path(sys.argv[1])/'scanned.json').write_text(json.dumps(files))\n"
        "assert (scan/'ocr/result.MD').read_bytes()==b'# OCR text\\nRecognized image content.\\n'\n"
        + tool.read_text()
    )
    args = [sys.executable, "-m", "dogpdkb", command, "--config", str(config)]
    if command == "sync":
        args += ["--resume", manifest.parent.name, "--retry-graph"]
    else:
        args += ["--input", str(manifest)]
    if mode:
        args += ["--graphify-inputs", mode]

    result = subprocess.run(
        args,
        check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    scanned = set(json.loads((tmp_path / "scanned.json").read_text()))
    if mode == "all":
        assert set(files) | {source.name} <= scanned
    else:
        assert scanned == {
            source.name,
            "ocr/result.MD",
            ".gitignore",
            ".graphifyignore",
        }
    for name, content in files.items():
        assert (corpus / name).read_bytes() == content
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    report = state["documents"]["42"]["graph"]["report"]
    assert json.loads(Path(report).read_text())["graphify_inputs"] == (mode or "text")


@pytest.mark.parametrize("command", ["sync", "update-graph"])
def test_invalid_graphify_input_mode_is_rejected_before_work(tmp_path, command):
    result = subprocess.run(
        [sys.executable, "-m", "dogpdkb", command, "--graphify-inputs", "images"],
        check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 64
    assert "invalid choice" in result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("broken", [False, True])
@pytest.mark.parametrize("retained", [False, True])
def test_excluded_image_symlink_does_not_block_text_graph(tmp_path, broken, retained):
    config, manifest, source = fixture(tmp_path)
    if retained:
        held_source(tmp_path, manifest)
    target = tmp_path / "outside.png"
    if not broken:
        target.write_bytes(b"outside image")
    image = source.parent / "assets/image.png"
    image.parent.mkdir()
    image.symlink_to(target)

    result = cli(config, manifest)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.is_symlink()
    assert target.exists() != broken
    if not broken:
        assert target.read_bytes() == b"outside image"
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    report = json.loads(Path(state["documents"]["42"]["graph"]["report"]).read_text())
    assert not (Path(report["corpus"]) / "assets/image.png").is_symlink()
    assert not (Path(report["corpus"]) / "assets/image.png").exists()


def test_markdown_symlink_still_stops_before_external_call(tmp_path):
    config, manifest, source = fixture(tmp_path)
    alias = source.parent / "alias.md"
    alias.symlink_to(source)

    result = cli(config, manifest)

    assert result.returncode == 1
    assert "ordinary files and directories" in result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    assert alias.is_symlink()
