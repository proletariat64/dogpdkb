"""Stage contracts for the unified batch runner.

A stage owns one batch responsibility. The runner executes stages in the fixed
order ``STAGES`` and only enters the next stage after the current one ends.
Handlers are registered by name; requesting an unregistered stage is an explicit
error, never a silent skip or a fabricated success.

Later tickets add ``dogpdkb.stages.<stage>`` exposing ``HANDLER``; nothing else
in the runner has to change.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from dogpdkb.sync_run import (
    STAGE_SCHEMA,
    STAGES,
    RunError,
    RunLayout,
    RunState,
    atomic_json,
    read_json,
)
from dogpdkb.sync_time import now_iso

#: Owner ticket for each stage that this module does not implement itself.
STAGE_OWNERS = {
    "capture": "01",
    "mdcompare": "04",
    "plan": "04",
    "ocr": "05",
    "compile": "06",
}


class StageError(ValueError):
    """A stage cannot produce a trustworthy batch result."""


class StageNotRegistered(StageError):
    """The requested stage has no handler in this installation."""


class StageFailed(StageError):
    """The stage cannot continue; the run must stop with a recorded reason."""


@dataclass
class StageResult:
    """What a handler returns; published verbatim as the stage envelope."""

    status: str = "completed"
    counts: dict[str, int] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "partial", "skipped", "failed"}:
            raise StageError(f"unknown stage status: {self.status}")


class StageLog:
    """Streaming destination for a stage's own and its tools' diagnostics."""

    def __init__(self, path: Path, stream: TextIO):
        self.path = path
        self._stream = stream

    def write(self, text: str) -> None:
        self._stream.write(text if text.endswith("\n") else text + "\n")
        self._stream.flush()

    def stream(self) -> TextIO:
        return self._stream

    def popen_kwargs(self) -> dict[str, Any]:
        """Pass to subprocess so external output streams to disk, not memory."""
        return {"stdout": self._stream, "stderr": subprocess.STDOUT}

    @contextmanager
    def capture(self) -> Iterator[StageLog]:
        """Redirect in-process stdout/stderr into the stage log while it runs."""
        with redirect_stdout(self._stream), redirect_stderr(self._stream):
            yield self


@contextmanager
def stage_log(path: Path) -> Iterator[StageLog]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        yield StageLog(path, stream)


@dataclass
class StageContext:
    """Everything a handler needs; no handler reaches into runner internals."""

    stage: str
    layout: RunLayout
    state: RunState
    config: Any
    book: Any
    corpus: Path
    graph_dir: Path
    progress: Any
    directory: Path
    log: StageLog
    inputs: dict[str, Any] = field(default_factory=dict)

    def artifact(self, stage: str, name: str) -> Path | None:
        """Resolve a named artifact published by an earlier stage."""
        envelope = self.inputs.get(stage) or {}
        reference = (envelope.get("artifacts") or {}).get(name)
        if not isinstance(reference, str):
            return None
        path = self.layout.resolve(reference)
        return path if path.exists() else None

    def require(self, stage: str, name: str) -> Path:
        path = self.artifact(stage, name)
        if path is None:
            raise StageFailed(f"{self.stage} requires the {stage} artifact {name}")
        return path

    def count(self, **counts: int) -> None:
        """Publish item progress atomically without rewriting document state."""
        self.state.progress(self.stage, **counts)


Handler = Callable[[StageContext], StageResult]
_HANDLERS: dict[str, Handler] = {}


def register_handler(stage: str, handler: Handler) -> None:
    if stage not in STAGES:
        raise StageError(f"unknown stage: {stage}")
    _HANDLERS[stage] = handler


def registered_stages() -> tuple[str, ...]:
    return tuple(stage for stage in STAGES if stage in _HANDLERS)


def handler_for(stage: str) -> Handler:
    if stage not in STAGES:
        raise StageError(f"unknown stage: {stage}; expected one of {', '.join(STAGES)}")
    load_handlers()
    handler = _HANDLERS.get(stage)
    if handler is None:
        raise StageNotRegistered(
            f"stage {stage} has no registered handler "
            f"(implemented by ticket {STAGE_OWNERS[stage]}); "
            f"available stages: {', '.join(registered_stages()) or 'none'}"
        )
    return handler


def load_handlers() -> None:
    """Import every available ``dogpdkb.stages.<stage>`` module once."""
    for stage in STAGES:
        if stage in _HANDLERS:
            continue
        try:
            module = importlib.import_module(f"dogpdkb.stages.{stage}")
        except ModuleNotFoundError as error:
            if error.name not in {f"dogpdkb.stages.{stage}", "dogpdkb.stages"}:
                raise
            continue
        handler = getattr(module, "HANDLER", None)
        if handler is None:
            raise StageError(f"dogpdkb.stages.{stage} does not expose HANDLER")
        register_handler(stage, handler)


def write_envelope(
    layout: RunLayout,
    stage: str,
    result: StageResult,
    *,
    started_at: str | None,
    log: Path,
) -> Path:
    """Publish ``<run>/<stage>/result.json``, the contract later stages consume."""
    payload = {
        "schema_version": STAGE_SCHEMA,
        "stage": stage,
        "run_id": layout.run_id,
        "status": result.status,
        "started_at": started_at,
        "ended_at": now_iso(),
        "counts": result.counts,
        "artifacts": result.artifacts,
        "inputs": result.inputs,
        "anomalies": result.anomalies,
        "reason": result.reason,
        "log": layout.relative(log),
    }
    path = layout.result_path(stage)
    atomic_json(path, payload)
    return path


def read_envelope(layout: RunLayout, stage: str) -> dict[str, Any] | None:
    path = layout.result_path(stage)
    if not path.is_file():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != STAGE_SCHEMA:
        raise RunError(f"unsupported stage envelope for {stage}")
    return payload


def report_progress(text: str) -> None:
    """Runner diagnostics go to stderr with a UTC+8 timestamp, never to stdout."""
    print(f"[{now_iso()}] {text}", file=sys.stderr, flush=True)
