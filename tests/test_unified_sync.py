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


def test_legacy_help_prints_help_without_running_a_batch(tmp_path: Path):
    # H5: a legacy flag plus --help must still print help, not strip --help and
    # run a real (unlocked) batch. No run directory may be created.
    workspace = Workspace(tmp_path, DOCUMENTS)
    completed = workspace.sync("--knowledge", "--help")

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()
    assert "--stop-after" in completed.stdout
    assert workspace.runs() == []


def test_legacy_dry_run_stops_after_plan_without_publishing(tmp_path: Path):
    # H11: the legacy --dry-run meant "stop after the plan" (no compile/publish).
    # It must map to --stop-after plan, never silently become a full publication.
    workspace = Workspace(tmp_path, DOCUMENTS)
    before = (workspace.corpus / "graphify-out" / "graph.json").read_bytes()
    completed = workspace.sync("--dry-run")

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["status"] == "paused"
    assert summary["stop_reason"] == "stop-after plan"
    assert summary["stages"]["plan"] == "completed"
    assert summary["stages"]["compile"] == "pending"
    assert (workspace.corpus / "graphify-out" / "graph.json").read_bytes() == before


def test_legacy_dry_run_caps_stop_after_plan_in_any_argument_order(tmp_path: Path):
    # [7] H11 residual: --dry-run must cap the batch at plan (no OCR/publication)
    # regardless of where an explicit --stop-after appears. argparse keeps the
    # LAST --stop-after, so forwarding both plan and the explicit stage would let
    # compile/ocr run despite the no-write flag when the explicit stage is last.
    for index, flags in enumerate(
        [
            ["--dry-run", "--stop-after", "compile"],
            ["--stop-after", "compile", "--dry-run"],
            ["--dry-run", "--stop-after=compile"],
            ["--dry-run", "--stop-after", "ocr"],
        ]
    ):
        workspace = Workspace(tmp_path / f"case-{index}", DOCUMENTS)
        before = (workspace.corpus / "graphify-out" / "graph.json").read_bytes()
        completed = workspace.sync(*flags)

        assert completed.returncode == 0, completed.stderr
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        assert summary["status"] == "paused", (flags, summary)
        assert summary["stop_reason"] == "stop-after plan", (flags, summary)
        assert summary["stages"]["plan"] == "completed", (flags, summary)
        assert summary["stages"]["ocr"] == "pending", (flags, summary)
        assert summary["stages"]["compile"] == "pending", (flags, summary)
        assert (
            workspace.corpus / "graphify-out" / "graph.json"
        ).read_bytes() == before, flags


def test_resume_selects_the_run_under_the_requested_book(tmp_path: Path):
    # M9: a bare run id that exists under two books must resolve within --book,
    # not the alphabetically first book. A colliding run under "aaa" must not
    # hijack (or block) the resume of the "fund" run.
    workspace = Workspace(tmp_path, DOCUMENTS)
    paused = workspace.result("--stop-after", "capture")
    run_id = paused["run"]
    fund_run = workspace.staging / "fund" / run_id
    collision = workspace.staging / "aaa" / run_id
    collision.mkdir(parents=True)
    payload = read_json(fund_run / "run.json")
    payload["book"]["key"] = "aaa"
    write_json(collision / "run.json", payload)

    completed = workspace.sync("--resume", run_id)

    assert "belongs to book aaa" not in completed.stderr
    assert f"resuming run {run_id}" in completed.stderr


def test_status_counts_reflect_live_stage_progress(workspace: Workspace):
    # M10: a paused (not finished) batch has zero run-level counts in run.json;
    # status must aggregate the recorded stage counts so live progress is visible.
    workspace.result("--stop-after", "capture")
    view = json.loads(workspace.status("--json").stdout)
    assert view["status"] == "paused"
    assert view["counts"]["total"] == 2
    assert view["counts"]["completed"] == 2


def test_resume_refreshes_liveness_metadata(tmp_path: Path):
    # M8: a resumed batch runs under a new process. Resume must record the new pid
    # and running status and clear the prior terminal fields, so observed_status
    # reports the live batch instead of mislabelling it interrupted.
    from dogpdkb.sync_run import RunLayout, RunState

    layout = RunLayout(tmp_path / "run-x", "fund")
    layout.root.mkdir(parents=True)
    state = RunState.create(
        layout,
        book={"key": "fund", "name": "F", "url": "u"},
        corpus=tmp_path,
        config=tmp_path / "c.toml",
        identity={},
        stop_after=None,
    )
    state.finish("paused", stop_reason="stop-after capture", exit_code=0)
    state.data["pid"] = 999999  # a stale, dead pid from the original process

    state.begin_resume()

    assert state.data["pid"] == os.getpid()
    assert state.data["status"] == "running"
    assert state.data["ended_at"] is None
    assert state.data["duration_seconds"] is None
    assert state.data["stop_reason"] is None
    assert state.data["exit_code"] is None


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


def test_resume_refuses_frozen_inputs_whose_bytes_changed(workspace: Workspace):
    # H16: resume must byte-check the frozen planning inputs against the capture
    # evidence, not just confirm the report/plan artifacts exist. Altered frozen
    # bytes would otherwise be compiled and published under the original version.
    first = workspace.result("--stop-after", "plan")
    run_dir = workspace.only_run()
    frozen = sorted(
        path
        for path in (run_dir / "mdcompare" / "inputs").glob("*.md")
        if not path.name.endswith("-previous.md")
    )
    assert frozen, "expected frozen planning inputs after stop-after plan"
    target = frozen[0]
    target.write_bytes(target.read_bytes() + b"\ntampered\n")

    resumed = workspace.sync("--resume", first["run"])

    assert resumed.returncode != 0
    assert "frozen input" in resumed.stderr
    assert workspace.runs() == [run_dir]


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
    # The first batch failed at compile (no Graphify in this harness), so its
    # sources keep COMPARISON_PENDING — a compile decision is terminal only once
    # published — and the unchanged second batch re-selects them for download
    # rather than stranding them as no-work.
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


def test_new_batch_reconciles_a_claim_orphaned_by_a_finished_run(workspace: Workspace):
    # H15: a crash after the terminal result is recorded but before the claim is
    # released leaves active_run bound to a finished run. The next cron batch must
    # reconcile that stale claim instead of being blocked forever.
    canonical = workspace.corpus / ".sync-state.json"
    first = workspace.sync()
    assert first.returncode != 0  # compile fails without Graphify: a terminal result
    run_dir = workspace.only_run()
    assert read_json(canonical)["active_run"] is None
    state = read_json(canonical)
    state["active_run"] = {
        "staging": str(run_dir / "capture" / "snapshot.json"),
        "book_key": "fund",
        "phase": "capture",
    }
    write_json(canonical, state)

    second = workspace.sync()

    assert "unfinished sync plan exists" not in second.stderr
    assert len(workspace.runs()) == 2  # a new batch started despite the stale claim
    assert read_json(canonical)["active_run"] is None


def test_resume_reuses_an_incomplete_snapshots_frozen_listing(workspace: Workspace):
    # H14: a crash after the export-partial (incomplete) snapshot was written but
    # before the capture envelope committed must reuse the frozen listing on
    # resume, not refetch the remote and mix new input into the old batch.
    remote = read_json(workspace.root / "remote.json")
    remote["details"]["2"].pop("body", None)  # doc2 export fails -> incomplete snapshot
    write_json(workspace.root / "remote.json", remote)
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    snapshot_path = run_dir / "capture" / "snapshot.json"
    snapshot = read_json(snapshot_path)
    assert snapshot["status"] == "incomplete"
    assert snapshot["listing"]["complete"] is True

    # Simulate the crash window: capture never committed its result envelope.
    state = read_json(run_dir / "run.json")
    state["stage_states"]["capture"]["status"] = "running"
    write_json(run_dir / "run.json", state)
    (run_dir / "capture" / "result.json").unlink()

    # A new remote document appears after the observation was frozen.
    remote = read_json(workspace.root / "remote.json")
    remote["documents"].append(
        {
            "id": 3,
            "title": "Three",
            "slug": "three",
            "content_updated_at": "2026-11-10T12:00:00+08:00",
        }
    )
    remote["details"]["3"] = {
        "id": 3,
        "body": "body 3",
        "content_updated_at": "2026-11-10T12:00:00+08:00",
    }
    write_json(workspace.root / "remote.json", remote)

    resumed = workspace.sync("--resume", first["run"], "--stop-after", "capture")

    assert resumed.returncode == 0, resumed.stderr
    reused = read_json(snapshot_path)
    assert reused["counts"]["documents"] == 2  # frozen listing, not the new remote
    assert reused["listing"]["document_ids"] == [1, 2]


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


def test_abandoned_capture_anomaly_does_not_keep_the_next_batch_partial(
    tmp_path: Path,
):
    # [1] M20 scope gap: an abandoned (given-up, resolved) capture anomaly is no
    # current failure, exactly like a done one. It stays suppressed (no re-export)
    # but must not hand back as a live anomaly that keeps every later same-version
    # batch partial. The plan stage already carries forward only pending.
    workspace = Workspace(tmp_path, DOCUMENTS)
    remote = read_json(workspace.root / "remote.json")
    remote["details"]["2"].pop("body", None)  # doc2 export fails -> capture anomaly
    write_json(workspace.root / "remote.json", remote)
    first = workspace.sync()  # full batch reaches a terminal status (compile fails)
    assert first.returncode != 0

    registry = read_json(workspace.root / "sync-results" / "fund" / "anomalies.json")
    capture_aids = [
        aid
        for aid, row in registry["anomalies"].items()
        if row["stage"] == "capture" and row["document_id"] == 2
    ]
    assert len(capture_aids) == 1, registry["anomalies"]
    aid = capture_aids[0]
    resolved = workspace.cli(
        "anomalies",
        "--config",
        str(workspace.config),
        "--book",
        "fund",
        "--resolve",
        aid,
        "--disposition",
        "abandoned",
    )
    assert resolved.returncode == 0, resolved.stderr
    assert json.loads(resolved.stdout)["disposition"] == "abandoned"

    # The next same-version batch suppresses doc2 (no re-export) and completes
    # capture instead of counting the resolved abandonment as a live failure.
    second = workspace.sync("--stop-after", "capture")
    assert second.returncode == 0, second.stderr
    run_dir = workspace.runs()[-1]
    snapshot = read_json(run_dir / "capture" / "snapshot.json")
    assert 2 not in snapshot["selected"]
    assert any(row["anomaly_id"] == aid for row in snapshot["suppressed"])
    envelope = read_json(run_dir / "capture" / "result.json")
    assert envelope["status"] == "completed", envelope
    assert aid not in {row.get("id") for row in envelope["anomalies"]}


def test_reused_snapshot_rejects_enriched_raw_instead_of_faking_completed(
    tmp_path: Path,
):
    # [0] H14 widened: the crash-window reuse branch never re-validated the
    # referenced raw. A captured raw that acquired OCR-enrichment markers must
    # NOT be handed downstream as verified raw under the original captured count
    # and a fabricated "completed". It becomes a visible CAPTURE_RAW_UNAVAILABLE
    # anomaly (partial), the same shape mdcompare uses for a missing raw, and the
    # snapshot FILE is rewritten so mdcompare also drops the enriched document.
    workspace = Workspace(tmp_path, DOCUMENTS)
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    snapshot_path = run_dir / "capture" / "snapshot.json"
    snapshot = read_json(snapshot_path)
    assert snapshot["status"] == "captured"
    raw2 = run_dir / "capture" / snapshot["documents"]["2"]["raw"]["path"]

    # Simulate the crash window: capture never committed its result envelope.
    state = read_json(run_dir / "run.json")
    state["stage_states"]["capture"]["status"] = "running"
    write_json(run_dir / "run.json", state)
    (run_dir / "capture" / "result.json").unlink()
    # doc2's captured raw acquires an OCR-enrichment marker after the snapshot.
    raw2.write_text(
        raw2.read_text(encoding="utf-8") + "\n<!-- mdocr:ocr-begin -->\n",
        encoding="utf-8",
    )

    resumed = workspace.sync("--resume", first["run"], "--stop-after", "capture")

    assert resumed.returncode == 0, resumed.stderr
    summary = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert summary["stages"]["capture"] == "partial", summary
    envelope = read_json(run_dir / "capture" / "result.json")
    assert envelope["status"] == "partial", envelope
    assert (2, "CAPTURE_RAW_UNAVAILABLE") in {
        (row["document_id"], row["code"]) for row in envelope["anomalies"]
    }
    assert envelope["counts"]["completed"] == 1, envelope["counts"]
    # The snapshot FILE is rewritten so mdcompare drops the enriched doc too.
    reused = read_json(snapshot_path)
    assert {"id": 2, "code": "CAPTURE_RAW_UNAVAILABLE"} in reused["anomalies"]
    assert reused["exports"]["2"]["status"] == "failed"


def test_interrupted_capture_resumes_against_the_frozen_observation(tmp_path: Path):
    # [16] spec:79/112: the observation (scope + versions + selection) must be
    # frozen BEFORE the export loop. A mid-export crash that loses snapshot.json
    # must resume against the original scope, not refetch the remote and silently
    # absorb a brand-new document or an edited version into the old batch.
    workspace = Workspace(tmp_path, DOCUMENTS)
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    capture_dir = run_dir / "capture"
    snapshot_path = capture_dir / "snapshot.json"
    original = read_json(snapshot_path)
    assert original["listing"]["document_ids"] == [1, 2]
    version2 = original["documents"]["2"]["version"]
    raw2_name = original["documents"]["2"]["raw"]["path"]

    # Simulate a mid-export crash: capture never committed, snapshot.json lost,
    # but the staging observation/checkpoint/raws survive.
    state = read_json(run_dir / "run.json")
    state["stage_states"]["capture"]["status"] = "running"
    write_json(run_dir / "run.json", state)
    (capture_dir / "result.json").unlink()
    snapshot_path.unlink()

    # The remote moves on during the pause: doc2 edited + a brand-new doc3.
    remote = read_json(workspace.root / "remote.json")
    remote["documents"][1]["content_updated_at"] = "2026-11-11T09:00:00+08:00"
    remote["details"]["2"] = {
        "id": 2,
        "body": "body 2 EDITED AFTER OBSERVATION",
        "content_updated_at": "2026-11-11T09:00:00+08:00",
    }
    remote["documents"].append(
        {
            "id": 3,
            "title": "Three",
            "slug": "three",
            "content_updated_at": "2026-11-11T08:00:00+08:00",
        }
    )
    remote["details"]["3"] = {
        "id": 3,
        "body": "body 3",
        "content_updated_at": "2026-11-11T08:00:00+08:00",
    }
    write_json(workspace.root / "remote.json", remote)

    resumed = workspace.sync("--resume", first["run"], "--stop-after", "capture")

    assert resumed.returncode == 0, resumed.stderr
    reused = read_json(snapshot_path)
    assert reused["listing"]["document_ids"] == [1, 2], reused["listing"]
    assert reused["counts"]["documents"] == 2
    assert "3" not in reused["exports"]
    # doc2 keeps its ORIGINAL observed version/body, not the edited remote one.
    row2 = reused["exports"]["2"]
    assert row2["detail_version"]["utc"] == version2["utc"]
    assert row2["raw"]["path"] == raw2_name
    assert (capture_dir / row2["raw"]["path"]).read_text(encoding="utf-8") == "body 2"


def test_resume_rejects_capture_raw_tampered_while_paused(tmp_path: Path):
    # [20] H16 scope gap: a capture-only pause bypasses raw integrity because
    # mdcompare is still pending. A captured raw MODIFIED while paused must fail
    # closed on resume, not be frozen + compiled under the original captured
    # source version. Capture-time evidence is kept durably outside wash.
    workspace = Workspace(tmp_path, DOCUMENTS)
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    snapshot = read_json(run_dir / "capture" / "snapshot.json")
    raw1 = run_dir / "capture" / snapshot["documents"]["1"]["raw"]["path"]
    raw1.write_text(
        raw1.read_text(encoding="utf-8") + "\nTAMPERED WHILE PAUSED\n",
        encoding="utf-8",
    )

    resumed = workspace.sync("--resume", first["run"], "--stop-after", "mdcompare")

    assert resumed.returncode != 0
    assert "no longer matches" in resumed.stderr
    # The tampered bytes were never frozen into a mdcompare input.
    assert not (run_dir / "mdcompare" / "result.json").exists()


def test_resume_keeps_availability_path_when_a_paused_capture_raw_is_deleted(
    tmp_path: Path,
):
    # [20] deviation guard: deletion is NOT the tamper case. A deleted captured
    # raw stays on the availability-first path (mdcompare archives
    # CAPTURE_RAW_UNAVAILABLE, partial), never a hard reject (spec:144, protected
    # tests/test_unified_plan.py::test_unavailable_current_input_archives_anomaly_not_compile).
    workspace = Workspace(tmp_path, DOCUMENTS)
    first = workspace.result("--stop-after", "capture")
    run_dir = workspace.only_run()
    snapshot = read_json(run_dir / "capture" / "snapshot.json")
    (run_dir / "capture" / snapshot["documents"]["2"]["raw"]["path"]).unlink()

    resumed = workspace.sync("--resume", first["run"], "--stop-after", "mdcompare")

    assert resumed.returncode == 0, resumed.stderr
    summary = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert summary["stages"]["mdcompare"] == "partial", summary
    report = read_json(run_dir / "mdcompare" / "report.json")
    assert (2, "CAPTURE_RAW_UNAVAILABLE") in {
        (row["document_id"], (row.get("anomaly") or {}).get("code"))
        for row in report["documents"]
    }
