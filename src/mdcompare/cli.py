"""Command-line entrypoint; all input and downstream state remain read-only."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .comparison import compare_documents
from .io import (
    InputError,
    InputReader,
    publish_reports,
    referenced_paths,
    validate_outputs,
)
from .manifest import ManifestError, load_manifest
from .model import CompareOptions


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(1, "mdcompare: INVALID_ARGUMENTS\n")


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
    parser = ArgumentParser(prog="mdcompare")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--incoming", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
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
        help="Maximum bytes per document or evidence file; oversized inputs stay UNKNOWN",
    )
    compare.add_argument(
        "--normalize-line-endings",
        action="store_true",
        help="Explicitly normalize CRLF and lone CR to LF (rule version 1)",
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
    args = parser.parse_args(argv)
    options = CompareOptions(
        include_evidence=args.include_evidence,
        max_file_bytes=args.max_file_bytes,
        normalize_line_endings=args.normalize_line_endings,
        max_diff_work=args.max_diff_work,
        max_evidence_chars=args.max_evidence_chars,
        max_char_region=args.max_char_region,
    )
    try:
        reader = InputReader(options.max_file_bytes)
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
        if args.report:
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
    return 2 if report["summary"]["actions"]["UNKNOWN"] or report["errors"] else 0
