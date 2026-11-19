from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def cli(cwd: Path, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env.pop("PADDLEOCR_TOKEN", None)
    return subprocess.run(
        [PYTHON, "-m", "mdocr.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_plan_freezes_occurrences_and_does_not_publish_output(tmp_path):
    source = tmp_path / "source.md"
    source.write_text(
        "before ![a](pic.png) `![literal](x.png)`\n```md\n![code](y.png)\n```\n![again](pic.png)\n",
        encoding="utf-8",
    )
    (tmp_path / "pic.png").write_bytes(b"image bytes")
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "paddle",
        "--input",
        str(source),
        "--output",
        str(tmp_path / "out.md"),
        "--plan",
        str(tmp_path / "plan.json"),
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["schema_version"] == "mdocr-plan-2"
    assert len(plan["documents"][0]["images"]) == 2
    first, second = plan["documents"][0]["images"]
    text = source.read_text()
    assert text[first["start"] : first["end"]] == "![a](pic.png)"
    assert text[second["start"] : second["end"]] == "![again](pic.png)"
    assert first["resource_id"] == "pic.png"
    assert not (tmp_path / "out.md").exists()


def test_public_help_exposes_plan_and_run_without_provider_setup(tmp_path):
    result = cli(tmp_path, "--help")
    assert result.returncode == 0
    assert "plan" in result.stdout and "run" in result.stdout
    assert not result.stderr
    assert not list(tmp_path.iterdir())


def test_plan_records_remote_reference_without_network_or_a_witness(tmp_path):
    source = tmp_path / "raw.md"
    source.write_bytes(b"Body\r\n![picture](https://example.invalid/a.png)\r\n")
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "llm",
        "--input",
        "raw.md",
        "--output",
        "out.md",
        "--plan",
        "plan.json",
    )
    assert result.returncode == 0, result.stderr
    (doc,) = json.loads((tmp_path / "plan.json").read_bytes())["documents"]
    import hashlib

    assert doc["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    (image,) = doc["images"]
    assert image["start"] == 6
    assert image["source_path"] is None
    assert image["error"] == "REMOTE_IMAGE_NOT_DOWNLOADED"


def test_plan_cannot_overwrite_its_list_input(tmp_path):
    source = tmp_path / "raw.md"
    source.write_text("# Body\n")
    listing = tmp_path / "list.json"
    listing.write_text('["raw.md"]')
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "paddle",
        "--list",
        "list.json",
        "--output-dir",
        "out",
        "--plan",
        "list.json",
    )
    assert result.returncode == 1
    assert "overlaps" in result.stderr
    assert listing.read_text() == '["raw.md"]'


def test_plan_downloads_remote_reference_once_only_when_selected(tmp_path):
    import hashlib

    payload = b"remote image bytes"
    seen = []

    class Server(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(payload)

    with ThreadingHTTPServer(("127.0.0.1", 0), Server) as server:
        port = server.server_port
        source = tmp_path / "source.md"
        source.write_text(
            f"![a](http://127.0.0.1:{port}/a.png)\n"
            f"![b](http://127.0.0.1:{port}/a.png)\n"
            f"![c](http://127.0.0.1:{port}/not-selected.png)\n"
        )
        (tmp_path / "selection.json").write_text(
            json.dumps({str(source): [f"http://127.0.0.1:{port}/a.png"]})
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = cli(
                tmp_path,
                "plan",
                "--mode",
                "paddle",
                "--input",
                "source.md",
                "--output",
                "out.md",
                "--plan",
                "plan.json",
                "--download",
                "--selection",
                "selection.json",
            )
        finally:
            server.shutdown()
            thread.join()
    assert result.returncode == 0, result.stderr
    assert seen == ["/a.png"]
    plan = json.loads((tmp_path / "plan.json").read_bytes())
    a, b, c = plan["documents"][0]["images"]
    assert a["id"] != b["id"]
    assert a["sha256"] == b["sha256"] == hashlib.sha256(payload).hexdigest()
    assert Path(a["source_path"]).read_bytes() == payload
    assert not c["selected"] and c["source_path"] is None
    assert not (tmp_path / "out.md").exists()


def test_plan_preserves_directory_tree_and_explicit_list_order(tmp_path):
    for relative in ("a/First.MD", "b/second.md", "b/notes.txt"):
        path = tmp_path / "source" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Body\n")
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "paddle",
        "--directory",
        "source",
        "--output-dir",
        "out",
        "--plan",
        "directory-plan.json",
    )
    assert result.returncode == 0, result.stderr
    directory = json.loads((tmp_path / "directory-plan.json").read_bytes())
    assert [
        Path(doc["output"]).relative_to(tmp_path / "out").as_posix()
        for doc in directory["documents"]
    ] == ["a/First.MD", "b/second.md"]
    (tmp_path / "paths.json").write_text('["source/b/second.md", "source/a/First.MD"]')
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "llm",
        "--list",
        "paths.json",
        "--output-dir",
        "out",
        "--plan",
        "list-plan.json",
    )
    assert result.returncode == 0, result.stderr
    listing = json.loads((tmp_path / "list-plan.json").read_bytes())
    assert [Path(doc["output"]).name for doc in listing["documents"]] == [
        "second.md",
        "First.MD",
    ]
    assert not (tmp_path / "out").exists()


def test_only_authored_image_outside_multiline_and_nested_code_is_selected(tmp_path):
    (tmp_path / "source.md").write_bytes(
        b"Code `line one\n![literal](same.png)` and `` `![also literal](same.png)` ``.\n\n"
        b"Unmatched ``` run and ![real](same.png).\n"
    )
    (tmp_path / "same.png").write_bytes(b"fixture")
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "paddle",
        "--input",
        "source.md",
        "--output",
        "out.md",
        "--plan",
        "plan.json",
    )
    assert result.returncode == 0, result.stderr
    (image,) = json.loads((tmp_path / "plan.json").read_text())["documents"][0][
        "images"
    ]
    text = (tmp_path / "source.md").read_text()
    assert text[image["start"] : image["end"]] == "![real](same.png)"


def test_code_delimiters_do_not_cross_heading_or_list_item_boundaries(tmp_path):
    (tmp_path / "source.md").write_bytes(
        b"# Heading `\n![heading image](same.png)\n# Another heading `\n\n"
        b"- First `\n- ![list image](same.png)\n- Third `\n\n"
        b"- One item with `code\n  ![example](same.png)` followed by ![real](same.png).\n"
    )
    (tmp_path / "same.png").write_bytes(b"fixture")
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "paddle",
        "--input",
        "source.md",
        "--output",
        "out.md",
        "--plan",
        "plan.json",
    )
    assert result.returncode == 0, result.stderr
    images = json.loads((tmp_path / "plan.json").read_text())["documents"][0]["images"]
    text = (tmp_path / "source.md").read_text()
    assert [text[im["start"] : im["end"]] for im in images] == [
        "![heading image](same.png)",
        "![list image](same.png)",
        "![real](same.png)",
    ]
