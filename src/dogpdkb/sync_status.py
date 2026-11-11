"""Read-only ``dogpdkb status``: report a batch without ever advancing it.

Liveness combines the recorded pid with the user-level run lock, so a leftover
``running`` record is reported as ``interrupted`` rather than assumed alive.
Nothing here writes run state, canonical state, or any artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from dogpdkb.sync_run import (
    RUN_SCHEMA,
    STAGES,
    RunError,
    RunLayout,
    book_runs,
    elapsed_seconds,
    find_run,
    observed_status,
    read_json,
)
from dogpdkb.sync_time import now_iso
from dogpdkb.workflows.get_sync_updates import safe_diagnostic
from mdocr.config import config_arguments, configure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dogpdkb status",
        description="Read-only status of the current or most recent sync batch.",
    )
    config_arguments(parser, book=True)
    parser.add_argument("--run", help="run id or run directory; default is the latest")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    return parser


def view(layout: RunLayout, payload: dict[str, Any]) -> dict[str, Any]:
    status, alive = observed_status(payload)
    stages = payload["stage_states"]
    current = payload.get("current_stage")
    if current is None:
        current = next(
            (name for name in STAGES if stages[name]["status"] == "running"), None
        )
    ended = payload.get("ended_at")
    duration = payload.get("duration_seconds")
    if duration is None:
        duration = elapsed_seconds(
            payload.get("started_at"),
            ended or (now_iso() if alive else payload.get("updated_at")),
        )
    reason = payload.get("stop_reason")
    if status == "interrupted" and not reason:
        reason = "process exited without a recorded result"
    book = payload.get("book") or {}
    return {
        "schema_version": RUN_SCHEMA,
        "run": payload["run_id"],
        "book": book,
        "status": status,
        "recorded_status": payload["status"],
        "alive": alive,
        "current_stage": current,
        "stop_after": payload.get("stop_after"),
        "stages": {
            name: {
                "status": stages[name]["status"],
                "attempts": stages[name].get("attempts", 0),
                "counts": stages[name].get("counts", {}),
                "started_at": stages[name].get("started_at"),
                "ended_at": stages[name].get("ended_at"),
                "reason": stages[name].get("reason"),
                "result": (
                    str(layout.resolve(stages[name]["result"]))
                    if stages[name].get("result")
                    else None
                ),
            }
            for name in STAGES
        },
        "counts": payload.get("counts", {}),
        "started_at": payload.get("started_at"),
        "updated_at": payload.get("updated_at"),
        "ended_at": ended,
        "duration_seconds": duration,
        "stop_reason": reason,
        "exit_code": payload.get("exit_code"),
        "run_dir": str(layout.root),
        "state": str(layout.state_path),
        "logs": {name: str(layout.log_path(name)) for name in STAGES},
        "resume": (
            f"dogpdkb sync --config {payload.get('config')} --book {book.get('key')} "
            f"--resume {payload['run_id']}"
            # A failed batch is resumable too: the runner retries its failed stage.
            if status not in {"completed", "partial"}
            else None
        ),
    }


def _latest(layouts: list[RunLayout]) -> RunLayout | None:
    running: list[tuple[str, RunLayout]] = []
    ended: list[tuple[str, RunLayout]] = []
    for layout in layouts:
        try:
            payload = read_json(layout.state_path)
        except RunError:
            continue
        status, _ = observed_status(payload)
        row = (str(payload.get("started_at") or ""), layout)
        (running if status == "running" else ended).append(row)
    pool = running or ended
    return max(pool, key=lambda row: row[0])[1] if pool else None


def report(data: dict[str, Any]) -> str:
    counts = data["counts"]
    stages = " ".join(f"{name}={data['stages'][name]['status']}" for name in STAGES)
    lines = [
        f"run {data['run']} book={data['book'].get('key')} status={data['status']}",
        f"current stage: {data['current_stage'] or 'none'}",
        f"stages: {stages}",
        (
            f"items: total={counts.get('total', 0)} "
            f"completed={counts.get('completed', 0)} "
            f"reused={counts.get('reused', 0)} failed={counts.get('failed', 0)}"
        ),
        f"started: {data['started_at']}  last progress: {data['updated_at']}",
        f"elapsed: {data['duration_seconds']}s",
        f"stop reason: {data['stop_reason'] or 'none'}",
        f"run directory: {data['run_dir']}",
        f"logs: {data['run_dir']}/logs",
    ]
    if data["resume"]:
        lines.append(f"resume: {data['resume']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(arguments)
    config = configure(parser, args.config)
    try:
        if args.run:
            layouts = [find_run(config.staging, args.run, args.book)]
        else:
            layouts = book_runs(config.staging, args.book)
    except RunError as error:
        parser.exit(1, f"dogpdkb status: {safe_diagnostic(error)}\n")
    selected = layouts[0] if args.run else _latest(layouts)
    if selected is None:
        empty = {"schema_version": RUN_SCHEMA, "status": "no-runs", "runs": 0}
        print(
            json.dumps(empty, ensure_ascii=False)
            if args.json
            else "no sync runs recorded"
        )
        return 0
    try:
        payload = read_json(selected.state_path)
        if payload.get("schema_version") != RUN_SCHEMA:
            raise RunError(f"unsupported run state schema in {selected.state_path}")
        result = view(selected, payload)
    except (RunError, KeyError, TypeError) as error:
        parser.exit(1, f"dogpdkb status: {safe_diagnostic(error)}\n")
    print(json.dumps(result, ensure_ascii=False) if args.json else report(result))
    return 0
