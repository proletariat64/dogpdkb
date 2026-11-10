"""Installed entry points for the complete knowledge synchronization platform."""

from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
from pathlib import Path

from dogpdkb.instance_lock import locked_main, subprocess_lock_options

WORKFLOWS = {
    "sync": "sync_knowledge_base.py",
    "discover": "get_sync_updates.py",
    "apply": "apply_sync_updates.py",
    "update-graph": "update_graph.py",
    "publish-web": "publish_web.py",
}
RESOURCES = Path(__file__).resolve().parent / "resources"


def init_config(directory: Path) -> None:
    """Create portable examples without overwriting any existing user file."""
    directory = directory.expanduser().resolve()
    names = ("dogpdkb.toml", "books.json")
    if any(
        (directory / name).exists() or (directory / name).is_symlink() for name in names
    ):
        raise FileExistsError("configuration already exists; no files were overwritten")
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name in names:
            with (directory / name).open("xb") as stream:
                created.append(directory / name)
                stream.write((RESOURCES / name).read_bytes())
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def _main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in WORKFLOWS:
        script = Path(__file__).resolve().parent / "workflows" / WORKFLOWS[arguments[0]]
        return subprocess.call(
            [sys.executable, str(script), *arguments[1:]], **subprocess_lock_options()
        )
    parser = argparse.ArgumentParser(description=__doc__, prog="dogpdkb")
    try:
        version = importlib.metadata.version("dogpdkb")
    except importlib.metadata.PackageNotFoundError:
        version = "uninstalled"
    parser.add_argument("--version", action="version", version=f"dogpdkb {version}")
    commands = parser.add_subparsers(dest="command")
    for name in WORKFLOWS:
        commands.add_parser(name, help=f"run {name}; use {name} --help for options")
    doctor = commands.add_parser("doctor", help="read-only, offline preflight checks")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument("--book")
    doctor.add_argument(
        "--package", action="store_true", help="check installed package only"
    )
    doctor.add_argument(
        "--require-ocr", action="store_true", help="require an OCR token"
    )
    doctor.add_argument("--json", action="store_true", help="machine-readable report")
    initialize = commands.add_parser(
        "init-config", help="create portable example configuration"
    )
    initialize.add_argument("-o", "--output", type=Path, default=Path.cwd())
    initialize_state = commands.add_parser(
        "init-state", help="register an existing corpus and graph without extraction"
    )
    initialize_state.add_argument("--config", type=Path)
    initialize_state.add_argument("--book")
    initialize_state.add_argument("-i", "--input", type=Path)
    initialize_state.add_argument("--graph-dir", type=Path)
    upgrade = commands.add_parser(
        "update", help="install a specified Release; retain configuration and data"
    )
    upgrade.add_argument("--version", required=True, help="MAJOR.MINOR.PATCH")
    remove = commands.add_parser(
        "uninstall", help="remove managed program; retain configuration and data"
    )
    remove.add_argument(
        "--yes", action="store_true", help="confirm noninteractive removal"
    )
    args = parser.parse_args(arguments)
    if args.command == "init-state":
        import json

        from dogpdkb.sync_state import initialize_state
        from mdocr.config import load_config, select_book

        try:
            config = load_config(args.config)
            book = select_book(config, args.book)
            corpus = (args.input or book.corpus).expanduser().resolve()
            graph = (
                (args.graph_dir or config.graph_dir or corpus / "graphify-out")
                .expanduser()
                .resolve()
            )
            result = initialize_state(
                corpus, graph, book.key, url=book.url, name=book.name
            )
        except (OSError, ValueError) as error:
            parser.exit(1, f"dogpdkb init-state: {error}\n")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command in ("update", "uninstall"):
        from dogpdkb import lifecycle

        try:
            return (
                lifecycle.update(args.version)
                if args.command == "update"
                else lifecycle.uninstall(args.yes)
            )
        except (OSError, ValueError) as error:
            parser.exit(1, f"dogpdkb {args.command}: {error}\n")
    if args.command == "doctor":
        from dogpdkb.doctor import diagnose, print_report

        report = diagnose(args.config, args.book, args.package, args.require_ocr)
        print_report(report, json_output=args.json)
        return 1 if any(row["status"] == "fail" for row in report) else 0
    if args.command == "init-config":
        try:
            init_config(args.output)
        except OSError as error:
            parser.exit(1, f"dogpdkb init-config: {error}\n")
        print(
            "Created dogpdkb.toml and books.json; edit gateway/source settings before syncing."
        )
        return 0
    parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {*WORKFLOWS, "init-state", "update", "uninstall"}:
        return locked_main(_main)(arguments)
    return _main(arguments)
