"""Public commands for serial knowledge synchronization and independent Web publication."""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

from dogpdkb.help import HelpParser
from dogpdkb.instance_lock import locked_main

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
    code = _dispatch(argv)
    if not isinstance(code, int):
        raise TypeError("dogpdkb command returned a non-integer exit status")
    return code


def _dispatch(argv: list[str] | None = None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "discover":
        from dogpdkb.discover import main as discover_main

        return discover_main(arguments[1:])
    if arguments and arguments[0] == "sync":
        from dogpdkb.sync import main as sync_main

        return sync_main(arguments[1:])
    if arguments and arguments[0] == "update-graph":
        from dogpdkb.graph import main as graph_main

        return graph_main(arguments[1:])
    if arguments and arguments[0] == "apply":
        from dogpdkb.apply import main as apply_main

        return apply_main(arguments[1:])
    if arguments and arguments[0] == "recover-export":
        from dogpdkb.export_recovery import main as recover_export_main

        return recover_export_main(arguments[1:])
    if arguments and arguments[0] == "publish-web":
        from dogpdkb.workflows.publish_web import main as web_main

        web_main(arguments[1:])
        return 0
    if arguments and arguments[0] == "status":
        from dogpdkb.observe import main as status_main

        return status_main(arguments[1:])
    if arguments and arguments[0] == "purge":
        from dogpdkb.purge import main as purge_main

        return purge_main(arguments[1:])
    parser = HelpParser(description=__doc__, prog="dogpdkb")
    try:
        version = importlib.metadata.version("dogpdkb")
    except importlib.metadata.PackageNotFoundError:
        version = "uninstalled"
    parser.add_argument("--version", action="version", version=f"dogpdkb {version}")
    commands = parser.add_subparsers(dest="command")
    for name in (
        "discover",
        "apply",
        "update-graph",
        "sync",
        "recover-export",
        "publish-web",
    ):
        commands.add_parser(name, help=f"run {name}; use {name} --help for options")
    commands.add_parser(
        "status", help="read-only sync batch status; use status --help for options"
    )
    commands.add_parser(
        "purge", help="delete selected wash runs; use purge --help for options"
    )
    doctor = commands.add_parser("doctor", help="read-only, offline preflight checks")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument("--book")
    doctor_inputs = doctor.add_mutually_exclusive_group()
    doctor_inputs.add_argument("--source", action="append", metavar="KEY")
    doctor_inputs.add_argument("--dir", type=Path, metavar="PATH")
    doctor_inputs.add_argument(
        "--package", action="store_true", help="check installed package only"
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
    try:
        args = parser.parse_args(arguments)
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    if args.command == "init-state":
        import json

        from dogpdkb.records import initialize
        from dogpdkb.settings import load_config, select_book

        try:
            config = load_config(args.config)
            book = select_book(config, args.book)
            result = initialize(config, book)
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
        from dogpdkb.preflight import diagnose, print_report

        report = diagnose(
            args.config, args.book, args.package, keys=args.source, directory=args.dir
        )
        print_report(report, json_output=args.json)
        for row in report:
            if row.get("exit_code") in {130, 143}:
                return row["exit_code"]
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
    if any(flag in arguments for flag in ("-h", "--help", "--full")) or arguments == [
        "--version"
    ]:
        return _main(arguments)
    if arguments and arguments[0] in {"update", "uninstall"}:
        return locked_main(_main)(arguments)
    from dogpdkb.lifecycle import runtime_lock

    try:
        with runtime_lock():
            return _main(arguments)
    except ValueError as error:
        print(f"dogpdkb: {error}", file=sys.stderr)
        return 1
