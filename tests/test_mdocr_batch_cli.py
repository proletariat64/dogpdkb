"""Batch CLI acceptance through subprocesses and a local HTTP service."""

import json
import os
import subprocess
import sys
from pathlib import Path


def invoke(tmp_path, documents, *extra, token=None):
    manifest = tmp_path / "input.json"
    manifest.write_text(
        json.dumps({"schema_version": "mdocr-batch-input-1.0", "documents": documents})
    )
    report = tmp_path / "report.json"
    env = dict(os.environ)
    env.pop("PADDLEOCR_TOKEN", None)
    if token is not None:
        env["PADDLEOCR_TOKEN"] = token
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdocr.cli",
            "batch",
            "--manifest",
            str(manifest),
            "--output-dir",
            str(tmp_path / "out"),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--report",
            str(report),
            *extra,
        ],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, json.loads(report.read_text()) if report.exists() else None


def test_batch_preserves_body_without_images_and_isolates_missing_document(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text("# Actual Markdown\n\nBody knowledge.\n")
    result, report = invoke(
        tmp_path,
        [
            {"document_id": "good", "path": "raw.md"},
            {"document_id": "missing", "path": "missing.md"},
        ],
    )
    assert result.returncode == 2, result.stderr
    assert report["schema_version"] == "mdocr-batch-1.0"
    assert report["status"] == "partial"
    good, missing = report["documents"]
    assert good["status"] == "completed"
    assert good["resources"] == []
    assert Path(good["output"]).read_text() == raw.read_text()
    assert missing["status"] == "failed"
    assert missing["output"] is None
    assert missing["error"] == "DOCUMENT_READ_FAILED"


from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from PIL import Image


@contextmanager
def paddle_service():
    state = {"posts": 0, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            state["posts"] += 1
            self.reply({"data": {"jobId": "job"}}, state["status"])

        def do_GET(self):
            if self.path == "/result":
                self.reply(
                    {
                        "result": {
                            "layoutParsingResults": [
                                {"markdown": {"text": "Recognized knowledge"}}
                            ]
                        }
                    }
                )
            else:
                self.reply(
                    {
                        "data": {
                            "state": "done",
                            "resultUrl": {
                                "jsonUrl": f"http://127.0.0.1:{self.server.server_port}/result"
                            },
                        }
                    }
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/jobs", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_batch_selects_images_and_reuses_success_without_token(tmp_path):
    Image.new("RGB", (8, 8), "red").save(tmp_path / "a.png")
    Image.new("RGB", (8, 8), "blue").save(tmp_path / "b.png")
    (tmp_path / "raw.md").write_text(
        "Body.\n![one](a.png)\n![again](a.png)\n![two](b.png)\n"
    )
    docs = [{"document_id": "opaque", "path": "raw.md", "resources": ["a.png"]}]
    with paddle_service() as (endpoint, state):
        result, report = invoke(
            tmp_path, docs, "--endpoint", endpoint, token="local-test-only"
        )
        assert result.returncode == 0, result.stderr
        row = report["documents"][0]
        assert [r["status"] for r in row["resources"]] == ["success", "unselected"]
        assert state["posts"] == 1
        text = Path(row["output"]).read_text()
        assert text.count("Recognized knowledge") == 2
        assert "![two](b.png)" in text
        record = row["ocr"]["images"][0]
        assert record["status"] == "success"
        assert (
            Path(record["source_path"]).read_bytes()
            == (tmp_path / "a.png").read_bytes()
        )
        with Image.open(record["prepared_image"]) as image:
            assert image.format == "JPEG"
        artifact = Path(record["artifact"]).read_text()
        assert artifact.startswith('<!-- mdocr:ocr-begin source="')
        docs[0]["resources"] = []
        result, report = invoke(tmp_path, docs, "--endpoint", endpoint)
        assert result.returncode == 0, result.stderr
        assert report["documents"][0]["resources"][0]["status"] == "reused"
        assert state["posts"] == 1


def test_batch_shared_auth_failure_stops_new_calls_and_retry_uses_only_successes(
    tmp_path,
):
    Image.new("RGB", (8, 8), "red").save(tmp_path / "a.png")
    Image.new("RGB", (8, 8), "blue").save(tmp_path / "b.png")
    (tmp_path / "raw.md").write_text("Body survives.\n![one](a.png)\n![two](b.png)\n")
    docs = [{"document_id": "opaque", "path": "raw.md"}]
    with paddle_service() as (endpoint, state):
        state["status"] = 401
        result, report = invoke(
            tmp_path, docs, "--endpoint", endpoint, token="local-test-only"
        )
        assert result.returncode == 2
        assert report["causes"] == ["OCR_AUTH_UNAVAILABLE"]
        assert [r["status"] for r in report["documents"][0]["resources"]] == [
            "failed",
            "unattempted",
        ]
        assert state["posts"] == 1
        assert "Body survives." in Path(report["documents"][0]["output"]).read_text()
        state["status"] = 200
        result, report = invoke(
            tmp_path, docs, "--endpoint", endpoint, token="local-test-only"
        )
        assert result.returncode == 0, result.stderr
        assert state["posts"] == 3
        assert report["counts"] == {"success": 2}
        # The resource URL/path is unchanged, but new original bytes require OCR.
        Image.new("RGB", (8, 8), "green").save(tmp_path / "a.png")
        result, report = invoke(
            tmp_path, docs, "--endpoint", endpoint, token="local-test-only"
        )
        assert result.returncode == 0, result.stderr
        assert report["counts"] == {"success": 1, "reused": 1}
        assert state["posts"] == 4


def test_batch_missing_token_and_expired_budget_publish_unattempted_resources(tmp_path):
    Image.new("RGB", (8, 8), "red").save(tmp_path / "a.png")
    Image.new("RGB", (8, 8), "blue").save(tmp_path / "b.png")
    (tmp_path / "raw.md").write_text("![one](a.png)\n![two](b.png)\n")
    docs = [{"document_id": "opaque", "path": "raw.md"}]
    result, report = invoke(tmp_path, docs)
    assert result.returncode == 2
    assert report["causes"] == ["OCR_AUTH_UNAVAILABLE"]
    assert report["counts"] == {"failed": 1, "unattempted": 1}
    result, report = invoke(tmp_path, docs, "--max-seconds", "0")
    assert result.returncode == 2
    assert report["causes"] == ["BATCH_DEADLINE_EXCEEDED"]
    assert report["counts"] == {"unattempted": 2}


def test_batch_evidence_is_consumable_by_mdcompare_cli(tmp_path):
    Image.new("RGB", (8, 8), "red").save(tmp_path / "a.png")
    (tmp_path / "raw.md").write_text("Body.\n![one](a.png)\n")
    with paddle_service() as (endpoint, _state):
        result, report = invoke(
            tmp_path,
            [{"document_id": "doc", "path": "raw.md"}],
            "--endpoint",
            endpoint,
            token="local-test-only",
        )
    assert result.returncode == 0, result.stderr
    row = report["documents"][0]
    resource = row["resources"][0]
    manifest = {
        "schema_version": "1.0",
        "documents": [
            {
                "knowledge_base": "any",
                "document_id": "doc",
                "content_kind": "raw",
                "path": str(tmp_path / "raw.md"),
                "metadata": {
                    "name": "Title",
                    "logical_path": "dir",
                    "source_ref": "any:doc",
                },
                "resources": [
                    {
                        "resource_id": "a.png",
                        "kind": "image",
                        "url": "a.png",
                        "path": resource["source_path"],
                    }
                ],
                "ocr": row["ocr"],
            }
        ],
    }
    source = tmp_path / "compare-input.json"
    source.write_text(json.dumps(manifest))
    output = tmp_path / "compare-report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "compare",
            "--baseline",
            str(source),
            "--incoming",
            str(source),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    comparison = json.loads(output.read_text())["results"][0]
    assert comparison["ocr"]["status"] == "UNCHANGED"
    assert comparison["action"] == "SKIP_VERIFIED"
