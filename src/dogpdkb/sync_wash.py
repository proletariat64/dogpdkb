"""Wash work-directory cleanup and historical reference migration.

Wash (``[paths].staging``) is batch workspace: paging, stage reports, plans and
temporary compile files. It is never the only home of a long-term original, a
successful OCR result, published knowledge or an anomaly recovery input. Those
live in the durable results store (``[paths].results``), the long-term OCR cache
(``[paths].ocr_cache``) and the canonical corpus/graph/state.

A finished batch cleans *earlier* finished work directories, keeping the most
recent finished batch plus every paused/unfinished one. Before a directory is
removed the durable per-run result must exist and no state/cache/anomaly record
may still reference a unique file inside it: such a file is copied into the
durable store and the reference is rewritten first. A cleanup failure is
recorded, never fatal, and the next finished batch retries it.

The historical first tidy (``dogpdkb wash``) inventories references and migrates
necessary artifacts without deleting whole directories.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dogpdkb.instance_lock import UpdateAlreadyRunning, updater_lock
from dogpdkb.ocr_cache import cache_root
from dogpdkb.sync_results import (
    _slug,
    followups_path,
    registry_path,
    results_root,
    run_result_path,
)
from dogpdkb.sync_run import (
    FINISHED_STATUSES,
    RUN_SCHEMA,
    RunError,
    RunLayout,
    atomic_json,
    book_runs,
    observed_status,
    read_json,
)
from dogpdkb.sync_state import save_state, state_path
from dogpdkb.sync_time import now_iso
from dogpdkb.workflows.get_sync_updates import safe_diagnostic
from mdocr.config import config_arguments, configure, configured_book

CLEANUP_SCHEMA = "unified-wash-cleanup-1.0"
INVENTORY_SCHEMA = "unified-wash-inventory-1.0"
MIGRATION_SCHEMA = "unified-wash-migration-1.0"
#: Provenance only: the durable copy is ``path``, so ``source`` never blocks.
PROVENANCE_KEYS = frozenset({"source"})
#: Records left by the superseded pipeline implementation.
LEGACY_RECORDS = (
    "pipeline-state.json",
    "export-progress.json",
    "capture-checkpoint.json",
)
#: Long-term originals and observation evidence that must outlive a wash run.
ARTIFACT_GLOBS = ("**/raw/*.md", "**/snapshot.json")
LEGACY_ANNOTATION = (
    "paused under the superseded implementation; the unified runner has not "
    "verified recovery - recovery requires validation of inputs and artifacts"
)
UNKNOWN_ANNOTATION = "unrecognized work directory; preserved for manual review"


class WashError(ValueError):
    """Wash cleanup or migration cannot proceed safely."""


@dataclass
class Source:
    """One durable record that may reference a file inside a wash directory."""

    name: str
    path: Path
    base: Path
    payload: Any = None
    ignore_keys: frozenset[str] = frozenset()
    readable: bool = True


@dataclass
class Reference:
    source: str
    record: Path
    field: str
    path: Path


@dataclass
class Sweep:
    """The outcome of one cleanup pass, published in the durable store."""

    runs: list[dict[str, Any]] = field(default_factory=list)
    migrated: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        actions = [row["action"] for row in self.runs]
        return {
            "removed": actions.count("removed"),
            "kept": actions.count("kept"),
            "failed": actions.count("failed"),
            "migrated": len(self.migrated),
        }


def cleanup_record_path(config: Any, book_key: str) -> Path:
    return results_root(config, book_key) / "cleanup.json"


def migration_record_path(config: Any, book_key: str) -> Path:
    return results_root(config, book_key) / "wash-migration.json"


def migrated_root(config: Any, book_key: str, run_id: str) -> Path:
    """Durable home for artifacts rescued from one wash work directory."""
    return results_root(config, book_key) / "migrated" / _slug(run_id)


def _strings(
    payload: Any, ignore: frozenset[str], trail: str = ""
) -> Iterator[tuple[str, str]]:
    """Every string value with its field trail; provenance keys are skipped."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in ignore and isinstance(value, str):
                continue
            yield from _strings(value, ignore, f"{trail}.{key}" if trail else key)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from _strings(value, ignore, f"{trail}[{index}]")
    elif isinstance(payload, str) and payload:
        yield trail, payload


def _resolved(base: Path, value: str) -> Path | None:
    """A record string is a path reference only when it resolves to real bytes."""
    try:
        path = Path(value).expanduser()
        candidate = path if path.is_absolute() else (base / path)
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.exists() else None


def sources(config: Any, book_key: str, corpus: Path) -> list[Source]:
    """Canonical state, anomaly registry, compile followups and OCR cache."""
    root = results_root(config, book_key)
    rows = [
        Source("state", state_path(corpus), corpus),
        Source(
            "anomalies",
            registry_path(config, book_key),
            root,
            ignore_keys=PROVENANCE_KEYS,
        ),
        Source("followups", followups_path(config, book_key), root),
    ]
    cache = cache_root(config, book_key)
    if cache.is_dir():
        for evidence in sorted(cache.glob("*/evidence.json")):
            rows.append(Source("ocr-cache", evidence, evidence.parent))
    for row in rows:
        if not row.path.is_file():
            continue
        try:
            row.payload = read_json(row.path)
        except RunError:
            row.readable = False
    return [row for row in rows if row.payload is not None or not row.readable]


def references(sources_: list[Source], directories: list[Path]) -> list[Reference]:
    """Existing paths inside the given wash directories, per durable record."""
    found = []
    for row in sources_:
        if row.payload is None:
            continue
        for trail, value in _strings(row.payload, row.ignore_keys):
            resolved = _resolved(row.base, value)
            if resolved is None:
                continue
            for directory in directories:
                if resolved.is_relative_to(directory):
                    found.append(Reference(row.name, row.path, trail, resolved))
                    break
    return found


def _rewrite(
    payload: Any, base: Path, ignore: frozenset[str], mapping: dict[Path, Path]
) -> Any:
    if isinstance(payload, dict):
        return {
            key: (
                value
                if key in ignore and isinstance(value, str)
                else _rewrite(value, base, ignore, mapping)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_rewrite(value, base, ignore, mapping) for value in payload]
    if isinstance(payload, str) and payload:
        resolved = _resolved(base, payload)
        if resolved is not None and resolved in mapping:
            return str(mapping[resolved])
    return payload


def _persist(config: Any, book_key: str, run_dir: Path, path: Path) -> Path:
    """Copy one necessary artifact into the durable store, never overwriting."""
    target = migrated_root(config, book_key, run_dir.name) / path.relative_to(run_dir)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = path.read_bytes()
    if target.is_file():
        if target.read_bytes() != data:
            raise WashError(f"durable copy conflict for {path.name}")
        return target
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def migrate_references(
    config: Any,
    book_key: str,
    corpus: Path,
    directory: Path,
    sweep: Sweep,
    *,
    artifacts: list[Path] | None = None,
    strict: bool = True,
) -> bool:
    """Copy necessary files out of one wash directory and rewrite the records.

    Returns False when a reference could not be made durable; the caller must
    then keep the directory. A directory reference (a live claim or a stage
    workspace) cannot be migrated file-by-file and blocks removal in ``strict``
    mode; the historical migration still copies the files it can.
    ``artifacts`` adds necessary products no record references yet.
    """
    rows = sources(config, book_key, corpus)
    unreadable = [row.name for row in rows if not row.readable]
    if unreadable:
        sweep.failures.append(
            {"reason": "records-unreadable", "sources": sorted(set(unreadable))}
        )
        return False
    found = references(rows, [directory])
    blocked = [row for row in found if row.path.is_dir()]
    if blocked and strict:
        sweep.failures.append(
            {
                "reason": "directory-reference",
                "run_id": directory.name,
                "references": [
                    {"source": row.source, "field": row.field, "path": str(row.path)}
                    for row in blocked
                ],
            }
        )
        return False
    mapping: dict[Path, Path] = {}
    wanted = [
        path
        for path in dict.fromkeys([row.path for row in found] + list(artifacts or []))
        if path.is_file()
    ]
    for path in wanted:
        reference = next((row for row in found if row.path == path), None)
        target = migrated_root(config, book_key, directory.name) / path.relative_to(
            directory
        )
        try:
            created = not target.is_file()
            mapping[path] = _persist(config, book_key, directory, path)
        except (OSError, ValueError) as error:
            sweep.failures.append(
                {
                    "reason": "migration-failed",
                    "run_id": directory.name,
                    "path": str(path),
                    "detail": safe_diagnostic(error),
                }
            )
            return False
        if created:
            sweep.migrated.append(
                {
                    "run_id": directory.name,
                    "from": str(path),
                    "to": str(mapping[path]),
                    "source": reference.source if reference else "artifact",
                    "field": reference.field if reference else None,
                }
            )
    if not mapping:
        return True
    for row in rows:
        updated = _rewrite(row.payload, row.base, row.ignore_keys, mapping)
        if updated == row.payload:
            continue
        try:
            _save(row, corpus, updated)
        except (OSError, ValueError) as error:
            sweep.failures.append(
                {
                    "reason": "reference-rewrite-failed",
                    "record": str(row.path),
                    "detail": safe_diagnostic(error),
                }
            )
            return False
        row.payload = updated
    return True


def _save(row: Source, corpus: Path, payload: Any) -> None:
    if row.name == "state":
        save_state(corpus, payload)
    else:
        atomic_json(row.path, payload)


def run_status(layout: RunLayout) -> str | None:
    try:
        payload = read_json(layout.state_path)
    except RunError:
        return None
    status, _ = observed_status(payload)
    return status if isinstance(status, str) else None


def _finished_order(layout: RunLayout) -> tuple[str, str]:
    try:
        payload = read_json(layout.state_path)
    except RunError:
        return ("", layout.run_id)
    return (
        str(payload.get("ended_at") or payload.get("started_at") or ""),
        layout.run_id,
    )


def _result_is_current_final(
    config: Any, book_key: str, layout: RunLayout, status: str
) -> bool:
    """The durable result must be a current FINAL archive of this finished run.

    Mere existence is not enough: a stale paused result archived before the run
    finished does not capture the finished run's evidence, so removing the work
    directory would lose run-dir-only evidence. Require the archived status and
    ended_at to match the run state (spec: complete result archiving before
    cleanup). An unreadable or absent result is never a current final archive.
    """
    try:
        result = read_json(run_result_path(config, book_key, layout.run_id))
        state = read_json(layout.state_path)
    except RunError:
        return False
    if not isinstance(result, dict) or not isinstance(state, dict):
        return False
    ended = state.get("ended_at")
    return (
        status in FINISHED_STATUSES
        and result.get("status") == status
        and ended is not None
        and result.get("ended_at") == ended
    )


def cleanup_wash(
    config: Any,
    book_key: str,
    *,
    corpus: Path,
    trigger: RunLayout | None = None,
) -> dict[str, Any]:
    """Remove earlier finished work directories after a completed/partial batch.

    Keeps the most recent finished batch and every paused/unfinished batch, and
    never touches the durable results store, the OCR cache or the canonical
    corpus/graph/state. Failures are recorded in the durable cleanup record and
    retried at the next finished batch; they never break a valid knowledge base.
    """
    sweep = Sweep()
    layouts = book_runs(Path(config.staging), book_key)
    statuses = {layout: run_status(layout) for layout in layouts}
    finished = sorted(
        (layout for layout in layouts if statuses[layout] in FINISHED_STATUSES),
        key=_finished_order,
    )
    newest = finished[-1] if finished else None
    removable: list[RunLayout] = []
    for layout in layouts:
        status = statuses[layout]
        row = {
            "run_id": layout.run_id,
            "run_dir": str(layout.root),
            "status": status or "unreadable",
            "action": "kept",
            "reason": "run state is unreadable",
        }
        if status is None:
            sweep.runs.append(row)
        elif status not in FINISHED_STATUSES:
            row["reason"] = "unfinished batch must stay resumable"
            sweep.runs.append(row)
        elif layout == newest or layout == trigger:
            row["reason"] = "most recent finished batch"
            sweep.runs.append(row)
        elif not run_result_path(config, book_key, layout.run_id).is_file():
            row["reason"] = "batch result is not archived yet"
            sweep.runs.append(row)
        elif not _result_is_current_final(config, book_key, layout, status):
            row["reason"] = "batch result archive is stale; not a current final result"
            sweep.runs.append(row)
        else:
            removable.append(layout)
    for layout in removable:
        row = {
            "run_id": layout.run_id,
            "run_dir": str(layout.root),
            "status": statuses[layout],
            "action": "kept",
            "reason": "necessary references are not durable yet",
        }
        ready = migrate_references(config, book_key, corpus, layout.root, sweep)
        remaining = references(sources(config, book_key, corpus), [layout.root])
        if ready and not remaining:
            try:
                shutil.rmtree(layout.root)
            except OSError as error:
                detail = safe_diagnostic(error)
                row.update(action="failed", reason=detail)
                sweep.failures.append(
                    {
                        "reason": "removal-failed",
                        "run_id": layout.run_id,
                        "detail": detail,
                    }
                )
            else:
                row.update(action="removed", reason="finished batch workspace")
        sweep.runs.append(row)
    sweep.runs.sort(key=lambda item: item["run_id"])
    record = {
        "schema_version": CLEANUP_SCHEMA,
        "book_key": book_key,
        "staging": str(Path(config.staging)),
        "results_dir": str(results_root(config, book_key)),
        "trigger_run": trigger.run_id if trigger else None,
        "updated_at": now_iso(),
        "counts": sweep.counts(),
        "runs": sweep.runs,
        "migrated": sweep.migrated,
        "failures": sweep.failures,
    }
    try:
        atomic_json(cleanup_record_path(config, book_key), record)
    except (OSError, ValueError) as error:
        sweep.failures.append(
            {"reason": "cleanup-record-failed", "detail": safe_diagnostic(error)}
        )
        record["failures"] = sweep.failures
    return record


def work_dirs(config: Any, book_key: str) -> list[Path]:
    """Every wash work directory of one book, including historical layouts."""
    root = Path(config.staging) / book_key
    if not root.is_dir():
        return []
    return sorted(
        path.resolve()
        for path in root.iterdir()
        if path.is_dir() and not path.is_symlink()
    )


def classify(directory: Path) -> dict[str, Any]:
    """Which implementation owns a work directory, and whether resume is proven.

    Only the current runner's own ``unified-run-1.0`` records are resumable by
    the unified runner (which re-validates artifacts on resume). A directory
    from the superseded pipeline is never assumed recoverable: recovery requires
    validation first.
    """
    state_file = directory / "run.json"
    if state_file.is_file():
        try:
            payload = read_json(state_file)
        except RunError:
            payload = None
        if isinstance(payload, dict) and payload.get("schema_version") == RUN_SCHEMA:
            status, _ = observed_status(payload)
            return {
                "kind": "unified",
                "status": status,
                "resume_verified": True,
                "annotation": None,
            }
    records = [name for name in LEGACY_RECORDS if (directory / name).is_file()]
    if records:
        status = "unknown"
        legacy_state = directory / "pipeline-state.json"
        if legacy_state.is_file():
            try:
                payload = read_json(legacy_state)
                status = str(payload.get("status") or "unknown")
            except RunError:
                status = "unreadable"
        return {
            "kind": "legacy",
            "status": status,
            "resume_verified": False,
            "annotation": LEGACY_ANNOTATION,
            "records": records,
        }
    return {
        "kind": "unknown",
        "status": "unknown",
        "resume_verified": False,
        "annotation": UNKNOWN_ANNOTATION,
    }


def necessary_artifacts(directory: Path) -> list[Path]:
    """Long-term originals, observation evidence and legacy recovery records."""
    found: set[Path] = set()
    for pattern in ARTIFACT_GLOBS:
        found.update(
            path.resolve() for path in directory.glob(pattern) if path.is_file()
        )
    for name in LEGACY_RECORDS:
        path = directory / name
        if path.is_file():
            found.add(path.resolve())
    return sorted(found)


def _run_row(
    config: Any,
    book_key: str,
    directory: Path,
    rows: list[Source],
) -> dict[str, Any]:
    info = classify(directory)
    found = references(rows, [directory])
    artifacts = necessary_artifacts(directory)
    archived = run_result_path(config, book_key, directory.name).is_file()
    if (
        info["kind"] == "unified"
        and info["status"] in FINISHED_STATUSES
        and archived
        and not found
    ):
        action = "cleanable"
        reason = "finished batch; automatic cleanup may remove it"
    elif found or artifacts:
        action = "migrate"
        reason = "necessary artifacts or references still live only in wash"
    else:
        action = "preserve"
        reason = "no necessary artifact found; kept for review"
    return {
        "run_id": directory.name,
        "run_dir": str(directory),
        **info,
        "result_archived": archived,
        "references": [
            {"source": row.source, "field": row.field, "path": str(row.path)}
            for row in found
        ],
        "necessary_artifacts": [str(path) for path in artifacts],
        "action": action,
        "reason": reason,
    }


def inventory(config: Any, book_key: str, *, corpus: Path) -> dict[str, Any]:
    """Read-only first tidy: enumerate work directories, references and artifacts."""
    rows = sources(config, book_key, corpus)
    runs = [
        _run_row(config, book_key, directory, rows)
        for directory in work_dirs(config, book_key)
    ]
    counts = {
        "runs": len(runs),
        "unified": sum(1 for row in runs if row["kind"] == "unified"),
        "legacy": sum(1 for row in runs if row["kind"] == "legacy"),
        "unknown": sum(1 for row in runs if row["kind"] == "unknown"),
        "references": sum(len(row["references"]) for row in runs),
        "necessary_artifacts": sum(len(row["necessary_artifacts"]) for row in runs),
        "cleanable": sum(1 for row in runs if row["action"] == "cleanable"),
        "migrate": sum(1 for row in runs if row["action"] == "migrate"),
    }
    return {
        "schema_version": INVENTORY_SCHEMA,
        "book_key": book_key,
        "staging": str(Path(config.staging)),
        "results_dir": str(results_root(config, book_key)),
        "counts": counts,
        "runs": runs,
    }


def migrate_wash(config: Any, book_key: str, *, corpus: Path) -> dict[str, Any]:
    """Persist necessary artifacts and rewrite old references; delete nothing.

    Historical directories keep all their evidence. A legacy or unrecognized run
    is annotated in place so nobody assumes the unified runner can resume it.
    Repeating the migration copies nothing new (byte comparison, no fingerprint).
    """
    sweep = Sweep()
    rows = []
    for directory in work_dirs(config, book_key):
        info = classify(directory)
        artifacts = necessary_artifacts(directory)
        before = len(sweep.migrated)
        durable = migrate_references(
            config,
            book_key,
            corpus,
            directory,
            sweep,
            artifacts=artifacts,
            strict=False,
        )
        row = {
            "run_id": directory.name,
            "run_dir": str(directory),
            **info,
            "references_migrated": durable,
            "artifacts": [
                {
                    "from": str(path),
                    "to": str(
                        migrated_root(config, book_key, directory.name)
                        / path.relative_to(directory)
                    ),
                }
                for path in artifacts
            ],
            "migrated": sweep.migrated[before:],
        }
        if info["kind"] != "unified":
            annotation = {
                "schema_version": MIGRATION_SCHEMA,
                "run_id": directory.name,
                "kind": info["kind"],
                "status": info["status"],
                "resume_verified": False,
                "annotation": info["annotation"],
                "migrated_at": now_iso(),
                "artifacts": row["artifacts"],
            }
            try:
                atomic_json(directory / "wash-migration.json", annotation)
            except (OSError, ValueError) as error:
                sweep.failures.append(
                    {
                        "reason": "annotation-failed",
                        "run_id": directory.name,
                        "detail": safe_diagnostic(error),
                    }
                )
        rows.append(row)
    manifest = {
        "schema_version": MIGRATION_SCHEMA,
        "book_key": book_key,
        "staging": str(Path(config.staging)),
        "results_dir": str(results_root(config, book_key)),
        "updated_at": now_iso(),
        "counts": {
            "runs": len(rows),
            "migrated": len(sweep.migrated),
            "failures": len(sweep.failures),
        },
        "runs": rows,
        "migrated": sweep.migrated,
        "failures": sweep.failures,
    }
    atomic_json(migration_record_path(config, book_key), manifest)
    return manifest


def report(view: dict[str, Any]) -> str:
    """Human-readable inventory/migration summary; no document bodies."""
    counts = view["counts"]
    lines = [f"book {view['book_key']} staging {view['staging']}"]
    lines.append(" ".join(f"{key}={value}" for key, value in counts.items()))
    migration = view.get("schema_version") == MIGRATION_SCHEMA
    for row in view["runs"]:
        if migration:
            lines.append(
                f"  {row['run_id']} {row['kind']} {row['status']} "
                f"references_migrated={row['references_migrated']} "
                f"artifacts={len(row['artifacts'])} migrated={len(row['migrated'])}"
            )
        else:
            lines.append(
                f"  {row['run_id']} {row['kind']} {row['status']} "
                f"action={row['action']} references={len(row['references'])} "
                f"artifacts={len(row['necessary_artifacts'])}"
            )
        if row.get("annotation"):
            lines.append(f"    {row['annotation']}")
    for row in view.get("failures", []):
        lines.append(f"  deferred: {safe_diagnostic(row)}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dogpdkb wash",
        description=(
            "Inventory historical wash work directories and migrate necessary "
            "artifacts into the durable results store. Never deletes a directory."
        ),
    )
    config_arguments(parser, book=True)
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="copy necessary artifacts and rewrite old references (no deletion)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(arguments)
    config = configure(parser, args.config)
    book = configured_book(parser, config, args)
    try:
        if args.migrate:
            with updater_lock():
                view = migrate_wash(config, book.key, corpus=book.corpus)
        else:
            view = inventory(config, book.key, corpus=book.corpus)
    except UpdateAlreadyRunning as error:
        parser.exit(1, f"dogpdkb wash: {error}\n")
    except (WashError, RunError, OSError, ValueError) as error:
        parser.exit(1, f"dogpdkb wash: {safe_diagnostic(error)}\n")
    print(json.dumps(view, ensure_ascii=False) if args.json else report(view))
    return 0


__all__ = [
    "CLEANUP_SCHEMA",
    "INVENTORY_SCHEMA",
    "MIGRATION_SCHEMA",
    "Reference",
    "Source",
    "Sweep",
    "WashError",
    "build_parser",
    "classify",
    "cleanup_record_path",
    "cleanup_wash",
    "inventory",
    "main",
    "migrate_references",
    "migrate_wash",
    "migrated_root",
    "migration_record_path",
    "necessary_artifacts",
    "references",
    "report",
    "run_status",
    "sources",
    "work_dirs",
]
