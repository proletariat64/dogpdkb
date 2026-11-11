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

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

CLEANUP_SCHEMA = "unified-wash-cleanup-1.0"
MIGRATION_SCHEMA = "unified-wash-migration-1.0"
#: Provenance only: the durable copy is ``path``, so ``source`` never blocks.
PROVENANCE_KEYS = frozenset({"source"})


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
    save: Any = None


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
    payload: Any, base: Path, ignore: frozenset[str], trail: str = ""
) -> Iterator[tuple[str, str]]:
    """Every string value with its field trail; provenance keys are skipped."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in ignore and isinstance(value, str):
                continue
            yield from _strings(value, base, ignore, f"{trail}.{key}" if trail else key)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from _strings(value, base, ignore, f"{trail}[{index}]")
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
        for trail, value in _strings(row.payload, row.base, row.ignore_keys):
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
    directories: list[Path],
    sweep: Sweep,
) -> bool:
    """Copy referenced files out of wash and rewrite the records that point at them.

    Returns False when any reference could not be made durable; the caller must
    then keep the directory. A directory reference (a live claim or a stage
    workspace) cannot be migrated file-by-file and always blocks removal.
    """
    rows = sources(config, book_key, corpus)
    unreadable = [row.name for row in rows if not row.readable]
    if unreadable:
        sweep.failures.append(
            {"reason": "records-unreadable", "sources": sorted(set(unreadable))}
        )
        return False
    found = references(rows, directories)
    blocked = [row for row in found if row.path.is_dir()]
    if blocked:
        sweep.failures.append(
            {
                "reason": "directory-reference",
                "references": [
                    {"source": row.source, "field": row.field, "path": str(row.path)}
                    for row in blocked
                ],
            }
        )
        return False
    mapping: dict[Path, Path] = {}
    for row in found:
        if row.path in mapping:
            continue
        run_dir = next(
            directory for directory in directories if row.path.is_relative_to(directory)
        )
        try:
            mapping[row.path] = _persist(config, book_key, run_dir, row.path)
        except (OSError, ValueError) as error:
            sweep.failures.append(
                {
                    "reason": "migration-failed",
                    "path": str(row.path),
                    "detail": safe_diagnostic(error),
                }
            )
            return False
        sweep.migrated.append(
            {
                "run_id": run_dir.name,
                "from": str(row.path),
                "to": str(mapping[row.path]),
                "source": row.source,
                "field": row.field,
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
        else:
            removable.append(layout)
    directories = [layout.root for layout in removable]
    ready = (
        migrate_references(config, book_key, corpus, directories, sweep)
        if directories
        else True
    )
    for layout in removable:
        row = {
            "run_id": layout.run_id,
            "run_dir": str(layout.root),
            "status": statuses[layout],
            "action": "kept",
            "reason": "necessary references are not durable yet",
        }
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


__all__ = [
    "CLEANUP_SCHEMA",
    "MIGRATION_SCHEMA",
    "Reference",
    "Source",
    "Sweep",
    "WashError",
    "cleanup_record_path",
    "cleanup_wash",
    "migrate_references",
    "migrated_root",
    "migration_record_path",
    "references",
    "run_status",
    "sources",
]
