"""Installed entry points for the complete knowledge synchronization platform."""

from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in WORKFLOWS:
        script = Path(__file__).resolve().parent / "workflows" / WORKFLOWS[arguments[0]]
        return subprocess.call([sys.executable, str(script), *arguments[1:]])
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
    args = parser.parse_args(arguments)
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
