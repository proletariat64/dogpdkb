"""Plan auditable knowledge updates without executing the legacy sync pipeline."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import NoReturn

from dogpdkb.knowledge_plan import PlanError, run_planner


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(1, f"knowledge sync: INVALID_ARGUMENTS: {message}\n")


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(prog="dogpdkb sync --knowledge")
    parser.add_argument("--knowledge", action="store_true", required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--quality-cases", type=Path)
    parser.add_argument("--graph-dir", type=Path)
    parser.add_argument("--graphify-python", type=Path)
    args = parser.parse_args(argv)
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
