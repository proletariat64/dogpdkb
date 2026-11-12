"""Durable batch results, anomaly handoff, retry suppression and disposition.

Persistent structured results and the anomaly registry live outside the cleanable
wash run directory (``[paths].results``), so wash cleanup never removes
the evidence a post-hoc agent needs. Anomaly identity reuses the existing
book + document ID + source version (no new content fingerprint). Attempt/handoff
state recorded here is separate from real success, which stays in the canonical
corpus state; a disposition never fabricates a successful sync.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dogpdkb.sync_run import (
    STAGES,
    RunError,
    RunLayout,
    RunState,
    atomic_json,
    read_json,
)
from dogpdkb.sync_stages import read_envelope
from dogpdkb.sync_time import now_iso

ANOMALY_SCHEMA = "unified-anomalies-1.0"
RESULT_SCHEMA = "unified-result-1.0"
FOLLOWUP_SCHEMA = "unified-compile-followups-1.0"
EVIDENCE_SCHEMA = "unified-posthoc-evidence-1.0"
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
        book_key,
        _slug(document_id),
        version_stamp(source_version),
        _slug(stage),
        _slug(code),
    ]
    if resource_id is not None:
        # Lossless encoding of the image identity: _slug collapses '/', '?',
        # '=', '&', ':' to '-', aliasing distinct URLs (e.g. /a/b.png and
        # /a-b.png) into one anomaly. quote is injective and reversible, so each
        # image keeps its own identity without a new content fingerprint.
        parts.append(quote(str(resource_id), safe=""))
    return "-".join(parts)


def results_root(config: Any, book_key: str) -> Path:
    # book_key is validator-safe; _slug would alias keys like "fund" and "fund-".
    return Path(config.results) / book_key


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
    # The logical id can exceed the 255-byte filesystem component limit (long
    # query/signed image URLs; measured corpus max 858 B). Using it as one
    # component raises ENAMETOOLONG, which loses the whole batch's archiving and
    # the durable run result. Store inputs under a bounded digest of the id: this
    # hashes the identity STRING, not file content (no content fingerprint), and
    # is deterministic so re-archiving keeps the same durable copy. The full
    # logical id stays verbatim in the registry record.
    storage_key = hashlib.sha256(aid.encode("utf-8")).hexdigest()[:32]
    target_dir = results_root(config, book_key) / "inputs" / storage_key
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
    if existing["disposition"] == "stale":
        # An invalidated handoff was re-attempted and failed again: hand it off
        # anew so the daily sync does not retry the same version every day.
        existing["disposition"] = "pending"
        existing["resolved_at"] = None
        existing["resolution"] = None
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


def suppressing_anomalies(
    config: Any, book_key: str, *, stage: str | None = None
) -> list[dict[str, Any]]:
    """Archived failures the daily sync must not re-attempt (handoff state).

    Consumed by capture before selection: a pending/abandoned/done anomaly for a
    source version hands that version to the post-hoc agent. ``stale`` is absent
    by design — an invalidated handoff returns the version to normal daily sync.
    This is attempt/handoff state only; real success stays in canonical state.
    """
    registry = load_registry(config, book_key)
    return [
        record
        for record in registry["anomalies"].values()
        if record["disposition"] in SUPPRESSING_DISPOSITIONS
        and (stage is None or record["stage"] == stage)
    ]


def batch_cause_id(book_key: str, run_id: str, stage: str, code: str) -> str:
    """One batch cause per book + run + stage + code (a shared token/quota error)."""
    return "-".join([_slug(book_key), _slug(run_id), _slug(stage), _slug(code)])


def record_batch_cause(
    registry: dict[str, Any],
    book_key: str,
    *,
    run_id: str,
    cause: dict[str, Any],
    anomaly_ids: list[str],
) -> dict[str, Any]:
    """Record a shared batch-level cause and enumerate the affected anomalies.

    A single token/quota failure that blocks many images is recorded once as a
    batch cause (book + run + stage + code) instead of N independent root causes.
    It enumerates the affected anomaly IDs so a post-hoc agent sees the whole
    blast radius from one record. Idempotent per batch cause: re-archiving merges
    the affected IDs rather than duplicating the record.
    """
    stage = cause["stage"]
    code = cause["code"]
    cid = batch_cause_id(book_key, run_id, stage, code)
    now = now_iso()
    existing = registry["batch_causes"].get(cid)
    if existing is None:
        record = {
            "id": cid,
            "book_key": book_key,
            "run_id": run_id,
            "stage": stage,
            "code": code,
            "reason": cause.get("reason") or code,
            "anomaly_ids": list(anomaly_ids),
            "recorded_at": now,
            "updated_at": now,
        }
        registry["batch_causes"][cid] = record
        return record
    for aid in anomaly_ids:
        if aid not in existing["anomaly_ids"]:
            existing["anomaly_ids"].append(aid)
    existing["updated_at"] = now
    if cause.get("reason"):
        existing["reason"] = cause["reason"]
    return existing


def archive_anomalies(
    context: Any,
    anomalies: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    batch_cause: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Stage archiving API: persist anomalies + necessary inputs outside wash.

    Returns the enriched anomaly records (stable ``id``, durable input refs,
    disposition) for the stage envelope. Inputs are copied before the registry is
    written, so an archiving failure never leaves a ref to a missing file and never
    touches the canonical corpus state. When ``batch_cause`` is given (a shared
    token/quota failure), one batch cause is recorded and every affected anomaly
    is stamped with its id so the whole blast radius is enumerable.
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
    if batch_cause is not None:
        cause = record_batch_cause(
            registry,
            book_key,
            run_id=run,
            cause=batch_cause,
            anomaly_ids=[row["id"] for row in archived],
        )
        for row in archived:
            row["batch_cause"] = cause["id"]
    save_registry(config, book_key, registry)
    return archived


def run_result_path(config: Any, book_key: str, run_id: str) -> Path:
    return results_root(config, book_key) / "runs" / f"{_slug(run_id)}.json"


def _document_identities(layout: RunLayout) -> list[dict[str, Any]]:
    """Per-document identity rows from the capture snapshot, durable outside wash.

    Records the doc ID, title, source version, export status and reuse so a
    post-hoc agent can identify exactly which source version each result refers to
    even after the wash run directory is cleaned. Reuses the existing snapshot
    version record (no new fingerprint); a missing/unreadable snapshot degrades to
    no rows rather than failing the whole result.
    """
    path = layout.stage_dir("capture") / "snapshot.json"
    if not path.is_file():
        return []
    try:
        snapshot = read_json(path)
    except RunError:
        return []
    if not isinstance(snapshot, dict):
        return []
    records = snapshot.get("documents") or {}
    exports = snapshot.get("exports") or {}
    rows = []
    for doc_id, record in sorted(records.items(), key=lambda item: str(item[0])):
        if not isinstance(record, dict):
            continue
        export = exports.get(str(doc_id)) or {}
        rows.append(
            {
                "document_id": record.get("id"),
                "title": record.get("title"),
                "source_version": record.get("version"),
                "export_status": export.get("status"),
                "reused": bool(export.get("reused")),
                "labels": export.get("labels") or [],
            }
        )
    return rows


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
        "documents": _document_identities(layout),
        "anomaly_ids": anomaly_ids,
        "run_dir": str(layout.root),
        "results_dir": str(results_root(config, book_key)),
    }
    atomic_json(run_result_path(config, book_key, layout.run_id), result)
    return result


def followups_path(config: Any, book_key: str) -> Path:
    return results_root(config, book_key) / "compile-followups.json"


def load_followups(config: Any, book_key: str) -> dict[str, Any]:
    path = followups_path(config, book_key)
    if not path.is_file():
        return {
            "schema_version": FOLLOWUP_SCHEMA,
            "book_key": book_key,
            "followups": {},
        }
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != FOLLOWUP_SCHEMA
    ):
        raise ArchiveError(f"unsupported compile followups: {path}")
    payload.setdefault("followups", {})
    return payload


def save_followups(config: Any, book_key: str, registry: dict[str, Any]) -> None:
    atomic_json(followups_path(config, book_key), registry)


def followup_id(book_key: str, document_id: Any, source_version: Any) -> str:
    """One follow-up per book + document + source version (no new fingerprint)."""
    return "-".join(
        [book_key, _slug(document_id), version_stamp(source_version), "compile"]
    )


def register_compile_followup(
    config: Any,
    book_key: str,
    *,
    document_id: Any,
    source_version: Any,
    reason: str,
    inputs: list[dict[str, Any]],
    anomaly_id: str | None = None,
    code: str = "compile_input_changed",
) -> dict[str, Any]:
    """Explicitly register follow-up compile work after a published input changed.

    An OCR backfill that changes the input a published
    compile consumed MUST register this follow-up; writing the OCR cache alone is
    not a completed knowledge update. The follow-up stays ``pending`` until compile
    publishes and records real completion. Registration is idempotent per version:
    repeated backfills merge inputs instead of duplicating work, and a backfill
    after a completed follow-up reopens it.
    """
    registry = load_followups(config, book_key)
    fid = followup_id(book_key, document_id, source_version)
    now = now_iso()
    existing = registry["followups"].get(fid)
    if existing is None:
        record = {
            "id": fid,
            "book_key": book_key,
            "document_id": document_id,
            "source_version": source_version,
            "version_stamp": version_stamp(source_version),
            "code": code,
            "reason": reason,
            "inputs": list(inputs),
            "anomaly_id": anomaly_id,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "run_id": None,
            "resolution": None,
        }
        registry["followups"][fid] = record
    else:
        known = {row["path"] for row in existing["inputs"]}
        for row in inputs:
            if row["path"] not in known:
                existing["inputs"].append(row)
                known.add(row["path"])
        existing["reason"] = reason
        existing["updated_at"] = now
        if anomaly_id is not None:
            existing["anomaly_id"] = anomaly_id
        if existing["status"] in {"done", "failed"}:
            existing["status"] = "pending"
            existing["resolution"] = None
        record = existing
    save_followups(config, book_key, registry)
    return record


def _evidence_refs(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify post-hoc cache evidence is readable; never fabricate a work product."""
    refs = evidence.get("cache")
    if not isinstance(refs, list) or not refs:
        raise ArchiveError("post-hoc evidence requires at least one cache reference")
    verified = []
    for row in refs:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ArchiveError("post-hoc cache reference requires a path")
        path = Path(row["path"]).expanduser().resolve()
        if not path.is_file():
            raise ArchiveError(f"post-hoc cache evidence is not readable: {path}")
        verified.append({"kind": row.get("kind", "cache"), "path": str(path)})
    return verified


def _materialize_ocr_backfill(
    book: Any, record: dict[str, Any], cache: list[dict[str, Any]]
) -> None:
    """Write a post-hoc OCR backfill into the canonical graph.ocr compile reads.

    compile enriches only ``status == "success"`` graph.ocr images; a backfill that
    lives solely in the evidence cache never reaches the published source. Match
    the failed image by the anomaly's resource_id and flip it to success with the
    backfill artifact, preserving its durable prepared/source paths.
    """
    resource_id = record.get("resource_id")
    if not isinstance(resource_id, str):
        return
    artifact = next(
        (row["path"] for row in cache if row.get("kind") in {"ocr", "ocr_artifact"}),
        None,
    )
    if artifact is None and len(cache) == 1:
        artifact = cache[0]["path"]
    if artifact is None:
        return
    from dogpdkb.sync_state import document_record, load_state, save_state

    state = load_state(book.corpus, book.key)
    doc = document_record(state, book.key, str(record["document_id"]))
    ocr = (doc.get("graph") or {}).get("ocr")
    if not isinstance(ocr, dict) or not isinstance(ocr.get("images"), list):
        return
    for image in ocr["images"]:
        if isinstance(image, dict) and image.get("resource_id") == resource_id:
            image["status"] = "success"
            image["artifact"] = artifact
            save_state(book.corpus, state)
            return


def record_posthoc_success(
    config: Any,
    book: Any,
    aid: str,
    evidence: dict[str, Any],
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Record a post-hoc success: cache evidence + real completion + disposition.

    Ordering guarantees no fabricated success: the readable cache evidence is
    verified first, real completion is written to canonical state, follow-up
    compile work is registered when the published input changed, and only then is
    the disposition set to ``done``. A failure leaves the anomaly pending so the
    agent can retry; it never marks done without real completion.
    """
    from dogpdkb.sync_progress import Progress

    if (
        not isinstance(evidence, dict)
        or evidence.get("schema_version") != EVIDENCE_SCHEMA
    ):
        raise ArchiveError(
            f"unsupported post-hoc evidence schema (expected {EVIDENCE_SCHEMA})"
        )
    registry = load_registry(config, book.key)
    record = registry["anomalies"].get(aid)
    if record is None:
        raise ArchiveError(f"unknown anomaly: {aid}")
    cache = _evidence_refs(evidence)
    completion = evidence.get("completion")
    if not isinstance(completion, dict) or not isinstance(completion.get("step"), str):
        raise ArchiveError("post-hoc evidence requires completion.step")
    details = completion.get("details") or {}
    if not isinstance(details, dict):
        raise ArchiveError("post-hoc completion.details must be an object")

    # Replaying identical done evidence is a no-op: it must not re-register
    # follow-up compile work that compile has already consumed to done (M3).
    # Genuinely new evidence (different cache/completion/input_changed) falls
    # through and reopens the follow-up for fresh compile work.
    prior = record.get("resolution") or {}
    if (
        record.get("disposition") == "done"
        and prior.get("cache") == cache
        and prior.get("completion") == {"step": completion["step"], "details": details}
        and prior.get("input_changed") == bool(evidence.get("input_changed"))
    ):
        return record

    # Real completion first: canonical state, never the disposition alone.
    progress = Progress(book.corpus, book.key)
    progress.step(record["document_id"], completion["step"], "completed", **details)
    if evidence.get("input_changed") and completion["step"] == "ocr":
        _materialize_ocr_backfill(book, record, cache)

    followup = None
    if evidence.get("input_changed"):
        followup = register_compile_followup(
            config,
            book.key,
            document_id=record["document_id"],
            source_version=record["source_version"],
            reason=evidence.get("reason")
            or "post-hoc backfill changed the published compile input",
            inputs=cache,
            anomaly_id=aid,
        )
    now = now_iso()
    record["disposition"] = "done"
    record["resolved_at"] = now
    record["resolution"] = {
        "disposition": "done",
        "note": note,
        "recorded_at": now,
        "cache": cache,
        "completion": {"step": completion["step"], "details": details},
        "input_changed": bool(evidence.get("input_changed")),
        "followup_id": followup["id"] if followup else None,
    }
    save_registry(config, book.key, registry)
    return record
