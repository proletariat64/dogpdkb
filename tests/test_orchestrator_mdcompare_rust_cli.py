"""End-to-end work-plan integration against a separately built Rust executable."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from scripts.smoke_install import REMOTE_CLI


@pytest.fixture
def binary():
    configured = os.environ.get("MDCOMPARE_RUST_BINARY")
    if not configured:
        pytest.skip("set MDCOMPARE_RUST_BINARY to a freshly built mdcompare")
    executable = Path(configured).resolve(strict=True)
    assert executable.is_file()
    return executable


def configure(tmp_path, binary, *, scoring=False):
    source = tmp_path / "inbox"
    source.mkdir()
    (tmp_path / "books.json").write_text(
        json.dumps(
            [
                {
                    "key": "funds",
                    "sources": [
                        {"type": "directory", "key": "inbox", "path": "./inbox"}
                    ],
                }
            ]
        )
    )
    fixture = tmp_path / "graphify.py"
    fixture.write_text(REMOTE_CLI)
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[tools]\n'
        + f"mdcompare_rust={json.dumps(str(binary))}\n"
        + 'mdcompare="unexpected-legacy-mdcompare"\n'
        + f"mdocr={json.dumps([sys.executable, '-m', 'mdocr.cli'])}\n"
        + f"graphify={json.dumps([sys.executable, str(fixture), 'graphify'])}\n"
        + ('[paths]\nmdcompare_config="scoring.toml"\n' if scoring else "")
    )
    return source / "rules.md", config


def invoke_sync(config, phase, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "sync",
            "--config",
            str(config),
            "--stop-after",
            phase,
            *map(str, arguments),
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def sync(config, phase):
    result = invoke_sync(config, phase)
    assert result.returncode == 0, result.stderr
    assert "unexpected-legacy-mdcompare" not in result.stderr
    output = json.loads(result.stdout)
    return json.loads(Path(output["run"]).read_text())


def test_offline_sync_publishes_new_work_plan_artifacts(tmp_path, binary):
    note, config = configure(tmp_path, binary)
    note.write_text("# Transfer limits\n\nLimit transfers to 10 units.\n")
    run = sync(config, "plan")
    assert run["sync_policy"]["tools"]["mdcompare_rust"] == [str(binary)]
    artifacts = run["artifacts"]
    report = json.loads(Path(artifacts["semantic"]).read_text())
    assert report["schema_version"] == "mdcompare-semantic-result-1"
    [row] = report["results"]
    assert row["semantic_status"] == "NEW"
    assert row["action"] == "COMPILE"
    for name, schema in (
        ("semantic_commit", "mdcompare-commit-1"),
        ("semantic_diff", "mdcompare-diff-index-1"),
        ("knowledge", "mdcompare-knowledge-result-1"),
        ("compare", "mdcompare-strict-result-1"),
    ):
        assert json.loads(Path(artifacts[name]).read_text())["schema_version"] == schema


def test_resume_rejects_changed_semantic_sidecar(tmp_path, binary):
    note, config = configure(tmp_path, binary)
    note.write_text("# Transfer limits\n\nLimit transfers to 10 units.\n")
    run = sync(config, "plan")
    artifacts = run["artifacts"]
    index = Path(artifacts["semantic_diff"])
    [document] = json.loads(index.read_text())["documents"]
    sidecar = index.parent / document["evidence"]
    sidecar.write_bytes(sidecar.read_bytes() + b"\n")

    previous_run = Path(artifacts["plan"]).with_name("run.json")
    result = invoke_sync(config, "ocr", "--resume", previous_run)
    assert result.returncode == 1, result.stderr
    assert "frozen mdcompare evidence changed" in result.stderr


def test_score_and_route_bind_the_reserved_document_version(tmp_path, binary):
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            packet = json.loads(request["input"][1]["content"][0]["text"])
            calls.append((self.headers.get("Authorization"), packet))
            outcome = {
                "evaluation_status": "evaluated",
                "change_score": 4,
                "checked_operations": list(range(len(packet["operations"]))),
                "changes": [
                    {
                        "operation_id": 0,
                        "before": "100",
                        "after": "50",
                        "ambiguity": None,
                    }
                ],
                "reason": "lower approval threshold",
                "missing_evidence": [],
            }
            event = {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": json.dumps(outcome)}
                            ],
                        }
                    ],
                },
            }
            body = f"data: {json.dumps(event)}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    note, config = configure(tmp_path, binary, scoring=True)
    with HTTPServer(("127.0.0.1", 0), Provider) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            (tmp_path / "scoring.toml").write_text(
                "[score]\nrequest_budget = 1\n"
                f'endpoint = "http://127.0.0.1:{server.server_port}/v1/responses"\n'
            )
            note.write_text(
                "# Transfer limits\n\nPayments above 100 USD require dual approval.\n"
            )
            sync(config, "graph")
            assert not calls

            note.write_text(
                "# Transfer limits\n\nPayments above 50 USD require dual approval.\n"
            )
            run = sync(config, "plan")
        finally:
            server.shutdown()
            worker.join()

    [(authorization, packet)] = calls
    assert authorization is None
    assert packet["operations"]
    artifacts = run["artifacts"]["semantic_scoring"]
    score = json.loads(Path(artifacts["scores"]["path"]).read_text())
    journal = json.loads(Path(artifacts["journal"]["path"]).read_text())
    [row] = score["rows"]
    [attempt] = journal["attempts"]
    assert (row["state"], row["score"], row["admission"]) == ("scored", 4, 1)
    assert attempt["state"] == "scored"
    route = json.loads(Path(artifacts["route"]["path"]).read_text())
    [decision] = route["results"]
    assert decision["routing_reason"] == "SCORE_ABOVE_THRESHOLD"
    assert decision["action"] == "COMPILE"
