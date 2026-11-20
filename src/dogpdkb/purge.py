"""Explicit removal of selected wash runs, without changing durable facts."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
from contextlib import ExitStack
from pathlib import Path

from dogpdkb.help import HelpParser
from dogpdkb.records import RUN_SCHEMA, Store, process_alive, read_json
from dogpdkb.settings import load_config, select_book, source_identity


def purge_runs(store, reference=None, *, all_runs=False, force=False):
    root = store.runs
    if root.is_symlink():
        raise ValueError("refusing a symlink wash directory")
    root = root.resolve()
    if all_runs:
        candidates = sorted(root.glob("run-*"))
    else:
        if reference is None:
            raise ValueError("select a run to purge")
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
    saved_targets = set()
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
        # Deletion needs ownership, not the frozen execution targets for resume.
        data = read_json(path / "run.json")
        if (
            not isinstance(data, dict)
            or data.get("schema") != RUN_SCHEMA
            or data.get("run_id") != path.name
            or not isinstance(data.get("book"), dict)
            or data["book"].get("key") != store.book.key
            or not isinstance(data.get("targets"), dict)
        ):
            raise ValueError("run does not belong to the selected book wash or schema")
        bindings = data["targets"].get("sources")
        known = store.data["book"]["sources"]
        if (
            not isinstance(bindings, dict)
            or not bindings
            or any(
                key not in known
                or not isinstance(binding, dict)
                or source_identity(known[key]) != source_identity(binding)
                for key, binding in bindings.items()
            )
        ):
            raise ValueError("run does not belong to the selected book wash or schema")
        for key in ("corpus", "graph"):
            value = data["targets"].get(key)
            if not isinstance(value, str) or not value or not Path(value).is_absolute():
                raise ValueError(f"saved {key} target must be an absolute path")
            saved_targets.add(Path(value).resolve())
        status = data.get("status")
        if status == "running" and process_alive(data.get("pid")):
            raise ValueError(f"run is still running: {path.name}")
        if status not in {"completed", "partial"} and not force:
            raise ValueError(f"discarding {status} run requires --force: {path.name}")
        checked.append(target)
    if any(
        target.is_relative_to(saved) or saved.is_relative_to(target)
        for target in checked
        for saved in saved_targets
    ):
        raise ValueError("wash overlaps saved corpus/graph target")
    current_locks = {
        store.book.corpus.resolve() / ".dogpdkb.lock",
        store.settings.graph_for(store.book).resolve() / ".dogpdkb.lock",
    }
    saved_locks = {target / ".dogpdkb.lock" for target in saved_targets}
    with ExitStack() as locks:
        for path in sorted(saved_locks - current_locks):
            try:
                descriptor = os.open(
                    path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
                )
            except FileNotFoundError:
                # Do not recreate historical target trees just for deletion.
                continue
            locks.callback(os.close, descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError(
                    f"another writer is running for saved target {path.parent}"
                ) from error
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
