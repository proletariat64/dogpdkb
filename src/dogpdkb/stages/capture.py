"""capture: observe the whole book, label changes and export the selected raw.

Delegates to the existing ``knowledge_capture.run_capture`` and keeps the
``knowledge-capture-1.0`` snapshot contract unchanged. Download reuse across
batches and the durable raw store are owned by ticket 02.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dogpdkb.knowledge_capture import SCHEMA_VERSION, CaptureError, run_capture
from dogpdkb.sync_results import (
    archive_anomalies,
    load_registry,
    suppressing_anomalies,
)
from dogpdkb.sync_run import RunError, read_json
from dogpdkb.sync_stages import StageContext, StageFailed, StageResult
from dogpdkb.sync_state import StateError
from dogpdkb.workflows.get_sync_updates import safe_diagnostic

COMPLETE = ("initialized", "captured")


def _necessary_inputs(
    context: StageContext, exports: dict[str, Any], doc_id: Any
) -> list[dict[str, str]]:
    """The raw export a post-hoc agent needs, referenced for durable persistence."""
    raw = (exports.get(str(doc_id)) or {}).get("raw") or {}
    reference = raw.get("path")
    if not isinstance(reference, str) or not reference:
        return []
    path = (context.directory / reference).resolve()
    return [{"kind": "raw", "path": str(path)}] if path.is_file() else []


def _snapshot(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise StageFailed(f"capture snapshot has an unsupported schema: {path}")
    return payload


def _baseline(context: StageContext) -> Path | None:
    """The last complete observation recorded for this book in canonical state."""
    steps = context.progress.book.get("steps") or {}
    reference = (steps.get("capture") or {}).get("snapshot")
    if not isinstance(reference, str) or not reference:
        return None
    path = Path(reference)
    try:
        payload = _snapshot(path)
    except (RunError, StageFailed):
        payload = None
    if (
        payload is None
        or payload.get("status") not in COMPLETE
        or (payload.get("listing") or {}).get("complete") is not True
    ):
        context.log.write(f"baseline snapshot unavailable: {reference}")
        return None
    return path


def _result(context: StageContext, snapshot: dict[str, Any]) -> StageResult:
    counts = snapshot["counts"]
    exports = snapshot.get("exports") or {}
    documents = snapshot.get("documents") or {}
    reused = sum(1 for row in exports.values() if row.get("reused"))
    book_id = (snapshot.get("book") or {}).get("book_id")
    anomalies = [
        {
            "stage": "capture",
            "book_id": book_id,
            "document_id": row["id"],
            "code": row["code"],
            "reason": row["code"],
            "source_version": (documents.get(str(row["id"])) or {}).get("version"),
            "necessary_inputs": _necessary_inputs(context, exports, row["id"]),
        }
        for row in snapshot.get("anomalies") or []
    ]
    artifacts = {
        "snapshot": context.layout.relative(context.directory / "snapshot.json")
    }
    checkpoint = context.directory / "staging" / "capture-checkpoint.json"
    if checkpoint.is_file():
        artifacts["checkpoint"] = context.layout.relative(checkpoint)
    raw = context.directory / "staging" / "raw"
    if raw.is_dir():
        artifacts["raw_dir"] = context.layout.relative(raw)
    if counts["documents"] == 0:
        status = "skipped"
    elif anomalies:
        status = "partial"
    else:
        status = "completed"
    return StageResult(
        status=status,
        counts={
            "total": counts["selected"],
            "completed": counts["captured"],
            "reused": reused,
            "failed": counts["failed"],
            "pending": counts["pending"],
            "documents": counts["documents"],
        },
        artifacts=artifacts,
        anomalies=anomalies,
        reason=None if status == "completed" else snapshot["status"],
    )


def _archived(context: StageContext, result: StageResult) -> StageResult:
    """Persist anomalies + necessary inputs outside wash before the envelope.

    Archiving is best-effort: if the durable store cannot be written, the failure
    is logged, the original anomalies stay in the envelope and the necessary
    inputs remain readable in the run directory. A valid corpus is never broken
    and no input is lost because archiving failed.
    """
    if result.anomalies:
        try:
            result.anomalies = archive_anomalies(context, result.anomalies)
        except (OSError, ValueError) as error:
            context.log.write(f"anomaly archiving failed: {safe_diagnostic(error)}")
    return result


def _handed_off(
    context: StageContext, snapshot: dict[str, Any], result: StageResult
) -> StageResult:
    """Reference suppressed anomalies without re-attempting or re-registering them.

    The daily batch still reports them (partial, never a fabricated completed) so
    a handed-off failure stays visible until the post-hoc agent disposes of it.
    """
    rows = snapshot.get("suppressed") or []
    if not rows:
        return result
    registry = load_registry(context.config, context.book.key)
    known = {row.get("id") for row in result.anomalies}
    for row in rows:
        record = registry["anomalies"].get(row["anomaly_id"])
        if record is not None and record["id"] not in known:
            result.anomalies.append(record)
            known.add(record["id"])
    if result.anomalies and result.status == "completed":
        result.status = "partial"
        result.reason = result.reason or "archived anomalies handed off"
    return result


def handle(context: StageContext) -> StageResult:
    snapshot_path = context.directory / "snapshot.json"
    existing = _snapshot(snapshot_path)
    if existing is not None and existing.get("status") in COMPLETE:
        # A finished observation is never re-exported; resume reuses its evidence.
        context.log.write(f"reusing complete snapshot {snapshot_path}")
        result = _archived(context, _result(context, existing))
        return _handed_off(context, existing, result)
    baseline = _baseline(context)
    arguments = SimpleNamespace(
        snapshot=snapshot_path,
        staging=context.directory / "staging",
        listing=None,
        details_dir=None,
        baseline_snapshot=baseline,
        pending_state=None,
        skylark_url=context.book.url,
        sync_source=context.book,
        sync_corpus=context.corpus,
        sync_config=context.config,
        suppressed_anomalies=suppressing_anomalies(
            context.config, context.book.key, stage="capture"
        ),
    )
    context.log.write(
        f"capture started book={context.book.key} baseline={baseline or 'none'}"
    )
    try:
        run_capture(arguments)
    except (CaptureError, StateError, OSError) as error:
        # An incomplete listing cannot establish this batch's scope at all.
        raise StageFailed(f"capture failed: {error}") from error
    snapshot = _snapshot(snapshot_path)
    if snapshot is None:
        raise StageFailed("capture produced no snapshot")
    result = _result(context, snapshot)
    if snapshot.get("status") in COMPLETE:
        context.progress.phase(
            "capture",
            result.status,
            snapshot=str(snapshot_path),
            observation_status=snapshot["status"],
        )
    context.count(**result.counts)
    result = _archived(context, result)
    return _handed_off(context, snapshot, result)


HANDLER = handle
