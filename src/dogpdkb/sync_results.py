"""Durable batch results, anomaly handoff, retry suppression and disposition.

Persistent structured results and the anomaly registry live outside the cleanable
wash run directory (``[paths].results``), so ticket 07's wash cleanup never removes
the evidence a post-hoc agent needs. Anomaly identity reuses the existing
book + document ID + source version (no new content fingerprint). Attempt/handoff
state recorded here is separate from real success, which stays in the canonical
corpus state; a disposition never fabricates a successful sync.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from dogpdkb.sync_run import STAGES, RunLayout, RunState, atomic_json, read_json
from dogpdkb.sync_stages import read_envelope
from dogpdkb.sync_time import now_iso

ANOMALY_SCHEMA = "unified-anomalies-1.0"
RESULT_SCHEMA = "unified-result-1.0"
FOLLOWUP_SCHEMA = "unified-compile-followups-1.0"
DISPOSITIONS = ("pending", "done", "abandoned", "stale")
#: Archived failures handed off, given up or resolved are not re-attempted daily.
SUPPRESSING_DISPOSITIONS = frozenset({"pending", "abandoned", "done"})


class ArchiveError(ValueError):
    """Results/anomalies cannot be persisted safely; inputs must not be lost."""


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-.")
    return text or "x"


def version_stamp(source_version: Any) -> str:
    """Filesystem-safe rendering of the existing version record (no fingerprint)."""
    if isinstance(source_version, dict):
        raw = source_version.get("utc") or source_version.get("value") or "unknown"
    else:
        raw = source_version or "unknown"
    return _slug(raw)


def anomaly_id(
    book_key: str,
    document_id: Any,
    source_version: Any,
    stage: str,
    code: str,
    resource_id: Any = None,
) -> str:
    """Stable identity from book + document ID + version + stage + code (+ image)."""
    parts = [
        _slug(book_key),
        _slug(document_id),
        version_stamp(source_version),
        _slug(stage),
        _slug(code),
    ]
    if resource_id is not None:
        parts.append(_slug(resource_id))
    return "-".join(parts)


def results_root(config: Any, book_key: str) -> Path:
    return Path(config.results) / _slug(book_key)


def registry_path(config: Any, book_key: str) -> Path:
    return results_root(config, book_key) / "anomalies.json"


def load_registry(config: Any, book_key: str) -> dict[str, Any]:
    path = registry_path(config, book_key)
    if not path.is_file():
        return {
            "schema_version": ANOMALY_SCHEMA,
            "book_key": book_key,
            "anomalies": {},
            "batch_causes": {},
        }
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != ANOMALY_SCHEMA:
        raise ArchiveError(f"unsupported anomaly registry: {path}")
    payload.setdefault("anomalies", {})
    payload.setdefault("batch_causes", {})
    return payload


def save_registry(config: Any, book_key: str, registry: dict[str, Any]) -> None:
    atomic_json(registry_path(config, book_key), registry)


def _resolve(layout: RunLayout | None, reference: str) -> Path:
    path = Path(reference)
    if path.is_absolute() or layout is None:
        return path.resolve()
    return layout.resolve(reference)


def _persist_input(
    config: Any, book_key: str, aid: str, kind: str, source: Path
) -> dict[str, Any]:
    """Copy a necessary input into the durable store; survive wash cleanup.

    Direct byte comparison guards an existing durable copy (no content hash): the
    same identity must keep the same bytes, and a conflict is never overwritten.
    """
    if not source.is_file():
        raise ArchiveError(f"necessary input is not a readable file: {source}")
    target_dir = results_root(config, book_key) / "inputs" / aid
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = target_dir / source.name
    data = source.read_bytes()
    if target.is_file():
        if target.read_bytes() != data:
            raise ArchiveError(f"durable input conflict for {aid}: {target.name}")
    else:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target_dir
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"kind": kind, "path": str(target.resolve()), "source": str(source)}


def register_anomaly(
    registry: dict[str, Any],
    config: Any,
    book_key: str,
    anomaly: dict[str, Any],
    *,
    run_id: str,
    layout: RunLayout | None = None,
) -> dict[str, Any]:
    """Idempotent upsert of one anomaly; persist inputs before recording refs."""
    stage = anomaly["stage"]
    document_id = anomaly["document_id"]
    source_version = anomaly.get("source_version")
    code = anomaly["code"]
    resource_id = anomaly.get("resource_id")
    aid = anomaly_id(book_key, document_id, source_version, stage, code, resource_id)
    inputs = [
        _persist_input(
            config,
            book_key,
            aid,
            ref.get("kind", "input"),
            _resolve(layout, ref["path"]),
        )
        for ref in anomaly.get("necessary_inputs") or []
    ]
    now = now_iso()
    existing = registry["anomalies"].get(aid)
    if existing is None:
        record = {
            "id": aid,
            "book_key": book_key,
            "book_id": anomaly.get("book_id"),
            "document_id": document_id,
            "source_version": source_version,
            "version_stamp": version_stamp(source_version),
            "stage": stage,
            "code": code,
            "reason": anomaly.get("reason") or code,
            "resource_id": resource_id,
            "image": anomaly.get("image"),
            "necessary_inputs": inputs,
            "disposition": "pending",
            "first_seen": now,
            "last_seen": now,
            "attempts": 1,
            "runs": [run_id],
            "last_run": run_id,
            "resolved_at": None,
            "resolution": None,
            "batch_cause": anomaly.get("batch_cause"),
        }
        registry["anomalies"][aid] = record
        return record
    # Preserve disposition and durable inputs; only advance handoff/attempt state.
    existing["last_seen"] = now
    existing["last_run"] = run_id
    if run_id not in existing["runs"]:
        existing["runs"].append(run_id)
        existing["attempts"] = existing.get("attempts", 0) + 1
    known = {row["path"] for row in existing["necessary_inputs"]}
    for row in inputs:
        if row["path"] not in known:
            existing["necessary_inputs"].append(row)
    if anomaly.get("reason"):
        existing["reason"] = anomaly["reason"]
    if anomaly.get("image") is not None:
        existing["image"] = anomaly["image"]
    if anomaly.get("batch_cause"):
        existing["batch_cause"] = anomaly["batch_cause"]
    return existing


def archive_anomalies(
    context: Any, anomalies: list[dict[str, Any]], *, run_id: str | None = None
) -> list[dict[str, Any]]:
    """Stage archiving API: persist anomalies + necessary inputs outside wash.

    Returns the enriched anomaly records (stable ``id``, durable input refs,
    disposition) for the stage envelope. Inputs are copied before the registry is
    written, so an archiving failure never leaves a ref to a missing file and never
    touches the canonical corpus state.
    """
    config = context.config
    book_key = context.book.key
    run = run_id or context.layout.run_id
    registry = load_registry(config, book_key)
    archived = [
        register_anomaly(
            registry, config, book_key, anomaly, run_id=run, layout=context.layout
        )
        for anomaly in anomalies
    ]
    save_registry(config, book_key, registry)
    return archived


def run_result_path(config: Any, book_key: str, run_id: str) -> Path:
    return results_root(config, book_key) / "runs" / f"{_slug(run_id)}.json"


def archive_run(state: RunState, config: Any, book_key: str) -> dict[str, Any]:
    """Persist the structured per-run result outside wash before the batch ends.

    Assembles the durable result from the lightweight run state and the stage
    envelopes, referencing the anomaly registry. Handlers archive their own
    necessary inputs; any envelope anomaly not yet registered is registered here
    as a safety net so a result never references an unrecorded anomaly.
    """
    layout = state.layout
    data = state.data
    registry = load_registry(config, book_key)
    stages: dict[str, Any] = {}
    anomaly_ids: list[str] = []
    for stage in STAGES:
        row = data["stage_states"][stage]
        envelope = read_envelope(layout, stage)
        if envelope is None:
            stages[stage] = {
                "status": row["status"],
                "counts": row.get("counts", {}),
                "reason": row.get("reason"),
                "artifacts": {},
                "anomaly_ids": [],
                "result": None,
            }
            continue
        ids = []
        for anomaly in envelope.get("anomalies") or []:
            aid = anomaly.get("id")
            if aid is None:
                aid = register_anomaly(
                    registry,
                    config,
                    book_key,
                    anomaly,
                    run_id=layout.run_id,
                    layout=layout,
                )["id"]
            ids.append(aid)
            if aid not in anomaly_ids:
                anomaly_ids.append(aid)
        stages[stage] = {
            "status": envelope["status"],
            "counts": envelope.get("counts", {}),
            "reason": envelope.get("reason"),
            "artifacts": envelope.get("artifacts", {}),
            "anomaly_ids": ids,
            "result": layout.relative(layout.result_path(stage)),
        }
    save_registry(config, book_key, registry)
    result = {
        "schema_version": RESULT_SCHEMA,
        "run_id": layout.run_id,
        "book_key": book_key,
        "book": data.get("book"),
        "status": data["status"],
        "stop_reason": data.get("stop_reason"),
        "exit_code": data.get("exit_code"),
        "started_at": data.get("started_at"),
        "ended_at": data.get("ended_at"),
        "duration_seconds": data.get("duration_seconds"),
        "corpus": data.get("corpus"),
        "config": data.get("config"),
        "counts": data.get("counts", {}),
        "stages": stages,
        "anomaly_ids": anomaly_ids,
        "run_dir": str(layout.root),
        "results_dir": str(results_root(config, book_key)),
    }
    atomic_json(run_result_path(config, book_key, layout.run_id), result)
    return result
