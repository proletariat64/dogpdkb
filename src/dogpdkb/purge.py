"""Explicit removal of selected wash runs, without changing durable facts."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from dogpdkb.help import HelpParser
from dogpdkb.records import Run, Store, process_alive
from dogpdkb.settings import load_config, select_book


def purge_runs(store, reference=None, *, all_runs=False, force=False):
    root = store.runs
    if root.is_symlink():
        raise ValueError("refusing a symlink wash directory")
    root = root.resolve()
    if all_runs:
        candidates = sorted(root.glob("run-*"))
    else:
        path = Path(reference).expanduser()
        if path.name == "run.json":
            path = path.parent
        if ".." in path.parts:
            raise ValueError("run must be directly inside the selected book wash")
        candidates = [root / path if len(path.parts) == 1 else path.absolute()]
    protected = [
        store.settings.state,
        store.book.corpus,
        store.settings.graph_for(store.book),
        store.settings.ocr_cache,
    ]
    checked = []
    for path in candidates:
        if (
            path.is_symlink()
            or path.parent.resolve() != root
            or not path.name.startswith("run-")
            or not path.is_dir()
        ):
            raise ValueError(f"not a selected book wash run: {path}")
        target = path.resolve()
        if any(
            target.is_relative_to(p.resolve()) or p.resolve().is_relative_to(target)
            for p in protected
        ):
            raise ValueError(f"wash overlaps protected data: {target}")
        if (path / "run.json").is_symlink():
            raise ValueError("refusing symlink run metadata")
        run = Run.load(store, path)
        status = run.data.get("status")
        if status == "running" and process_alive(run.data.get("pid")):
            raise ValueError(f"run is still running: {run.id}")
        if status not in {"completed", "partial"} and not force:
            raise ValueError(f"discarding {status} run requires --force: {run.id}")
        checked.append(target)
    for path in checked:
        shutil.rmtree(path)
    return {"book": store.book.key, "purged": [str(path) for path in checked]}


def main(argv=None):
    parser = HelpParser(description=__doc__, prog="dogpdkb purge")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--run", help="run ID or path within the selected book wash")
    target.add_argument(
        "--all", action="store_true", help="all selected book wash runs"
    )
    parser.add_argument("--force", action="store_true", help="discard unfinished runs")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    try:
        settings = load_config(args.config)
        book = select_book(settings, args.book)
        store = Store(settings, book)
        with store.lock():
            result = purge_runs(store, args.run, all_runs=args.all, force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"dogpdkb purge: {error}", file=sys.stderr)
        return 1
