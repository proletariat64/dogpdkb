"""Read-only status snapshots for the CLI orchestrator."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from dogpdkb.help import HelpParser
from dogpdkb.records import (
    RUN_SCHEMA,
    Store,
    lock_held,
    now_iso,
    process_alive,
    read_json,
)
from dogpdkb.settings import load_config, select_book


def view_run(path: Path) -> dict:
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != RUN_SCHEMA:
        raise ValueError(f"unsupported run record: {path}")
    recorded = payload["status"]
    alive = bool(
        recorded == "running"
        and process_alive(payload.get("pid"))
        and payload.get("lock")
        and lock_held(Path(payload["lock"]), payload["pid"])
    )
    status = "interrupted" if recorded == "running" and not alive else recorded
    end = payload.get("ended_at") or (now_iso() if alive else payload.get("updated_at"))
    try:
        elapsed = max(
            0.0,
            (
                datetime.fromisoformat(end)
                - datetime.fromisoformat(payload["started_at"])
            ).total_seconds(),
        )
    except (TypeError, ValueError, KeyError):
        elapsed = None
    return {
        **payload,
        "schema": "dogpdkb-status-1",
        "status": status,
        "recorded_status": recorded,
        "alive": alive,
        "elapsed_seconds": elapsed,
        "run_directory": str(path.parent.resolve()),
    }


def snapshot(store: Store, reference: str | None = None) -> dict:
    if reference:
        path = Path(reference).expanduser()
        if path.name != "run.json":
            path = (
                path / "run.json" if path.is_dir() else store.runs / path / "run.json"
            )
        data = view_run(path)
        if data.get("book", {}).get("key") != store.book.key:
            raise ValueError("run does not belong to the selected book")
        return data
    views = [view_run(path) for path in store.runs.glob("run-*/run.json")]
    if not views:
        return {"schema": "dogpdkb-status-1", "status": "no-runs", "runs": 0}
    running = [view for view in views if view["alive"]]
    return max(
        running or views,
        key=lambda view: (
            view.get("started_at", ""),
            (Path(view["run_directory"]) / "run.json").stat().st_mtime_ns,
        ),
    )


def report(data: dict) -> str:
    if data["status"] == "no-runs":
        return "no sync runs recorded"
    counts = " ".join(f"{key}={value}" for key, value in data.get("counts", {}).items())
    return (
        f"run {data['run_id']} status={data['status']} stage={data.get('stage') or 'none'}\n"
        f"{counts}\nelapsed={data['elapsed_seconds']}s updated={data.get('updated_at')}\n"
        f"artifacts: {data.get('artifacts', {})}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = HelpParser(prog="dogpdkb status")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    parser.add_argument("--run")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--watch", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    try:
        config = load_config(args.config)
        book = select_book(config, args.book)
        store = Store(config, book)
        while True:
            data = snapshot(store, args.run)
            print(
                json.dumps(data, ensure_ascii=False) if args.json else report(data),
                flush=True,
            )
            if not args.watch:
                return 0
            time.sleep(1)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError) as error:
        parser.exit(1, f"dogpdkb status: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
