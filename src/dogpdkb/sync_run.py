"""Per-run workspace layout, lightweight run state and read-only liveness.

Cross-batch document progress stays in the canonical corpus state
(``sync_state``/``sync_progress``). This module owns only the small per-run
record that ``dogpdkb status`` reads, so a single item never rewrites the full
document state.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dogpdkb.instance_lock import lock_is_held
from dogpdkb.sync_time import UTC8, now_iso, parse_timestamp

RUN_SCHEMA = "unified-run-1.0"
STAGE_SCHEMA = "unified-stage-1.0"
STAGES = ("capture", "mdcompare", "plan", "ocr", "compile")
RUN_STATUSES = ("running", "paused", "completed", "partial", "failed", "interrupted")
STAGE_STATUSES = ("pending", "running", "completed", "partial", "skipped", "failed")
#: Terminal results release the batch claim so the next cron run may start.
FINISHED_STATUSES = ("completed", "partial", "failed")
#: A batch that never reached a terminal result must be resumed, not replaced.
UNFINISHED_STATUSES = ("running", "paused", "interrupted")
DONE_STAGE_STATUSES = ("completed", "partial", "skipped")


class RunError(ValueError):
    """A run cannot be created, interpreted or resumed safely."""


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RunError(f"run state must not be a symlink: {path.name}")
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RunError(f"cannot read {path.name}: {error}") from error


@dataclass(frozen=True)
class RunLayout:
    """``<staging>/<book>/<run-id>/`` and every path a stage may write."""

    root: Path
    book_key: str

    @property
    def run_id(self) -> str:
        return self.root.name

    @property
    def state_path(self) -> Path:
        return self.root / "run.json"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def stage_dir(self, stage: str) -> Path:
        return self.root / stage

    def result_path(self, stage: str) -> Path:
        return self.stage_dir(stage) / "result.json"

    def log_path(self, stage: str) -> Path:
        return self.logs_dir / f"{stage}.log"

    def relative(self, path: Path) -> str:
        """Artifact references are stored relative to the run directory."""
        resolved = Path(path).resolve()
        return (
            str(resolved.relative_to(self.root))
            if resolved.is_relative_to(self.root)
            else str(resolved)
        )

    def resolve(self, reference: str) -> Path:
        path = Path(reference)
        return path if path.is_absolute() else (self.root / path).resolve()


def book_runs(staging: Path, book_key: str | None = None) -> list[RunLayout]:
    """Every recorded run under the wash root, oldest first."""
    root = Path(staging)
    if not root.is_dir():
        return []
    books = [root / book_key] if book_key else sorted(root.iterdir())
    layouts = []
    for book in books:
        if not book.is_dir():
            continue
        for run in sorted(book.glob("run-*")):
            if (run / "run.json").is_file():
                layouts.append(RunLayout(run.resolve(), book.name))
    return layouts


def new_run_layout(staging: Path, book_key: str) -> RunLayout:
    stamp = f"{datetime.now(UTC8):%Y%m%d-%H%M%S}"
    parent = Path(staging) / book_key
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = parent / f"run-{stamp}"
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = parent / f"run-{stamp}-{suffix}"
    candidate.mkdir(mode=0o700)
    return RunLayout(candidate.resolve(), book_key)


def find_run(staging: Path, reference: str, book_key: str | None = None) -> RunLayout:
    """Accept a run id, a run directory path, or a run.json path."""
    path = Path(reference).expanduser()
    if path.name == "run.json":
        path = path.parent
    if path.is_dir():
        layout = RunLayout(path.resolve(), path.parent.name)
        if not layout.state_path.is_file():
            raise RunError(f"no run state in {layout.root}")
        return layout
    for layout in book_runs(staging, book_key):
        if layout.run_id == reference:
            return layout
    raise RunError(f"unknown run: {reference}")


def process_alive(pid: Any) -> bool:
    """A recorded pid is only evidence while it is a live, non-zombie process."""
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return True
    return fields[0] != "Z"


def observed_status(payload: dict[str, Any]) -> tuple[str, bool]:
    """Combine the recorded status with the real process and the user run lock.

    A leftover ``running`` record is never reported as live on timeout alone:
    the process must exist *and* the user-level lock must still be held.
    """
    recorded = payload.get("status")
    if recorded != "running":
        return recorded, False
    alive = process_alive(payload.get("pid")) and lock_is_held()
    return ("running", True) if alive else ("interrupted", False)


def elapsed_seconds(started: Any, ended: Any) -> float | None:
    try:
        delta = parse_timestamp(str(ended)) - parse_timestamp(str(started))
    except (TypeError, ValueError):
        return None
    return round(max(0.0, delta.total_seconds()), 3)


class RunState:
    """The lightweight per-run record, published atomically on every change."""

    def __init__(self, layout: RunLayout, payload: dict[str, Any]):
        self.layout = layout
        self.data = payload

    @classmethod
    def create(
        cls,
        layout: RunLayout,
        *,
        book: dict[str, Any],
        corpus: Path,
        config: Path,
        identity: dict[str, Any],
        stop_after: str | None,
    ) -> RunState:
        started = now_iso()
        payload = {
            "schema_version": RUN_SCHEMA,
            "run_id": layout.run_id,
            "status": "running",
            "book": book,
            "corpus": str(Path(corpus).resolve()),
            "run_dir": str(layout.root),
            "config": str(Path(config).resolve()),
            "config_identity": identity,
            "stages": list(STAGES),
            "stop_after": stop_after,
            "current_stage": None,
            "pid": os.getpid(),
            "started_at": started,
            "updated_at": started,
            "ended_at": None,
            "duration_seconds": None,
            "stop_reason": None,
            "exit_code": None,
            # Reserved run-schema field: resume reuses this same run record
            # instead of creating a linked one, so no predecessor is recorded.
            "resume_of": None,
            "counts": {"total": 0, "completed": 0, "reused": 0, "failed": 0},
            "stage_states": {
                stage: {
                    "status": "pending",
                    "attempts": 0,
                    "started_at": None,
                    "ended_at": None,
                    "updated_at": None,
                    "counts": {},
                    "result": None,
                    "log": layout.relative(layout.log_path(stage)),
                    "reason": None,
                }
                for stage in STAGES
            },
        }
        state = cls(layout, payload)
        state.save()
        return state

    @classmethod
    def load(cls, layout: RunLayout) -> RunState:
        payload = read_json(layout.state_path)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != RUN_SCHEMA
            or not isinstance(payload.get("stage_states"), dict)
        ):
            raise RunError(f"unsupported run state schema in {layout.state_path}")
        missing = [stage for stage in STAGES if stage not in payload["stage_states"]]
        if missing:
            raise RunError(f"run state is missing stages: {', '.join(missing)}")
        return cls(layout, payload)

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        atomic_json(self.layout.state_path, self.data)

    @property
    def status(self) -> str:
        return self.data["status"]

    def stage(self, stage: str) -> dict[str, Any]:
        return self.data["stage_states"][stage]

    def start_stage(self, stage: str) -> None:
        row = self.stage(stage)
        # Each attempt stamps its own start so a retry never reports the paused
        # gap as elapsed work; the appended stage log keeps the earlier attempt.
        row.update(
            status="running",
            attempts=row.get("attempts", 0) + 1,
            started_at=now_iso(),
            ended_at=None,
            reason=None,
        )
        self.data["current_stage"] = stage
        self.data["status"] = "running"
        self.save()

    def progress(self, stage: str, **counts: Any) -> None:
        """Record item progress for one stage without touching document state."""
        row = self.stage(stage)
        row["counts"] = {**row["counts"], **counts}
        row["updated_at"] = now_iso()
        self.save()

    def end_stage(self, stage: str, result: dict[str, Any]) -> None:
        row = self.stage(stage)
        row.update(
            status=result["status"],
            ended_at=now_iso(),
            counts=result.get("counts", {}),
            result=self.layout.relative(self.layout.result_path(stage)),
            reason=result.get("reason"),
        )
        row["updated_at"] = row["ended_at"]
        self.data["current_stage"] = None
        self.save()

    def interrupt_stages(self) -> None:
        """A stage that never ended goes back to pending; resume retries it."""
        for row in self.data["stage_states"].values():
            if row["status"] == "running":
                row.update(status="pending", ended_at=None, reason=None)

    def finish(self, status: str, *, stop_reason: str | None, exit_code: int) -> None:
        if status not in RUN_STATUSES:
            raise RunError(f"unknown run status: {status}")
        ended = now_iso()
        self.data.update(
            status=status,
            ended_at=ended,
            stop_reason=stop_reason,
            exit_code=exit_code,
            current_stage=None,
            duration_seconds=elapsed_seconds(self.data["started_at"], ended),
        )
        totals: dict[str, int] = {"total": 0, "completed": 0, "reused": 0, "failed": 0}
        for row in self.data["stage_states"].values():
            for key in totals:
                value = row.get("counts", {}).get(key, 0)
                if type(value) is int:
                    totals[key] += value
        self.data["counts"] = totals
        self.save()

    def summary(self) -> dict[str, Any]:
        """The concise batch result published on stdout."""
        return {
            "run": self.layout.run_id,
            "book": self.data["book"]["key"],
            "status": self.data["status"],
            "stages": {
                stage: row["status"] for stage, row in self.data["stage_states"].items()
            },
            "counts": self.data["counts"],
            "stop_reason": self.data["stop_reason"],
            "run_dir": str(self.layout.root),
        }
