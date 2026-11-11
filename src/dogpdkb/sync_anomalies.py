"""Read-only anomaly scan and version-scoped disposition for the post-hoc agent.

``dogpdkb anomalies`` scans the durable results store (never the cleanable wash
run directory), reads the persisted necessary inputs, and records done/abandoned/
stale for one archived source-version anomaly. Scanning executes no sync work; a
disposition is handoff state and never fabricates canonical success. Disposition
writes hold the same user-level run lock as the daily batch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dogpdkb.instance_lock import UpdateAlreadyRunning, updater_lock
from dogpdkb.sync_results import (
    DISPOSITIONS,
    RESULT_SCHEMA,
    ArchiveError,
    load_followups,
    load_registry,
    record_posthoc_success,
    results_root,
    save_registry,
)
from dogpdkb.sync_run import STAGES, RunError, read_json
from dogpdkb.sync_time import now_iso
from dogpdkb.workflows.get_sync_updates import safe_diagnostic
from mdocr.config import config_arguments, configure, configured_book

SCAN_SCHEMA = "unified-anomaly-scan-1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dogpdkb anomalies",
        description=(
            "Scan durable batch results and archived anomalies; read necessary "
            "inputs and record a version-scoped disposition. Never executes sync."
        ),
    )
    config_arguments(parser, book=True)
    parser.add_argument("--json", action="store_true", help="machine-readable scan")
    parser.add_argument("--run", help="only anomalies and results seen in this run")
    parser.add_argument(
        "--disposition",
        choices=DISPOSITIONS,
        help="scan filter, or the disposition to record with --resolve",
    )
    parser.add_argument("--stage", choices=STAGES, help="scan filter by stage")
    parser.add_argument("--show", metavar="ID", help="print one anomaly record as JSON")
    parser.add_argument(
        "--resolve", metavar="ID", help="record a disposition for this anomaly id"
    )
    parser.add_argument("--note", help="operator rationale stored with the disposition")
    parser.add_argument(
        "--evidence",
        type=Path,
        metavar="FILE",
        help="post-hoc success evidence JSON (required for --disposition done)",
    )
    return parser


def run_results(config: Any, book_key: str) -> list[dict[str, Any]]:
    """Every durable per-run result, oldest first; wash cleanup cannot remove them."""
    directory = results_root(config, book_key) / "runs"
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        payload = read_json(path)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != RESULT_SCHEMA
        ):
            raise ArchiveError(f"unsupported batch result: {path}")
        rows.append(payload)
    return rows


def scan_view(
    config: Any,
    book_key: str,
    *,
    run: str | None = None,
    disposition: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    registry = load_registry(config, book_key)
    runs = run_results(config, book_key)
    anomalies = sorted(
        registry["anomalies"].values(),
        key=lambda row: (str(row.get("first_seen") or ""), row["id"]),
    )
    causes = sorted(
        registry["batch_causes"].values(),
        key=lambda row: (str(row.get("recorded_at") or ""), row["id"]),
    )
    followups = sorted(
        load_followups(config, book_key)["followups"].values(),
        key=lambda row: (str(row.get("created_at") or ""), row["id"]),
    )
    if run is not None:
        runs = [row for row in runs if row["run_id"] == run]
        anomalies = [row for row in anomalies if run in row.get("runs", [])]
        causes = [row for row in causes if row.get("run_id") == run]
    if disposition is not None:
        anomalies = [row for row in anomalies if row["disposition"] == disposition]
    if stage is not None:
        anomalies = [row for row in anomalies if row["stage"] == stage]
    counts: dict[str, int] = {"runs": len(runs), "anomalies": len(anomalies)}
    for name in DISPOSITIONS:
        counts[name] = sum(1 for row in anomalies if row["disposition"] == name)
    counts["batch_causes"] = len(causes)
    counts["compile_followups"] = len(followups)
    counts["followups_pending"] = sum(
        1 for row in followups if row["status"] == "pending"
    )
    return {
        "schema_version": SCAN_SCHEMA,
        "book_key": book_key,
        "results_dir": str(results_root(config, book_key)),
        "runs": runs,
        "anomalies": anomalies,
        "batch_causes": causes,
        "compile_followups": followups,
        "counts": counts,
    }


def report(view: dict[str, Any]) -> str:
    counts = view["counts"]
    lines = [f"book {view['book_key']} results {view['results_dir']}"]
    lines.append(f"runs: {counts['runs']}")
    for row in view["runs"]:
        lines.append(
            f"  {row['run_id']} {row['status']} anomalies={len(row.get('anomaly_ids', []))}"
        )
    dispositions = " ".join(f"{name}={counts[name]}" for name in DISPOSITIONS)
    lines.append(f"anomalies: {counts['anomalies']} {dispositions}")
    for row in view["anomalies"]:
        lines.append(
            f"  {row['id']} doc={row['document_id']} version={row['version_stamp']} "
            f"stage={row['stage']} code={row['code']} "
            f"disposition={row['disposition']} attempts={row.get('attempts', 0)}"
        )
    lines.append(f"batch causes: {counts['batch_causes']}")
    for row in view["batch_causes"]:
        lines.append(
            f"  {row['id']} stage={row['stage']} code={row['code']} "
            f"affected={len(row.get('anomaly_ids', []))}"
        )
    lines.append(
        f"compile followups: {counts['compile_followups']} "
        f"pending={counts['followups_pending']}"
    )
    for row in view["compile_followups"]:
        lines.append(
            f"  {row['id']} doc={row['document_id']} version={row['version_stamp']} "
            f"status={row['status']}"
        )
    return "\n".join(lines)


def find_anomaly(config: Any, book_key: str, aid: str) -> dict[str, Any]:
    record = load_registry(config, book_key)["anomalies"].get(aid)
    if record is None:
        raise ArchiveError(f"unknown anomaly: {aid}")
    return record


def resolve_anomaly(
    config: Any,
    book_key: str,
    aid: str,
    disposition: str,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Record a version-scoped disposition; handoff state only, never success.

    The anomaly id encodes book + document + source version + stage + code, so a
    disposition applies to that version alone and never bans the source. Repeating
    the same abandoned/stale disposition is a no-op.
    """
    if disposition not in DISPOSITIONS:
        raise ArchiveError(f"unknown disposition: {disposition}")
    registry = load_registry(config, book_key)
    record = registry["anomalies"].get(aid)
    if record is None:
        raise ArchiveError(f"unknown anomaly: {aid}")
    if disposition == "done":
        raise ArchiveError("done requires post-hoc success evidence (--evidence)")
    if record["disposition"] == disposition:
        return record
    now = now_iso()
    record["disposition"] = disposition
    record["resolved_at"] = now
    record["resolution"] = {
        "disposition": disposition,
        "note": note,
        "recorded_at": now,
    }
    save_registry(config, book_key, registry)
    return record


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(arguments)
    config = configure(parser, args.config)
    book = configured_book(parser, config, args)
    try:
        if args.show:
            print(
                json.dumps(
                    find_anomaly(config, book.key, args.show), ensure_ascii=False
                )
            )
            return 0
        if args.resolve:
            if args.disposition is None:
                parser.exit(1, "dogpdkb anomalies: --resolve requires --disposition\n")
            with updater_lock():
                if args.disposition == "done":
                    if args.evidence is None:
                        raise ArchiveError(
                            "done requires post-hoc success evidence (--evidence FILE)"
                        )
                    record = record_posthoc_success(
                        config,
                        book,
                        args.resolve,
                        read_json(args.evidence),
                        note=args.note,
                    )
                else:
                    if args.evidence is not None:
                        raise ArchiveError(
                            "--evidence is only valid with --disposition done"
                        )
                    record = resolve_anomaly(
                        config, book.key, args.resolve, args.disposition, note=args.note
                    )
            print(json.dumps(record, ensure_ascii=False))
            return 0
        view = scan_view(
            config,
            book.key,
            run=args.run,
            disposition=args.disposition,
            stage=args.stage,
        )
    except UpdateAlreadyRunning as error:
        parser.exit(1, f"dogpdkb anomalies: {error}\n")
    except (ArchiveError, RunError, OSError, ValueError) as error:
        parser.exit(1, f"dogpdkb anomalies: {safe_diagnostic(error)}\n")
    print(json.dumps(view, ensure_ascii=False) if args.json else report(view))
    return 0


__all__ = [
    "SCAN_SCHEMA",
    "build_parser",
    "find_anomaly",
    "main",
    "report",
    "resolve_anomaly",
    "run_results",
    "scan_view",
]
