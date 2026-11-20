"""mdocr command-line interface."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import NoReturn

from . import __version__
from . import help as contracts


class CliError(Exception):
    """Fatal invocation error; reported before any processing starts."""


class _HelpfulParser(argparse.ArgumentParser):
    """Print the full help of the failing command on argument errors, so a
    misuse teaches the user how to use mdocr instead of a bare usage line."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        self.print_help(sys.stderr)
        self.exit(
            2,
            f"\n{self.prog}: error: {message}\nTry '{self.prog} --help' for usage.\n",
        )


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary basename short even when path.name is near NAME_MAX.
    tmp = path.with_name(f".mdocr-{uuid.uuid4().hex}.tmp")
    stream = tmp.open("x", encoding="utf-8")
    try:
        with stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def build_parser(*, full: bool = False) -> argparse.ArgumentParser:
    parser = _HelpfulParser(
        prog="mdocr",
        description="Plan and safely process Markdown image OCR.",
        epilog=contracts.ROOT + ("\n" + contracts.ROOT_FULL if full else ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"mdocr {__version__}")
    parser.add_argument(
        "--full",
        action="store_true",
        help="with -h/--help, show the full local contract",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=False)

    doctor = sub.add_parser(
        "doctor",
        help="check the local OCR environment",
        epilog=contracts.DOCTOR + ("\n" + contracts.DOCTOR_FULL if full else ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    doctor.add_argument(
        "--mode", choices=("prepare", "paddle", "llm"), default="paddle"
    )
    doctor.add_argument("--model")
    doctor.add_argument("--effort", choices=("low", "medium", "high", "xhigh"))
    doctor.add_argument(
        "--json", action="store_true", help="emit a machine-readable report"
    )

    plan = sub.add_parser(
        "plan",
        help="create an immutable, inference-free conversion plan",
        epilog=contracts.PLAN + ("\n" + contracts.PLAN_FULL if full else ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    plan.add_argument("--mode", choices=("paddle", "llm"), required=True)
    shape = plan.add_mutually_exclusive_group(required=True)
    shape.add_argument("--input", help="one Markdown file")
    shape.add_argument("--list", help="JSON array of Markdown paths")
    shape.add_argument("--directory", help="recursive Markdown directory")
    plan.add_argument("--output", help="output Markdown file for --input")
    plan.add_argument("--output-dir", help="output directory for --list/--directory")
    plan.add_argument("--plan", required=True, help="immutable plan JSON destination")
    plan.add_argument("--model")
    plan.add_argument("--effort")
    plan.add_argument("--prompt-file")
    plan.add_argument("--token-budget", type=int)
    plan.add_argument("--request-budget", type=int)
    plan.add_argument(
        "--fix", type=int, default=0, help="batch-end retries per failed image"
    )
    plan.add_argument("--concurrency", type=int, choices=range(1, 11), default=3)
    plan.add_argument("--max-seconds", type=float, default=900)
    plan.add_argument("--endpoint")
    plan.add_argument("--download", action="store_true")
    plan.add_argument("--download-list", help="restrict --download to listed inputs")
    plan.add_argument("--selection")
    plan.add_argument("--cache-dir")

    run = sub.add_parser(
        "run",
        help="run or resume a frozen plan",
        epilog=contracts.RUN + ("\n" + contracts.RUN_FULL if full else ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run.add_argument("--plan", required=True)
    run.add_argument("--result", required=True)
    run.add_argument("--retry-failed", action="store_true")

    command = sub.add_parser(
        "prepare",
        help="copy Markdown with local Prepared image JPEGs; no model calls",
        epilog=contracts.PREPARE + ("\n" + contracts.PREPARE_FULL if full else ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    command.add_argument(
        "--full",
        action="store_true",
        help="with -h/--help, show the full local contract",
    )
    command.add_argument(
        "-f",
        "--file",
        required=True,
        nargs="+",
        metavar="FILE",
        help="required Markdown file(s); paths relative to cwd; basenames must be unique",
    )
    command.add_argument(
        "-o",
        "--output-dir",
        required=True,
        type=Path,
        metavar="DIR",
        help="required directory for output Markdown and Prepared image JPEGs; relative to cwd",
    )
    command.add_argument(
        "--download",
        action="store_true",
        help="download eligible HTTP(S) images; default: retain remote links with warnings",
    )
    command.add_argument(
        "--format",
        action="append",
        default=[],
        metavar="EXT[,EXT...]",
        help="add Pillow-readable extensions to PNG/WebP/GIF/JPEG/TIFF defaults; repeatable",
    )
    command.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing output Markdown; never submits OCR jobs",
    )
    return parser


def _validate(args: argparse.Namespace) -> list[Path]:
    files = [Path(f) for f in args.file]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise CliError("input file not found: " + ", ".join(missing))
    basenames = [f.name for f in files]
    duplicates = sorted({name for name in basenames if basenames.count(name) > 1})
    if duplicates:
        raise CliError(
            "duplicate input basenames would collide: " + ", ".join(duplicates)
        )
    return files


def _enabled_extensions(values: list[str]) -> frozenset[str]:
    """Normalize and validate user-requested Pillow image extensions."""
    from .prepare import DEFAULT_ENABLED_EXTENSIONS, readable_extensions

    requested: set[str] = set()
    for value in values:
        for item in value.split(","):
            normalized = item.strip().lower().lstrip(".")
            if not normalized:
                raise CliError(f"invalid image format extension: {item!r}")
            requested.add(f".{normalized}")

    unavailable = sorted(requested - readable_extensions())
    if unavailable:
        raise CliError(
            "image format extension not readable by Pillow: " + ", ".join(unavailable)
        )
    return DEFAULT_ENABLED_EXTENSIONS | frozenset(requested)


def _output_path_is_overwritable(path: Path, *, force: bool) -> bool:
    """Return whether mdocr may write ``path`` without replacing non-files."""
    if not path.exists() and not path.is_symlink():
        return True
    return force and path.is_file() and not path.is_symlink()


def _check_output_collisions(files: list[Path], args: argparse.Namespace) -> None:
    collisions = [
        str(args.output_dir / file.name)
        for file in files
        if not _output_path_is_overwritable(
            args.output_dir / file.name, force=args.force
        )
    ]
    if not collisions:
        return
    if args.force:
        raise CliError(
            "output path is not a regular file and cannot be overwritten: "
            + ", ".join(collisions)
        )
    raise CliError(
        "output file exists (use --force to overwrite): " + ", ".join(collisions)
    )


def run_command(args: argparse.Namespace) -> int:
    import requests

    from .prepare import prepare_document

    files = _validate(args)
    enabled_extensions = _enabled_extensions(args.format)
    _check_output_collisions(files, args)
    session = requests.Session()

    failed = False
    for md_file in files:
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"mdocr: error: {md_file}: {exc}", file=sys.stderr)
            failed = True
            continue
        try:
            result = prepare_document(
                text,
                md_file,
                args.output_dir,
                download=args.download,
                enabled_extensions=enabled_extensions,
                session=session,
            )
            output = args.output_dir / md_file.name
            # Re-check at write time: a file created after the upfront collision
            # check must not be silently overwritten without --force.
            if output.exists() and not args.force:
                raise CliError(
                    f"output file exists (use --force to overwrite): {output}"
                )
            atomic_write(output, result.markdown)
        except CliError as exc:
            print(f"mdocr: error: {md_file}: {exc}", file=sys.stderr)
            failed = True
            continue
        except Exception as exc:  # noqa: BLE001 - unexpected file-level failure
            print(f"mdocr: error: {md_file}: {exc}", file=sys.stderr)
            failed = True
            continue
        for warning in result.warnings:
            print(f"mdocr: warning: {md_file.name}: {warning}", file=sys.stderr)
        for error in result.errors:
            print(f"mdocr: error: {md_file.name}: {error}", file=sys.stderr)
        if result.errors:
            failed = True
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    option_end = arguments.index("--") if "--" in arguments else len(arguments)
    options = arguments[:option_end]
    full = "--full" in options
    parser = build_parser(full=full)
    if full and not any(flag in options for flag in ("-h", "--help")):
        parser.error("--full requires -h or --help")
    args = parser.parse_args(
        [arg for arg in options if arg != "--full"] + arguments[option_end:]
    )
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "doctor":
        from .doctor import check
        from .llm import DEFAULT_EFFORT, DEFAULT_MODEL

        if args.mode != "llm" and (args.model is not None or args.effort is not None):
            parser.error("--model and --effort require --mode llm")
        args.model = DEFAULT_MODEL if args.model is None else args.model
        args.effort = args.effort or DEFAULT_EFFORT
        return check(args)
    if args.command == "plan":
        from .plan import plan_command

        try:
            return plan_command(args)
        except (OSError, ValueError, TypeError) as exc:
            print(f"mdocr: plan: {exc}", file=sys.stderr)
            return 1
    if args.command == "run":
        from .runner import run_command as execute_plan

        try:
            return execute_plan(args)
        except (OSError, ValueError, TypeError) as exc:
            print(f"mdocr: run: {exc}", file=sys.stderr)
            return 1
    try:
        return run_command(args)
    except CliError as exc:
        print(f"mdocr: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
