"""Plan auditable knowledge updates without executing the legacy sync pipeline."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import NoReturn

from dogpdkb.knowledge_plan import PlanError, _load_snapshot, _load_state, run_planner
from dogpdkb.sync_progress import Progress
from dogpdkb.sync_state import (
    StateError,
    initialize_state,
    load_state,
    state_path,
)
from mdocr.config import cli_path, config_arguments, configure, configured_book


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(1, f"knowledge sync: INVALID_ARGUMENTS: {message}\n")


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(prog="dogpdkb sync --knowledge")
    parser.add_argument("--knowledge", action="store_true", required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    config_arguments(parser, book=True)
    parser.add_argument("--input", type=cli_path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--quality-cases", type=Path)
    parser.add_argument("--graph-dir", type=Path)
    parser.add_argument("--graphify-python", type=Path)
    args = parser.parse_args(argv)
    if args.config or args.book or args.input or args.state is None:
        config = configure(parser, args.config)
        source = configured_book(parser, config, args)
        corpus = args.input or source.corpus
        target = state_path(corpus)
        try:
            if not target.exists():
                initialize_state(
                    corpus,
                    args.graph_dir or config.graph_dir or corpus / "graphify-out",
                    source.key,
                    url=source.url,
                    name=source.name,
                )
            observed = _load_snapshot(args.snapshot.expanduser().resolve())
            Progress(
                corpus,
                source.key,
                url=source.url,
                name=source.name,
                book_id=observed["book"]["book_id"],
            ).claim(
                args.staging,
                phase="execute" if args.execute else "compare",
                parent=args.snapshot,
            )
            if (
                args.state
                and args.state.resolve() != target.resolve()
                and args.state.exists()
            ):
                legacy = json.loads(args.state.read_text())
                if legacy.get("schema_version") == "knowledge-state-migrated-1.0":
                    if Path(legacy["target"]).resolve() != target.resolve():
                        raise StateError("legacy state migrated to another corpus")
                elif legacy.get("schema_version") == "knowledge-state-1.0":
                    legacy = _load_state(args.state, observed["book"])
                    root = load_state(corpus, source.key)
                    entry = root["books"][source.key]
                    entry["book_id"] = legacy["book"]["book_id"]

                    def rebase(value):
                        if isinstance(value, list):
                            return [rebase(item) for item in value]
                        if not isinstance(value, dict):
                            return value
                        result = {key: rebase(item) for key, item in value.items()}
                        if result.get("stage") in {
                            "raw_export",
                            "historical_cleaned",
                        } and isinstance(result.get("path"), str):
                            reference = Path(result["path"])
                            if not reference.is_absolute():
                                result["path"] = str(
                                    (args.state.parent / reference).resolve()
                                )
                        return result

                    for doc_id, row in legacy["documents"].items():
                        current = entry["documents"].get(doc_id) or {}
                        if current.get("graph") and current["graph"] != row.get(
                            "graph"
                        ):
                            raise StateError(
                                "legacy import would replace an existing knowledge state"
                            )
                        entry["documents"].setdefault(doc_id, {}).update(rebase(row))
                    from mdcompare.io import publish_reports

                    publish_reports(
                        [
                            (
                                target,
                                json.dumps(root, ensure_ascii=False, indent=2) + "\n",
                            ),
                            (
                                args.state,
                                json.dumps(
                                    {
                                        "schema_version": "knowledge-state-migrated-1.0",
                                        "target": str(target.resolve()),
                                    }
                                )
                                + "\n",
                            ),
                        ]
                    )
                else:
                    raise StateError("cannot import unsupported legacy state")
            args.state = target
        except (OSError, ValueError) as error:
            parser.exit(1, f"knowledge sync: {error}\n")
    elif args.state.exists():
        marker = json.loads(args.state.read_text())
        if marker.get("schema_version") == "knowledge-state-migrated-1.0":
            args.state = Path(marker["target"])
    if not args.execute and (args.approvals or args.quality_cases):
        parser.error("--approvals and --quality-cases require --execute")
    if args.execute:
        if not args.approvals or not args.quality_cases or not args.graph_dir:
            parser.error(
                "--execute requires --approvals, --quality-cases and --graph-dir"
            )
        try:
            module = importlib.import_module("dogpdkb.knowledge_execute")
        except ImportError:
            parser.exit(1, "knowledge sync: approved execution is not installed\n")
        return module.run_execution(args)
    try:
        run_planner(args)
    except (OSError, PlanError, ValueError) as error:
        parser.exit(1, f"knowledge sync: {error}\n")
    return 0
