"""End-to-end work-plan integration against a separately built Rust executable."""

import json
import os
import signal
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
        pytest.skip("set MDCOMPARE_RUST_BINARY to a freshly built mdcomparer")
    executable = Path(configured).resolve(strict=True)
    assert executable.is_file()
    return executable


def configure(tmp_path, binary, *, scoring=False, interrupt=False):
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
    command = str(binary)
    if interrupt:
        wrapper = tmp_path / "interrupt.py"
        wrapper.write_text(
            "import os, signal, subprocess, sys\n"
            "child = subprocess.run(sys.argv[1:], check=False)\n"
            "if sys.argv[2] == 'score' and '--recover' not in sys.argv and child.returncode in {0, 2}:\n"
            "    os.kill(os.getppid(), signal.SIGKILL)\n"
            "raise SystemExit(child.returncode)\n"
        )
        command = [sys.executable, str(wrapper), command]
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[tools]\n'
        + f"mdcompare_rust={json.dumps(command)}\n"
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


def test_recovery_plan_uses_only_submitted_snapshot_sides(tmp_path, binary):
    note, config = configure(tmp_path, binary)
    note.write_text("# Rules\n\nRequire approval.\n")
    collection = sync(config, "collect")
    differences = json.loads(Path(collection["artifacts"]["differences"]).read_text())
    [row] = differences["documents"]
    identity = row["document_id"]
    declarations = [
        {
            "knowledge_base": "funds",
            "document_id": key,
            "side": side,
            "source_copy": str(note),
            "insertions": [],
        }
        for key, side in (
            (identity, "incoming"),
            (identity, "baseline"),
            ("other", "incoming"),
        )
    ]
    (tmp_path / "recovery.json").write_text(
        json.dumps(
            {
                "schema_version": "mdcompare-body-recovery-1",
                "documents": declarations,
            }
        )
    )
    with config.open("a") as stream:
        stream.write('[paths]\nbody_recovery="recovery.json"\n')

    ready = sync(config, "plan")
    artifact = Path(ready["artifacts"]["body_recovery"])
    [frozen] = json.loads(artifact.read_text())["documents"]
    assert (frozen["document_id"], frozen["side"]) == (identity, "incoming")


@pytest.mark.parametrize(
    "interruption",
    [
        None,
        "terminal",
        "missing_result",
        "settled_unsealed",
        "pending",
        "corrupt",
        "mismatched_pin",
    ],
)
def test_score_and_route_bind_the_reserved_document_version(
    tmp_path, binary, interruption
):
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

    note, config = configure(
        tmp_path,
        binary,
        scoring=True,
        interrupt=interruption is not None,
    )
    (note.parent / "guide.pdf").write_bytes(b"%PDF-1.1\n% retained local attachment\n")
    attachment = "\n[Reference](guide.pdf)\n"
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
                + attachment
            )
            sync(config, "graph")
            assert not calls

            note.write_text(
                "# Transfer limits\n\nPayments above 50 USD require dual approval.\n"
                + attachment
            )
            if interruption:
                result = invoke_sync(config, "plan")
                assert result.returncode == -signal.SIGKILL, result.stderr
                [(previous_run, job)] = [
                    (path, data["native_score_job"])
                    for path in (tmp_path / "runs").rglob("run.json")
                    if (data := json.loads(path.read_text())).get("native_score_job")
                ]
                score_root = Path(job["score_work"]).with_suffix(".mdcompare")
                journal_file = score_root / "journal.json"
                if interruption in {
                    "missing_result",
                    "settled_unsealed",
                    "pending",
                    "corrupt",
                }:
                    (score_root / "result.json").unlink()
                if interruption in {"settled_unsealed", "pending"}:
                    journal = json.loads(journal_file.read_text())
                    journal["terminal"] = None
                    if interruption == "pending":
                        journal["attempts"][0].update(
                            state="admitted",
                            score=None,
                            ambiguous=None,
                            usage_tokens=None,
                        )
                    journal_file.write_text(json.dumps(journal))
                if interruption == "corrupt":
                    journal_file.write_text("{")
                if interruption == "mismatched_pin":
                    state_path = tmp_path / "state/funds/state.json"
                    [reserved_document] = json.loads(state_path.read_text())[
                        "documents"
                    ].values()
                    reservation = reserved_document["steps"]["semantic_score"]
                    run = json.loads(previous_run.read_text())
                    run["native_score_job"]["score_work"] += ".mismatch"
                    previous_run.write_text(json.dumps(run))
                journal_before = journal_file.read_bytes()
                result = invoke_sync(config, "plan", "--resume", previous_run)
                assert result.returncode == 0, result.stderr
                run = json.loads(previous_run.read_text())
                if interruption != "mismatched_pin":
                    assert run["native_score_job"] == job
                if interruption in {"pending", "corrupt", "mismatched_pin"}:
                    assert journal_file.read_bytes() == journal_before
                    assert (score_root / "result.json").exists() == (
                        interruption == "mismatched_pin"
                    )
            else:
                run = sync(config, "plan")
        finally:
            server.shutdown()
            worker.join()

    [(authorization, packet)] = calls
    assert authorization is None
    assert packet["operations"]
    if interruption in {"pending", "corrupt", "mismatched_pin"}:
        assert "semantic_scoring" not in run["artifacts"]
        persisted = json.loads((tmp_path / "state/funds/state.json").read_text())
        [document] = persisted["documents"].values()
        if interruption == "mismatched_pin":
            assert document["steps"]["semantic_score"] == reservation
        else:
            assert document["steps"]["semantic_score"]["status"] == "unknown"
            assert document["blocked_versions"]["semantic_score"]
        [failure] = [
            failure
            for failure in run["failures"]
            if failure["reason"] == "SEMANTIC_SCORE_UNVERIFIABLE"
        ]
        if interruption == "mismatched_pin":
            assert "Pinned mdcompare score reservations" in failure["message"]
        return
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
