"""mdocr command-line interface."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

import requests

from . import __version__
from .markdown import render_image, transform_images
from .ocr import ocr_transform
from .paddle import PaddleClient
from .prepare import prepare_or_report


class CliError(Exception):
    """Fatal invocation error; reported before any processing starts."""


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def prepare_transform(
    text: str,
    md_file: Path,
    output_dir: Path,
    *,
    download: bool,
    session: requests.Session | None = None,
) -> tuple[str, list[str], list[str]]:
    """Return ``(new_markdown, warnings, errors)`` for one Markdown document."""
    errors: list[str] = []
    warnings: list[str] = []

    def transform(ref):
        prepared = prepare_or_report(
            ref,
            md_file,
            output_dir,
            download=download,
            session=session,
            warnings=warnings,
            errors=errors,
        )
        if prepared is None:
            return None
        return render_image(ref.alt, prepared.asset_rel, ref.title)

    return transform_images(text, transform, errors=errors, warnings=warnings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdocr",
        description="Prepare images referenced by Markdown and optionally OCR them.",
    )
    parser.add_argument("--version", action="version", version=f"mdocr {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

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
            "--force",
            action="store_true",
            help="overwrite existing outputs and re-OCR existing mdocr blocks",
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
    if args.command == "ocr" and not os.environ.get("PADDLEOCR_TOKEN"):
        raise CliError("missing PADDLEOCR_TOKEN environment variable")
    return files


def _check_output_collisions(files: list[Path], args: argparse.Namespace) -> None:
    collisions = [
        str(args.output_dir / f.name)
        for f in files
        if (args.output_dir / f.name).is_file()
    ]
    if collisions and not args.force:
        raise CliError(
            "output file exists (use --force to overwrite): " + ", ".join(collisions)
        )


def run_command(args: argparse.Namespace) -> int:
    files = _validate(args)
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
                assert client is not None  # PADDLEOCR_TOKEN validated in _validate
                new_text, warnings, errors = ocr_transform(
                    text,
                    md_file,
                    args.output_dir,
                    client,
                    download=args.download,
                    force=args.force,
                    session=session,
                )
            else:
                new_text, warnings, errors = prepare_transform(
                    text,
                    md_file,
                    args.output_dir,
                    download=args.download,
                    session=session,
                )
            output = args.output_dir / md_file.name
            # Re-check at write time: a file created after the upfront collision
            # check must not be silently overwritten without --force.
            if output.exists() and not args.force:
                raise CliError(
                    f"output file exists (use --force to overwrite): {output}"
                )
            atomic_write(output, new_text)
        except CliError as exc:
            print(f"mdocr: error: {md_file}: {exc}", file=sys.stderr)
            failed = True
            continue
        except Exception as exc:  # noqa: BLE001 - unexpected file-level failure
            print(f"mdocr: error: {md_file}: {exc}", file=sys.stderr)
            failed = True
            continue
        for warning in warnings:
            print(f"mdocr: warning: {md_file.name}: {warning}", file=sys.stderr)
        for error in errors:
            print(f"mdocr: error: {md_file.name}: {error}", file=sys.stderr)
        if errors:
            failed = True
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return run_command(args)
    except CliError as exc:
        print(f"mdocr: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
