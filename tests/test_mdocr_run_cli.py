import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Barrier, Event, Lock, Thread

from PIL import Image


def cli(cwd, *args):
    env = dict(
        os.environ,
        PADDLEOCR_TOKEN="synthetic-fixture-only",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    return subprocess.run(
        [sys.executable, "-m", "mdocr.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=25,
    )


@contextmanager
def service(statuses=lambda _: 200):
    calls = []
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, value, status=200):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            with lock:
                count = len(calls) + 1
                calls.append(count)
            self.send({"data": {"jobId": "job"}}, statuses(count))

        def do_GET(self):
            if self.path == "/result":
                self.send(
                    {
                        "result": {
                            "layoutParsingResults": [
                                {"markdown": {"text": "Recognized."}}
                            ]
                        }
                    }
                )
            else:
                self.send(
                    {
                        "data": {
                            "state": "done",
                            "resultUrl": {
                                "jsonUrl": f"http://127.0.0.1:{self.server.server_port}/result"
                            },
                        }
                    }
                )

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/jobs", calls
        finally:
            server.shutdown()
            thread.join()


def corpus(tmp_path, count):
    src = tmp_path / "source"
    src.mkdir()
    for i in range(count):
        Image.new("RGB", (8, 8), (i, 23, 45)).save(src / f"{i}.png")
        (src / f"{i}.md").write_bytes(
            f"# Body {i}\r\n\r\n![seed]({i}.png)\r\n".encode()
        )


def test_run_writes_bound_evidence_and_never_replays_success(tmp_path):
    corpus(tmp_path, 1)
    with service() as (endpoint, calls):
        plan = cli(
            tmp_path,
            "plan",
            "--mode",
            "paddle",
            "--directory",
            "source",
            "--output-dir",
            "out",
            "--plan",
            "plan.json",
            "--endpoint",
            endpoint,
        )
        assert plan.returncode == 0, plan.stderr
        args = ("run", "--plan", "plan.json", "--result", "result.json")
        first = cli(tmp_path, *args)
        assert first.returncode == 0, first.stderr
        again = cli(tmp_path, *args)
        assert again.returncode == 0, again.stderr
        alt = cli(tmp_path, "run", "--plan", "plan.json", "--result", "alt.json")
        assert alt.returncode == 1
    assert calls == [1]
    result = json.loads((tmp_path / "result.json").read_bytes())
    (doc,) = result["documents"]
    (image,) = doc["ocr"]["images"]
    assert (
        image["source_sha256"]
        == hashlib.sha256((tmp_path / "source/0.png").read_bytes()).hexdigest()
    )
    artifact = Path(image["artifact"]).read_bytes()
    assert image["artifact_sha256"] == hashlib.sha256(artifact).hexdigest()
    output = Path(doc["output"]).read_bytes()
    assert (
        output.replace(b"\n\n" + artifact + b"\n\n", b"![seed](0.png)")
        == (tmp_path / "source/0.md").read_bytes()
    )
    assert b"Recognized." in artifact and b"<!-- mdocr:ocr-end -->" in artifact


def test_real_parallel_fallback_sends_no_rate_limited_job_twice(tmp_path):
    corpus(tmp_path, 8)
    first = Barrier(3, timeout=6)
    second = Barrier(2, timeout=6)

    def status(number):
        if number <= 3:
            first.wait()
        elif number <= 5:
            second.wait()
        return 429 if number <= 5 else 200

    with service(status) as (endpoint, calls):
        plan = cli(
            tmp_path,
            "plan",
            "--mode",
            "paddle",
            "--directory",
            "source",
            "--output-dir",
            "out",
            "--plan",
            "plan.json",
            "--endpoint",
            endpoint,
        )
        assert plan.returncode == 0, plan.stderr
        args = ("run", "--plan", "plan.json", "--result", "result.json")
        first_run = cli(tmp_path, *args)
        assert first_run.returncode == 2, first_run.stderr
        result = json.loads((tmp_path / "result.json").read_bytes())
        assert result["concurrency"]["transitions"] == [
            {"from": 3, "to": 2},
            {"from": 2, "to": 1},
        ]
        assert [row["status"] for row in result["documents"]] == ["partial"] * 5 + [
            "completed"
        ] * 3
        assert cli(tmp_path, *args).returncode == 2
    assert calls == list(range(1, 9))


def test_zero_llm_request_limit_needs_no_pi_or_provider(tmp_path):
    corpus(tmp_path, 1)
    plan = cli(
        tmp_path,
        "plan",
        "--mode",
        "llm",
        "--input",
        "source/0.md",
        "--output",
        "out.md",
        "--plan",
        "plan.json",
        "--request-budget",
        "0",
    )
    assert plan.returncode == 0, plan.stderr
    result = cli(tmp_path, "run", "--plan", "plan.json", "--result", "result.json")
    assert result.returncode == 2, result.stderr
    state = json.loads((tmp_path / "result.json").read_bytes())
    assert state["budget"]["requests"] == 0
    assert state["causes"] == ["OCR_REQUEST_BUDGET_EXHAUSTED"]
    assert not (tmp_path / "out.md").exists()


def _paddle_plan(tmp_path, endpoint, name, *extra):
    result = cli(
        tmp_path,
        "plan",
        "--mode",
        "paddle",
        "--directory",
        "source",
        "--output-dir",
        f"out-{name}",
        "--plan",
        f"{name}.json",
        "--endpoint",
        endpoint,
        *extra,
    )
    assert result.returncode == 0, result.stderr


def test_resume_cache_hit_without_shared_cache(tmp_path):
    corpus(tmp_path, 1)
    with service() as (endpoint, calls):
        for name in ("warm", "reuse"):
            _paddle_plan(tmp_path, endpoint, name, "--cache-dir", "cache")
            assert (
                cli(
                    tmp_path,
                    "run",
                    "--plan",
                    f"{name}.json",
                    "--result",
                    f"{name}-result.json",
                ).returncode
                == 0
            )
        args = ("run", "--plan", "reuse.json", "--result", "reuse-result.json")
        assert (
            json.loads((tmp_path / "reuse-result.json").read_text())["requests"] == []
        )
        shutil.rmtree(tmp_path / "cache")
        result = cli(tmp_path, *args)
        assert result.returncode == 0, result.stderr
        assert calls == [1]


def test_resume_after_rate_limit_stays_at_one(tmp_path):
    corpus(tmp_path, 10)
    first, second = Barrier(3, timeout=6), Barrier(2, timeout=6)

    def status(number):
        if number <= 3:
            first.wait()
        elif number <= 5:
            second.wait()
        return 429

    with service(status) as (endpoint, calls):
        _paddle_plan(tmp_path, endpoint, "throttled")
        args = ("run", "--plan", "throttled.json", "--result", "result.json")
        assert cli(tmp_path, *args).returncode == 2
        assert len(calls) == 6
        assert cli(tmp_path, *args).returncode == 2
        assert len(calls) == 7


def test_distinct_plans_cannot_share_a_live_result(tmp_path):
    corpus(tmp_path, 1)
    entered, release = Event(), Event()

    def status(_):
        entered.set()
        assert release.wait(8)
        return 200

    with service(status) as (endpoint, calls):
        for name in ("alpha", "beta"):
            _paddle_plan(tmp_path, endpoint, name)
        result = []
        thread = Thread(
            target=lambda: result.append(
                cli(tmp_path, "run", "--plan", "alpha.json", "--result", "shared.json")
            )
        )
        thread.start()
        try:
            assert entered.wait(8)
            loser = cli(
                tmp_path, "run", "--plan", "beta.json", "--result", "shared.json"
            )
            assert (
                loser.returncode == 1
                and "result is already being executed" in loser.stderr
            )
        finally:
            release.set()
            thread.join()
        assert result[0].returncode == 0, result[0].stderr
        assert calls == [1]


def test_paddle_and_llm_publish_same_v2_block_shape(tmp_path, monkeypatch):
    from mdocr import runner
    from mdocr.cli import main

    class LocalLlm:
        def __init__(self, *_args):
            pass

        def preflight(self):
            return {"ok": True}

        def convert(self, _body, images, _prompt):
            return {
                "patches": [
                    {
                        "id": image["id"],
                        "kind": "text",
                        "markdown": "Recognized.",
                        "uncertainty": "",
                    }
                    for image in images
                ],
                "usage_tokens": None,
            }

    monkeypatch.setattr(runner, "LlmClient", LocalLlm)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PADDLEOCR_TOKEN", "synthetic-only")
    corpus(tmp_path, 1)
    blocks = {}
    with service() as (endpoint, calls):
        for mode in ("paddle", "llm"):
            plan = f"{mode}-plan.json"
            result = f"{mode}-result.json"
            output = f"{mode}.md"
            extra = ["--endpoint", endpoint] if mode == "paddle" else []
            assert (
                main(
                    [
                        "plan",
                        "--mode",
                        mode,
                        "--input",
                        "source/0.md",
                        "--output",
                        output,
                        "--plan",
                        plan,
                        *extra,
                    ]
                )
                == 0
            )
            assert main(["run", "--plan", plan, "--result", result]) == 0
            evidence = json.loads(Path(result).read_bytes())["documents"][0]["ocr"]
            assert evidence["contract"] == "mdocr-ocr-2"
            (image,) = evidence["images"]
            block = Path(image["artifact"]).read_bytes()
            assert image["artifact_sha256"] == hashlib.sha256(block).hexdigest()
            assert block in Path(output).read_bytes()
            assert block.startswith(b"<!-- mdocr:ocr-begin v=2 id=")
            assert (
                f"> Original image: [source](<{tmp_path / 'source/0.png'}>)".encode()
                in block
            )
            blocks[mode] = block.replace(f"mode={mode}".encode(), b"mode=engine", 1)
    assert blocks["paddle"] == blocks["llm"]
    assert calls == [1]
