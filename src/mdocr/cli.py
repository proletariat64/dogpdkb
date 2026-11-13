"""mdocr command-line interface."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import NoReturn

import requests

from . import __version__
from .ocr import ocr_document
from .paddle import PaddleClient
from .prepare import (
    DEFAULT_ENABLED_EXTENSIONS,
    prepare_document,
    readable_extensions,
)


class CliError(Exception):
    """Fatal invocation error; reported before any processing starts."""


class _HelpfulParser(argparse.ArgumentParser):
    """Print the full help of the failing command on argument errors, so a
    misuse teaches the user how to use mdocr instead of a bare usage line."""

    def error(self, message: str) -> NoReturn:
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")


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


def build_parser() -> argparse.ArgumentParser:
    parser = _HelpfulParser(
        prog="mdocr",
        description="Prepare images referenced by Markdown and optionally OCR them.",
    )
    parser.add_argument("--version", action="version", version=f"mdocr {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=False)

    for name, help_text in (
        ("prepare", "copy Markdown with images normalized to local JPEG assets"),
        ("ocr", "replace eligible image embeds with marked PaddleOCR results"),
    ):
        command = sub.add_parser(name, help=help_text, description=help_text)
        command.add_argument(
            "-f",
            "--file",
            required=True,
            nargs="+",
            metavar="FILE",
            help="Markdown file(s) to process",
        )
        command.add_argument(
            "-o",
            "--output-dir",
            required=True,
            type=Path,
            metavar="DIR",
            help="directory for outputs and prepared assets",
        )
        command.add_argument(
            "--download",
            action="store_true",
            help="download remote images instead of skipping them",
        )
        command.add_argument(
            "--format",
            action="append",
            default=[],
            metavar="EXT[,EXT...]",
            help="add Pillow-readable image extension(s)",
        )
        command.add_argument(
            "--force",
            action="store_true",
            help="overwrite existing outputs and re-OCR existing mdocr blocks",
        )
    batch = sub.add_parser(
        "batch", help="Process a generic OCR manifest and publish structured outcomes"
    )
    batch.add_argument("--manifest", type=Path, required=True)
    batch.add_argument("--output-dir", type=Path, required=True)
    batch.add_argument("--report", type=Path, required=True)
    batch.add_argument("--cache-dir", type=Path)
    batch.add_argument("--download", action="store_true")
    batch.add_argument("--endpoint")
    batch.add_argument("--max-seconds", type=float, default=900)
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
    if args.command == "ocr" and not os.environ.get("PADDLEOCR_TOKEN"):
        raise CliError("missing PADDLEOCR_TOKEN environment variable")
    return files


def _enabled_extensions(values: list[str]) -> frozenset[str]:
    """Normalize and validate user-requested Pillow image extensions."""
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
    files = _validate(args)
    enabled_extensions = _enabled_extensions(args.format)
    _check_output_collisions(files, args)
    session = requests.Session()
    client = (
        PaddleClient(os.environ["PADDLEOCR_TOKEN"], session=session)
        if args.command == "ocr"
        else None
    )

    failed = False
    for md_file in files:
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"mdocr: error: {md_file}: {exc}", file=sys.stderr)
            failed = True
            continue
        try:
            if args.command == "ocr":
                if client is None:  # PADDLEOCR_TOKEN validated in _validate
                    raise CliError("missing PADDLEOCR_TOKEN environment variable")
                result = ocr_document(
                    text,
                    md_file,
                    args.output_dir,
                    client,
                    download=args.download,
                    force=args.force,
                    enabled_extensions=enabled_extensions,
                    session=session,
                )
            else:
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "batch":
        from .batch import run_batch

        try:
            return run_batch(args)
        except (OSError, ValueError, TypeError) as exc:
            print(f"mdocr: batch: {exc}", file=sys.stderr)
            return 1
    try:
        return run_command(args)
    except CliError as exc:
        print(f"mdocr: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
