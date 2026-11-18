"""Command-line entrypoint; all input and downstream state remain read-only."""

import argparse
import json
import sys
from collections.abc import Iterable
from typing import TypeVar, cast

_N = TypeVar("_N", bound=argparse.Namespace)
from pathlib import Path

from . import __version__
from .catalog import catalog_manifest
from .comparison import compare_documents
from .help import NORMAL, full_contract
from .io import (
    InputError,
    InputReader,
    publish_reports,
    referenced_paths,
    validate_outputs,
)
from .knowledge import compare_knowledge_documents, render_knowledge_report
from .manifest import ManifestError, load_manifest
from .model import CompareOptions
from .semantic_io import publish_semantic
from .semantic_route import publish_semantic_route


class ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)
        self.full_help = False
        self.add_argument(
            "--full",
            action="store_true",
            help="show the complete local contract with -h/--help",
        )

    def parse_known_args(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, args: Iterable[str] | None = None, namespace: _N | None = None
    ) -> tuple[_N, list[str]]:
        arguments = list(sys.argv[1:] if args is None else args)
        self.full_help = "--full" in arguments
        if self.full_help and self.prog == "mdcompare":
            command = next(
                (arg for arg in arguments if arg in NORMAL and arg != "mdcompare"),
                None,
            )
            if command and arguments.index("--full") < arguments.index(command):
                self.error(
                    f"place --full after the command: mdcompare {command} --help --full"
                )
        if self.full_help and not any(flag in arguments for flag in ("-h", "--help")):
            self.error("--full requires -h or --help")
        parsed, remaining = super().parse_known_args(arguments, namespace)
        return cast(_N, parsed), remaining

    def format_help(self):
        text = super().format_help()
        if self.full_help:
            text += full_contract(self.prog.split()[-1])
        return text

    def error(self, message):
        self.exit(
            1,
            f"{self.prog}: INVALID_ARGUMENTS: {message}\n"
            f"See {self.prog} --help for usage.\n",
        )


def nonnegative_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected nonnegative integer") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("expected nonnegative integer")
    return result


def _positive_file_limit(value: str) -> int:
    result = nonnegative_int(value)
    if result == 0:
        raise argparse.ArgumentTypeError("File limit must be positive")
    return result


def main(argv=None) -> int:
    parser = ArgumentParser(
        prog="mdcompare",
        description="Compare captured Markdown offline or catalog its resource evidence.",
        epilog=NORMAL["mdcompare"],
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    compare = sub.add_parser(
        "compare",
        help="Strict file and processing-evidence comparison",
        description="Compare text, resources, metadata and captured OCR evidence without processing them.",
        epilog=NORMAL["compare"],
    )
    compare.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="baseline JSON manifest of immutable local snapshots",
    )
    compare.add_argument(
        "--incoming",
        type=Path,
        required=True,
        help="incoming JSON manifest using the same scoped document identities",
    )
    compare.add_argument(
        "--output",
        type=Path,
        required=True,
        help="authoritative JSON report path; parent must exist",
    )
    compare.add_argument(
        "--report", type=Path, help="Optional private human-readable report"
    )
    compare.add_argument(
        "--include-evidence",
        action="store_true",
        help="Include bounded body evidence in reports",
    )
    compare.add_argument(
        "--max-file-bytes",
        type=_positive_file_limit,
        default=16 * 1024 * 1024,
        help="Maximum bytes per document/evidence file (default: 16777216); oversized inputs stay UNKNOWN",
    )
    compare.add_argument(
        "--normalize-line-endings",
        action="store_true",
        help="Opt into CRLF/lone CR to LF (rule version 1); default: preserve bytes",
    )
    compare.add_argument(
        "--max-diff-work",
        type=nonnegative_int,
        default=CompareOptions.max_diff_work,
        help="Maximum Myers frontier and matching steps (default: 2000000)",
    )
    compare.add_argument(
        "--max-evidence-chars",
        type=nonnegative_int,
        default=CompareOptions.max_evidence_chars,
        help="Maximum body characters across text evidence (default: 8000)",
    )
    compare.add_argument(
        "--max-char-region",
        type=nonnegative_int,
        default=CompareOptions.max_char_region,
        help="Maximum combined region length for character explanation (default: 4096)",
    )
    knowledge = sub.add_parser(
        "knowledge",
        help="Compare body knowledge; ignore images and OCR",
        description=(
            "Report reusable, added, revised and unresolved body knowledge. "
            "Images and marked OCR blocks are ignored; no image/OCR tasks are created."
        ),
        epilog=NORMAL["knowledge"],
    )
    knowledge.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="baseline JSON manifest of captured Markdown bodies",
    )
    knowledge.add_argument(
        "--incoming",
        type=Path,
        required=True,
        help="incoming JSON manifest; image/OCR evidence is ignored",
    )
    knowledge.add_argument(
        "--output",
        type=Path,
        required=True,
        help="authoritative knowledge-1.0 JSON report path; parent must exist",
    )
    knowledge.add_argument(
        "--report", type=Path, help="Optional private human-readable knowledge report"
    )
    knowledge.add_argument(
        "--include-evidence", action="store_true", help="Include bounded body evidence"
    )
    knowledge.add_argument(
        "--max-file-bytes",
        type=_positive_file_limit,
        default=CompareOptions.max_file_bytes,
        help="Maximum bytes per Markdown file (default: 16777216); oversized inputs stay UNKNOWN",
    )
    knowledge.add_argument(
        "--max-diff-work",
        type=nonnegative_int,
        default=CompareOptions.max_diff_work,
        help="Maximum fuzzy alignment work (default: 2000000); exact reuse is still evaluated",
    )
    knowledge.add_argument(
        "--max-evidence-chars",
        type=nonnegative_int,
        default=CompareOptions.max_evidence_chars,
        help="Maximum body evidence characters per document (default: 8000)",
    )
    semantic = sub.add_parser(
        "semantic",
        help="Route authored-body changes for compilation",
        description="Offline authored-text routing with complete durable diff evidence.",
        epilog=NORMAL["semantic"],
    )
    for name, help_text in (
        ("baseline", "baseline descriptor manifest"),
        ("incoming", "incoming descriptor manifest"),
        ("output", "semantic-1.0 report path"),
        ("diff-output", "complete mdcompare-semantic-diff-1 path"),
    ):
        semantic.add_argument("--" + name, type=Path, required=True, help=help_text)
    semantic.add_argument(
        "--recovery", type=Path, help="optional body recovery manifest"
    )
    semantic.add_argument(
        "--max-file-bytes",
        type=_positive_file_limit,
        default=CompareOptions.max_file_bytes,
    )
    semantic.add_argument(
        "--max-diff-work", type=nonnegative_int, default=CompareOptions.max_diff_work
    )
    route_parser = sub.add_parser(
        "semantic-route", help="Route changed documents from bound offline scores",
        description="Validate score bindings and publish a distinct score-route report.",
        epilog=NORMAL["semantic-route"],
    )
    for name in ("report", "diff-index", "scores", "output"):
        route_parser.add_argument("--" + name, type=Path, required=True)
    route_parser.add_argument("--compile-threshold", type=int, choices=range(1, 6), default=3)
    route_parser.add_argument("--skip-ambiguity-intercept", choices=("yes", "no"), default="no")
    catalog = sub.add_parser(
        "catalog",
        help="Build an offline resource manifest from Markdown",
        description="Read a JSON descriptor manifest and inventory local Markdown dependencies without downloading.",
        epilog=NORMAL["catalog"],
    )
    catalog.add_argument(
        "--input",
        type=Path,
        required=True,
        help="schema_version 1.0 JSON descriptor manifest; resources optional",
    )
    catalog.add_argument(
        "--output",
        type=Path,
        required=True,
        help="catalog JSON manifest path; missing parents are created",
    )
    args = parser.parse_args(argv)
    if args.command == "catalog":
        try:
            catalog_manifest(args.input, args.output)
        except (InputError, ManifestError, OSError, ValueError) as exc:
            print(f"mdcompare: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "semantic-route":
        try:
            publish_semantic_route(args)
        except InputError as exc:
            print(f"mdcompare: {getattr(exc, 'reason_code', str(exc))}", file=sys.stderr)
            return 1
        except OSError:
            print("mdcompare: REPORT_WRITE_FAILED", file=sys.stderr)
            return 1
        return 0
    if args.command == "semantic":
        try:
            report = publish_semantic(args)
        except (InputError, ManifestError) as exc:
            print(
                f"mdcompare: {getattr(exc, 'reason_code', str(exc))}", file=sys.stderr
            )
            return 1
        except OSError:
            print("mdcompare: REPORT_WRITE_FAILED", file=sys.stderr)
            return 1
        return 2 if any(row["action"] == "DEFER" for row in report["results"]) else 0
    options = CompareOptions(
        include_evidence=args.include_evidence,
        max_file_bytes=args.max_file_bytes,
        normalize_line_endings=getattr(args, "normalize_line_endings", False),
        max_diff_work=args.max_diff_work,
        max_evidence_chars=args.max_evidence_chars,
        max_char_region=getattr(
            args, "max_char_region", CompareOptions.max_char_region
        ),
    )
    try:
        reader = InputReader(options.max_file_bytes)
        if args.command == "knowledge":
            baseline = load_manifest(args.baseline, reader, body_only=True)
            incoming = load_manifest(args.incoming, reader, body_only=True)
            report = compare_knowledge_documents(baseline, incoming, options, reader)
        else:
            baseline = load_manifest(args.baseline, reader)
            incoming = load_manifest(args.incoming, reader)
            report = compare_documents(baseline, incoming, options, reader)
        if reader.changed_paths() & {
            args.baseline.absolute(),
            args.incoming.absolute(),
        }:
            raise ManifestError("MANIFEST_CHANGED")
        protected = reader.paths | {args.baseline, args.incoming}
        for document in baseline + incoming:
            protected.update(referenced_paths(document))
        outputs = [p for p in (args.output, args.report) if p is not None]
        validate_outputs(outputs, protected)
        reports = []
        if args.report and args.command == "knowledge":
            reports.append((args.report, render_knowledge_report(report)))
        elif args.report:
            lines = [
                "# mdcompare report",
                "",
                f"Documents: {report['summary']['total']}",
                "",
            ]
            for row in report["results"]:
                lines += [
                    f"## {row['knowledge_base']} / {row['document_id']}",
                    "",
                    f"Action: {row['action']}",
                    "",
                    "Reasons: " + ", ".join(row["reason_codes"]),
                    "",
                ]
                lines += [
                    f"- {name}: {row[name]['status']}"
                    for name in ("text", "resources", "metadata", "ocr")
                ]
                if options.include_evidence and row["text"].get("differences"):
                    lines += [
                        "",
                        "Text differences (bounded JSON):",
                        "",
                        "```json",
                        json.dumps(
                            row["text"]["differences"], ensure_ascii=False, indent=2
                        ),
                        "```",
                    ]
                lines.append("")
            reports.append((args.report, "\n".join(lines) + "\n"))
        reports.append(
            (
                args.output,
                json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            )
        )
        publish_reports(reports)
    except InputError as exc:
        print(f"mdcompare: {exc.reason_code}", file=sys.stderr)
        return 1
    except ManifestError as exc:
        print(f"mdcompare: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("mdcompare: REPORT_WRITE_FAILED", file=sys.stderr)
        return 1
    if args.command == "knowledge":
        statuses = report["summary"]["statuses"]
        return (
            2
            if statuses.get("UNKNOWN") or statuses.get("REVIEW") or report["errors"]
            else 0
        )
    return 2 if report["summary"]["actions"]["UNKNOWN"] or report["errors"] else 0
