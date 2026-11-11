"""Unified sync runner and read-only status, through the installed CLI entry."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("capture", "mdcompare", "plan", "ocr", "compile")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Workspace:
    """A temporary corpus driven by the existing local Yuque proxy double."""

    def __init__(self, tmp_path: Path, documents: list[dict[str, Any]]):
        from tests.test_knowledge_capture import write_remote_adapter

        self.root = tmp_path
        self.documents = documents
        write_remote_adapter(tmp_path, documents)
        self.corpus = tmp_path / "corpus" / "fund"
        self.corpus.mkdir(parents=True)
        (self.corpus / "One-2026-11-10-[1].md").write_text(
            "# One\n\nOld rule.\n", encoding="utf-8"
        )
        graph = self.corpus / "graphify-out"
        graph.mkdir()
        write_json(
            graph / "graph.json",
            {
                "nodes": [{"id": "old", "source_file": "One-2026-11-10-[1].md"}],
                "links": [],
            },
        )
        write_json(
            tmp_path / "books.json",
            [{"key": "fund", "url": "https://example.invalid/team/uat", "name": "UAT"}],
        )
        self.config = tmp_path / "config.toml"
        self.config.write_text(
            '[source]\nbooks_file="books.json"\n'
            '[paths]\ncorpus="corpus"\nstaging="wash"\nstate="state"\nweb="web"\n'
            '[skylark]\nmcp_url="http://127.0.0.1:9/mcp"\n'
            f'proxy_bin="{tmp_path / "fake-proxy.py"}"\n'
            "timeout_seconds=5\nmax_retries=0\nconcurrency=1\n",
            encoding="utf-8",
        )
        self.staging = tmp_path / "wash"
        self.env = os.environ.copy()
        for name in list(self.env):
            if name.startswith(("DOGPDKB_", "SKYLARK_")) or name in {
                "GRAPHIFY_PYTHON",
                "UTOO_PROXY_BIN",
                "PADDLEOCR_TOKEN",
            }:
                self.env.pop(name)
        self.env["PYTHONPATH"] = str(ROOT / "src")
        self.env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
        # Never let an omitted --config fall back to the checkout's real config.
        self.env["DOGPDKB_CONFIG"] = str(self.config)
        initialized = self.cli(
            "init-state", "--config", str(self.config), "--book", "fund"
        )
        assert initialized.returncode == 0, initialized.stderr

    def cli(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "dogpdkb", *args],
            cwd=self.root,
            env={**self.env, **kwargs.pop("env", {})},
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
            **kwargs,
        )

    def sync(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "sync", "--config", str(self.config), "--book", "fund", *args, **kwargs
        )

    def status(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return self.cli("status", "--config", str(self.config), *args, **kwargs)

    def runs(self) -> list[Path]:
        return sorted((self.staging / "fund").glob("run-*"))

    def only_run(self) -> Path:
        runs = self.runs()
        assert len(runs) == 1, f"expected exactly one run, found {runs}"
        return runs[0]

    def result(self, *args: str) -> dict[str, Any]:
        completed = self.sync(*args)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def spawn(self, *args: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "dogpdkb",
                "sync",
                "--config",
                str(self.config),
                "--book",
                "fund",
                *args,
            ],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    def block_remote(self, seconds: int = 120) -> None:
        """Make the Yuque boundary hang so a live batch can be observed."""
        proxy = self.root / "fake-proxy.py"
        proxy.write_text(
            f"#!{sys.executable}\nimport time\ntime.sleep({seconds})\n",
            encoding="utf-8",
        )
        proxy.chmod(0o755)
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "timeout_seconds=5", f"timeout_seconds={seconds}"
            ),
            encoding="utf-8",
        )

    def wait_for_stage(self, stage: str, status: str = "running") -> Path:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            for run in self.runs():
                if not (run / "run.json").is_file():
                    continue
                state = read_json(run / "run.json")
                if state["stage_states"][stage]["status"] == status:
                    return run
            time.sleep(0.05)
        raise AssertionError(f"{stage} never reached {status}")


DOCUMENTS = [
    {
        "id": 1,
        "title": "One",
        "slug": "one",
        "content_updated_at": "2026-11-10T10:00:00+08:00",
    },
    {
        "id": 2,
        "title": "Two",
        "slug": "two",
        "content_updated_at": "2026-11-10T11:00:00+08:00",
    },
]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, DOCUMENTS)


def test_stop_after_capture_pauses_a_resumable_run(workspace: Workspace):
    completed = workspace.sync("--stop-after", "capture")
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    # stderr carries the stage, its progress and a UTC+8 timestamp.
    lines = completed.stderr.strip().splitlines()
    assert all(
        re.match(r"^\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d.*\+08:00\] ", line)
        for line in lines
    )
    assert any("capture: started" in line for line in lines)
    assert any("capture: completed" in line and "completed=2" in line for line in lines)
    assert any("paused: stop-after capture" in line for line in lines)

    assert result["status"] == "paused"
    assert result["stages"]["capture"] == "completed"
    run_dir = workspace.only_run()
    assert Path(result["run_dir"]) == run_dir
    state = read_json(run_dir / "run.json")
    assert state["status"] == "paused"
    assert state["stage_states"]["capture"]["status"] == "completed"
    assert all(
        state["stage_states"][name]["status"] == "pending" for name in STAGES[1:]
    )
    snapshot = read_json(run_dir / "capture" / "snapshot.json")
    assert snapshot["schema_version"] == "knowledge-capture-1.0"
    assert snapshot["selected"] == [1, 2]
    assert snapshot["counts"]["captured"] == 2
    assert (run_dir / "logs" / "capture.log").is_file()


def test_a_failed_compile_ends_the_batch_loudly_after_ocr(workspace: Workspace):
    completed = workspace.sync()

    assert completed.returncode != 0
    assert "compile" in completed.stderr
    assert "compile failed" in completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["status"] == "failed"
    assert summary["stages"]["capture"] == "completed"
    assert summary["stages"]["mdcompare"] == "completed"
    assert summary["stages"]["plan"] == "completed"
    assert summary["stages"]["ocr"] == "skipped"
    state = read_json(workspace.only_run() / "run.json")
    assert state["status"] == "failed"
    assert state["stage_states"]["ocr"]["status"] == "skipped"
    assert state["stage_states"]["compile"]["status"] == "failed"
    envelope = read_json(workspace.only_run() / "compile" / "result.json")
    assert envelope["status"] == "failed"
    assert envelope["artifacts"] == {}


def test_resume_reuses_the_frozen_capture_instead_of_re_exporting(workspace: Workspace):
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    snapshot = run_dir / "capture" / "snapshot.json"
    before = snapshot.read_bytes()
    ended_at = read_json(run_dir / "run.json")["stage_states"]["capture"]["ended_at"]

    resumed = workspace.sync("--resume", first["run"])

    assert resumed.returncode != 0  # compile fails via the Graphify shim
    summary = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert summary["run"] == first["run"]
    assert summary["stages"]["capture"] == "completed"
    assert workspace.runs() == [run_dir]
    assert snapshot.read_bytes() == before
    state = read_json(run_dir / "run.json")
    assert state["stage_states"]["capture"]["ended_at"] == ended_at
    assert state["stage_states"]["compile"]["status"] == "failed"


def test_new_batch_refuses_to_replace_a_paused_run_and_names_the_resume_target(
    workspace: Workspace,
):
    first = workspace.result("--stop-after", "capture")

    blocked = workspace.sync("--stop-after", "capture")

    assert blocked.returncode != 0
    assert first["run"] in blocked.stderr
    assert f"--resume {first['run']}" in blocked.stderr
    assert len(workspace.runs()) == 1
    assert read_json(workspace.only_run() / "run.json")["status"] == "paused"


def test_status_identifies_a_crashed_batch_as_interrupted_without_touching_it(
    workspace: Workspace,
):
    workspace.block_remote()
    process = workspace.spawn("--stop-after", "capture")
    try:
        run_dir = workspace.wait_for_stage("capture")
        assert json.loads(workspace.status("--json").stdout)["status"] == "running"
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)
    recorded = (run_dir / "run.json").read_bytes()
    canonical = (workspace.corpus / ".sync-state.json").read_bytes()

    reported = workspace.status("--json")

    assert reported.returncode == 0
    view = json.loads(reported.stdout)
    assert view["run"] == run_dir.name
    assert view["status"] == "interrupted"
    assert view["recorded_status"] == "running"
    assert view["alive"] is False
    assert view["current_stage"] == "capture"
    assert view["resume"] and f"--resume {run_dir.name}" in view["resume"]
    assert (run_dir / "run.json").read_bytes() == recorded
    assert (workspace.corpus / ".sync-state.json").read_bytes() == canonical


def test_legacy_flags_forward_into_the_unified_runner_with_a_deprecation_notice(
    tmp_path: Path,
):
    # Ticket 08 convergence: the superseded approval executor and the old
    # run_pipeline executor are no longer reachable from `dogpdkb sync`. Legacy
    # flags are deprecated compatibility forwarding into the single unified
    # entry, which imposes no human-approval / semantic-quality daily gate.
    for index, flags in enumerate(
        [["--knowledge"], ["--cutoff"], ["--resume", "--cutoff"]]
    ):
        workspace = Workspace(tmp_path / f"case-{index}", DOCUMENTS)
        completed = workspace.sync(*flags, "--stop-after", "capture")

        assert completed.returncode == 0, completed.stderr
        assert "deprecated legacy flags" in completed.stderr
        # The old executors are not reached: no approval-argument error and no
        # legacy run_pipeline recovery command.
        assert "knowledge sync: INVALID_ARGUMENTS" not in completed.stderr
        assert "sync_knowledge_base.py" not in completed.stderr
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        assert summary["status"] == "paused"
        assert summary["stages"]["capture"] == "completed"
        run_dir = workspace.only_run()
        assert read_json(run_dir / "run.json")["schema_version"] == "unified-run-1.0"


def test_zero_work_capture_is_skipped_and_the_batch_still_ends(tmp_path: Path):
    workspace = Workspace(tmp_path, [])

    result = workspace.result("--stop-after", "capture")

    assert result["status"] == "paused"
    assert result["stages"]["capture"] == "skipped"
    assert result["counts"]["total"] == 0
    envelope = read_json(workspace.only_run() / "capture" / "result.json")
    assert envelope["status"] == "skipped"
    assert envelope["anomalies"] == []


def test_partial_capture_hands_its_usable_set_to_the_next_stage(workspace: Workspace):
    # One document's detail disagrees with the listing version: the existing
    # capture contract archives it as an anomaly and still exports the rest.
    remote = read_json(workspace.root / "remote.json")
    remote["details"]["2"]["content_updated_at"] = "2026-11-10T12:00:00+08:00"
    write_json(workspace.root / "remote.json", remote)

    completed = workspace.sync()

    run_dir = workspace.only_run()
    state = read_json(run_dir / "run.json")
    assert state["stage_states"]["capture"]["status"] == "partial"
    assert state["stage_states"]["capture"]["counts"]["completed"] == 2
    # A partial stage ends and the batch moves on; it does not abort here.
    assert state["stage_states"]["mdcompare"]["status"] in {"completed", "partial"}
    assert state["stage_states"]["ocr"]["status"] == "skipped"
    assert state["stage_states"]["compile"]["status"] == "failed"
    assert "compile failed" in completed.stderr
    envelope = read_json(run_dir / "capture" / "result.json")
    assert envelope["status"] == "partial"
    assert [row["document_id"] for row in envelope["anomalies"]] == [2]
    assert envelope["anomalies"][0]["code"] == "detail_newer_than_listing"
    assert envelope["artifacts"]["snapshot"] == "capture/snapshot.json"


def test_capture_that_cannot_observe_the_book_fails_the_batch(workspace: Workspace):
    remote = read_json(workspace.root / "remote.json")
    # An unusable listing page: the book cannot be observed at all. (A single
    # missing *detail* is an archived per-document export failure, not a stage
    # failure; see test_sync_results.py.)
    remote["page_overrides"] = {"0": {"error": "listing unavailable"}}
    write_json(workspace.root / "remote.json", remote)

    completed = workspace.sync()

    assert completed.returncode != 0
    state = read_json(workspace.only_run() / "run.json")
    assert state["status"] == "failed"
    assert state["stage_states"]["capture"]["status"] == "failed"
    assert state["stage_states"]["mdcompare"]["status"] == "pending"
    assert "observation failed" in state["stage_states"]["capture"]["reason"]
    assert "Traceback" not in completed.stderr


def test_resume_refuses_a_run_whose_artifacts_were_removed(workspace: Workspace):
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    recorded = (run_dir / "run.json").read_bytes()
    (run_dir / "capture" / "snapshot.json").unlink()

    resumed = workspace.sync("--resume", first["run"])

    assert resumed.returncode != 0
    assert "missing capture artifact snapshot" in resumed.stderr
    assert workspace.runs() == [run_dir]
    assert (run_dir / "run.json").read_bytes() == recorded


def test_resume_refuses_inputs_that_no_longer_match_the_original_batch(
    workspace: Workspace,
):
    first = workspace.result("--stop-after", "capture")
    books = read_json(workspace.root / "books.json")
    books[0]["url"] = "https://example.invalid/team/another-book"
    write_json(workspace.root / "books.json", books)

    resumed = workspace.sync("--resume", first["run"])

    assert resumed.returncode != 0
    assert "different configuration" in resumed.stderr
    assert len(workspace.runs()) == 1


def test_resume_of_a_failed_batch_retries_only_the_failed_stage(workspace: Workspace):
    failed = workspace.sync()
    assert failed.returncode != 0
    run_dir = workspace.only_run()
    state = read_json(run_dir / "run.json")
    capture = state["stage_states"]["capture"]
    first_attempt = state["stage_states"]["compile"]["started_at"]
    snapshot = (run_dir / "capture" / "snapshot.json").read_bytes()

    resumed = workspace.sync("--resume", run_dir.name)

    assert resumed.returncode != 0
    state = read_json(run_dir / "run.json")
    assert state["stage_states"]["capture"] == capture
    assert state["stage_states"]["compile"]["started_at"] != first_attempt
    assert state["stage_states"]["compile"]["attempts"] == 2
    assert state["stage_states"]["capture"]["attempts"] == 1
    assert (run_dir / "capture" / "snapshot.json").read_bytes() == snapshot
    assert workspace.runs() == [run_dir]


CRASH_AFTER_PLAN_ARTIFACT = """
import os
from pathlib import Path
_replace = os.replace
def crashing_replace(source, destination):
    result = _replace(source, destination)
    if (
        os.environ.get("DOGPDKB_CRASH_AFTER_PLAN") == "1"
        and Path(destination).name == "plan.json"
        and Path(destination).parent.name == "plan"
    ):
        os._exit(137)
    return result
os.replace = crashing_replace
"""


def test_resume_recovers_a_crash_between_plan_artifact_and_stage_record(
    tmp_path: Path,
):
    # Standards J13 / spec:150: a hard crash after the plan artifact lands but
    # before the stage records completion leaves plan.json present with the plan
    # stage still "running". Resume re-runs that non-done stage, so the
    # "output already exists" guard must not permanently block recovery.
    workspace = Workspace(tmp_path, DOCUMENTS)
    shim = tmp_path / "crash-shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(CRASH_AFTER_PLAN_ARTIFACT, encoding="utf-8")
    workspace.env["PYTHONPATH"] = os.pathsep.join([str(shim), str(ROOT / "src")])
    workspace.env["DOGPDKB_CRASH_AFTER_PLAN"] = "1"

    crashed = workspace.sync()
    assert crashed.returncode != 0
    run = workspace.only_run()
    assert (run / "plan" / "plan.json").is_file()
    state = read_json(run / "run.json")
    assert state["stage_states"]["plan"]["status"] == "running"

    # Resume the same run: the plan stage must recover, not fail on the guard.
    workspace.env["DOGPDKB_CRASH_AFTER_PLAN"] = "0"
    workspace.sync("--resume", run.name)
    recovered = read_json(run / "run.json")["stage_states"]["plan"]
    assert recovered["status"] in {"completed", "partial", "skipped"}, recovered
    assert "already exists" not in (recovered.get("reason") or "")


def test_status_without_records_returns_normally(workspace: Workspace):
    canonical = workspace.corpus / ".sync-state.json"
    before = canonical.read_bytes()

    human = workspace.status()
    machine = workspace.status("--json")

    assert human.returncode == 0
    assert "no sync runs recorded" in human.stdout
    assert machine.returncode == 0
    assert json.loads(machine.stdout) == {
        "schema_version": "unified-run-1.0",
        "status": "no-runs",
        "runs": 0,
    }
    assert canonical.read_bytes() == before
    assert workspace.runs() == []


def test_status_reports_the_latest_batch_and_can_name_an_older_one(
    workspace: Workspace,
):
    older = json.loads(workspace.sync().stdout.strip().splitlines()[-1])
    newer = workspace.result("--stop-after", "capture")
    assert len(workspace.runs()) == 2

    latest = workspace.status()

    assert latest.returncode == 0
    assert f"run {newer['run']}" in latest.stdout
    assert "status=paused" in latest.stdout
    assert "current stage: none" in latest.stdout
    assert "stop reason: stop-after capture" in latest.stdout
    # The second batch reuses the first batch's downloads via canonical state.
    assert "items: total=2 completed=2 reused=2 failed=0" in latest.stdout
    assert (
        f"logs: {workspace.staging / 'fund' / newer['run'] / 'logs'}" in latest.stdout
    )
    assert f"--resume {newer['run']}" in latest.stdout

    named = workspace.status("--run", older["run"], "--json")

    assert named.returncode == 0
    view = json.loads(named.stdout)
    assert view["run"] == older["run"]
    assert view["status"] == "failed"
    assert view["recorded_status"] == "failed"
    assert view["exit_code"] == 1
    assert view["stages"]["capture"]["status"] == "completed"
    assert view["stages"]["compile"]["reason"]
    assert view["stages"]["compile"]["result"].endswith("compile/result.json")
    assert view["logs"]["capture"].endswith("logs/capture.log")
    assert f"--resume {older['run']}" in view["resume"]

    unknown = workspace.status("--run", "run-19990101-000000")

    assert unknown.returncode != 0
    assert "unknown run: run-19990101-000000" in unknown.stderr


def test_status_redacts_credential_forms_in_its_error_output(workspace: Workspace):
    # F5 / Standards J16: status reports structural errors (unknown run, schema,
    # key). They carry no secret in practice, but route them through the shared
    # redactor like the stage diagnostics so no token/auth form can ever reach
    # status output. The --run reference is the one user-controlled error vector.
    leaked = workspace.status("--run", "token=supersecret123")

    assert leaked.returncode != 0
    assert "supersecret123" not in leaked.stderr
    assert "token=[redacted]" in leaked.stderr


def test_a_second_batch_is_rejected_while_the_first_holds_the_run_lock(
    workspace: Workspace,
):
    workspace.block_remote()
    process = workspace.spawn("--stop-after", "capture")
    try:
        workspace.wait_for_stage("capture")

        second = workspace.sync("--stop-after", "capture")

        assert second.returncode != 0
        assert "UPDATE_ALREADY_RUNNING" in second.stderr
        assert len(workspace.runs()) == 1
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)


def test_user_interrupt_pauses_the_batch_with_exit_130(workspace: Workspace):
    workspace.block_remote()
    process = workspace.spawn("--stop-after", "capture")
    try:
        run_dir = workspace.wait_for_stage("capture")
        os.kill(read_json(run_dir / "run.json")["pid"], signal.SIGINT)
        assert process.wait(timeout=60) == 130
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)

    state = read_json(run_dir / "run.json")
    assert state["status"] == "paused"
    assert state["exit_code"] == 130
    assert state["stop_reason"] == "user interrupt"
    # The interrupted stage never ended, so it must not stay marked running.
    assert state["stage_states"]["capture"]["status"] == "pending"
    assert state["current_stage"] is None
    blocked = workspace.sync("--stop-after", "capture")
    assert blocked.returncode != 0
    assert f"--resume {run_dir.name}" in blocked.stderr


def test_a_finished_batch_releases_the_claim_and_a_paused_one_keeps_it(
    workspace: Workspace,
):
    canonical = workspace.corpus / ".sync-state.json"
    workspace.result("--stop-after", "capture")
    paused = read_json(canonical)
    assert paused["active_run"] is not None
    # The canonical claim is bound to the frozen capture observation.
    assert paused["active_run"]["staging"] == str(
        workspace.only_run() / "capture" / "snapshot.json"
    )
    assert paused["active_run"]["phase"] == "capture"

    resumed = workspace.sync("--resume", workspace.only_run().name)

    assert resumed.returncode != 0  # ocr is unregistered: a terminal failure
    finished = read_json(canonical)
    assert finished["active_run"] is None
    # The next cron batch may start without clearing anything by hand.
    assert len(workspace.runs()) == 1
    workspace.sync("--stop-after", "capture")
    assert len(workspace.runs()) == 2


def test_tool_output_goes_to_the_stage_log_and_bodies_never_reach_the_console(
    workspace: Workspace,
):
    completed = workspace.sync("--stop-after", "capture")

    assert completed.returncode == 0
    run_dir = workspace.only_run()
    log = (run_dir / "logs" / "capture.log").read_text(encoding="utf-8")
    assert "page 1/1 offset=0 items=2" in log
    assert "page 1/1" not in completed.stderr
    assert "page 1/1" not in completed.stdout
    assert len(completed.stdout.strip().splitlines()) == 1
    records = [
        completed.stdout,
        completed.stderr,
        (run_dir / "run.json").read_text(encoding="utf-8"),
        (run_dir / "capture" / "result.json").read_text(encoding="utf-8"),
    ]
    for text in records:
        assert "body 1" not in text
        assert "body 2" not in text
        assert "Old rule." not in text
