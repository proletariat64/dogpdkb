"""One serial command chain shared with discover, apply and update-graph."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dogpdkb.apply import apply_run, parser_for, record_interruption
from dogpdkb.discover import DiscoverError
from dogpdkb.records import Run, Store
from dogpdkb.settings import load_config, select_book


def sync_run(settings, book, store, run, *, stop_after=None, resume=False):
    from dogpdkb.discover import discover

    differences = None if resume else discover(settings, book, store, run)
    result = apply_run(
        settings, book, store, run, differences, stop_after=stop_after, resume=resume
    )
    if result["paused"]:
        return result
    from dogpdkb.graph import run_graph

    run.data.update(status="running", ended_at=None, exit_code=None, reason=None)
    run.save()
    graph_code = run_graph(
        settings, book, store, run, manifest=Path(result["manifest"])
    )
    if graph_code in {130, 143}:
        record_interruption(store, run, graph_code)
        return {**result, "exit_code": graph_code}
    code = 1 if graph_code == 1 else 2 if 2 in {graph_code, result["exit_code"]} else 0
    run.finish("failed" if code == 1 else "partial" if code == 2 else "completed", code)
    return {**result, "exit_code": code}


def main(argv=None):
    parser = parser_for("sync")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    run = None
    try:
        settings = load_config(args.config)
        book = select_book(settings, args.book)
        store = Store(settings, book)
        with store.lock():
            run = (
                Run.load(store, args.resume)
                if args.resume
                else Run.create(store, "sync")
            )
            run.data.update(
                status="running",
                pid=os.getpid(),
                command="sync",
                ended_at=None,
                exit_code=None,
                reason=None,
            )
            try:
                result = sync_run(
                    settings,
                    book,
                    store,
                    run,
                    stop_after=args.stop_after,
                    resume=bool(args.resume),
                )
            except InterruptedError as error:
                record_interruption(store, run, error.args[0])
                return error.args[0]
            print(json.dumps({**result, "run": str(run.path)}, ensure_ascii=False))
            return result["exit_code"]
    except DiscoverError as error:
        code = error.exit_code
        if run:
            run.finish(
                "interrupted" if code in {130, 143} else "failed", code, str(error)
            )
        print(f"dogpdkb sync: {error}", file=sys.stderr)
        return code
    except (OSError, ValueError, KeyError, TypeError) as error:
        if run:
            run.finish("failed", 1, str(error))
        print(f"dogpdkb sync: {error}", file=sys.stderr)
        return 1
