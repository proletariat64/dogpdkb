"""capture: observe the whole book, label changes and export the selected raw.

Delegates to the existing ``knowledge_capture.run_capture`` and keeps the
``knowledge-capture-1.0`` snapshot contract unchanged. Download reuse across
batches and the durable raw store are owned by the capture layer.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dogpdkb.knowledge_capture import (
    SCHEMA_VERSION,
    CaptureError,
    _raw_validity,
    _write_json,
    run_capture,
)
from dogpdkb.sync_results import (
    archive_anomalies,
    load_followups,
    load_registry,
    results_root,
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
    A ``done`` (resolved) or ``abandoned`` (given-up) record is already disposed:
    it stays suppressed (no re-export) but is no current failure, so it neither
    hands off nor keeps the batch partial. This mirrors the plan stage, which
    carries forward only ``pending`` suppressions.
    """
    rows = snapshot.get("suppressed") or []
    if not rows:
        return result
    registry = load_registry(context.config, context.book.key)
    known = {row.get("id") for row in result.anomalies}
    for row in rows:
        record = registry["anomalies"].get(row["anomaly_id"])
        if record is None or record.get("disposition") in {"done", "abandoned"}:
            continue
        if record["id"] not in known:
            result.anomalies.append(record)
            known.add(record["id"])
    if result.anomalies and result.status == "completed":
        result.status = "partial"
        result.reason = result.reason or "archived anomalies handed off"
    return result


def _revalidate_reused_raws(
    context: StageContext, snapshot: dict[str, Any], snapshot_path: Path
) -> dict[str, Any]:
    """Re-check each reused captured raw before handing the observation downstream.

    A crash-window reuse must not freeze enriched/unreadable Markdown as verified
    raw (spec:80-82, issue-02 #1/#3). The existing ``_raw_validity`` markers decide
    (no new fingerprint): an enriched/invalid raw becomes a visible
    ``CAPTURE_RAW_UNAVAILABLE`` anomaly (partial), the same shape mdcompare uses
    for a missing raw. A merely missing raw keeps today's downstream availability
    path. The snapshot FILE is rewritten so mdcompare reads the same verdict.
    """
    exports = snapshot.get("exports") or {}
    documents = snapshot.get("documents") or {}
    anomalies = snapshot.setdefault("anomalies", [])
    known = {
        (row.get("id"), row.get("code")) for row in anomalies if isinstance(row, dict)
    }
    invalidated = 0
    for doc_id, export in exports.items():
        if not isinstance(export, dict) or export.get("status") != "captured":
            continue
        raw = export.get("raw") or (documents.get(doc_id) or {}).get("raw") or {}
        reference = raw.get("path")
        if not isinstance(reference, str) or not reference:
            continue
        reason = _raw_validity((snapshot_path.parent / reference).resolve())
        if reason not in {"raw_enriched", "raw_invalid"}:
            continue
        numeric_id = int(doc_id)
        export["status"] = "failed"
        export["reused"] = False
        export["error"] = reason
        if (numeric_id, "CAPTURE_RAW_UNAVAILABLE") not in known:
            anomalies.append({"id": numeric_id, "code": "CAPTURE_RAW_UNAVAILABLE"})
            known.add((numeric_id, "CAPTURE_RAW_UNAVAILABLE"))
        invalidated += 1
        context.log.write(f"reused raw unavailable document={doc_id} reason={reason}")
    if invalidated:
        counts = snapshot.get("counts") or {}
        counts["captured"] = sum(
            1
            for row in exports.values()
            if isinstance(row, dict) and row.get("status") == "captured"
        )
        counts["failed"] = sum(
            1
            for row in exports.values()
            if isinstance(row, dict) and row.get("status") == "failed"
        )
        counts["anomalies"] = len(anomalies)
        snapshot["counts"] = counts
        snapshot["status"] = "incomplete"
        _write_json(snapshot_path, snapshot)
    return snapshot


def capture_evidence_dir(config: Any, book_key: str, run_id: str) -> Path:
    """Durable capture-time raw evidence, outside wash (spec:130-132).

    Keyed by run so a resumed batch compares against its own captured bytes.
    Located via the durable store's public root; never cleaned with wash.
    """
    return results_root(config, book_key) / "capture-evidence" / run_id


def _persist_capture_raw(source: Path, target: Path) -> None:
    """Atomically copy raw bytes once; a conflicting durable copy is never overwritten."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = source.read_bytes()
    if target.is_file():
        if target.read_bytes() != data:
            raise ValueError(f"capture evidence conflict: {target.name}")
        return
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
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


def _persist_capture_evidence(context: StageContext, snapshot: dict[str, Any]) -> None:
    """Keep a durable byte copy of each captured raw so resume can detect tampering.

    Best-effort (like anomaly archiving): a store failure is logged, never fatal;
    resume integrity checking then degrades to no comparison for that document.
    Direct bytes, no fingerprint (spec:82).
    """
    exports = snapshot.get("exports") or {}
    documents = snapshot.get("documents") or {}
    snapshot_path = context.directory / "snapshot.json"
    evidence_dir = capture_evidence_dir(
        context.config, context.book.key, context.layout.run_id
    )
    for doc_id, export in exports.items():
        if not isinstance(export, dict) or export.get("status") != "captured":
            continue
        raw = export.get("raw") or (documents.get(doc_id) or {}).get("raw") or {}
        reference = raw.get("path")
        if not isinstance(reference, str) or not reference:
            continue
        source = (snapshot_path.parent / reference).resolve()
        if not source.is_file():
            continue
        try:
            _persist_capture_raw(source, evidence_dir / source.name)
        except (OSError, ValueError) as error:
            context.log.write(
                f"capture evidence persistence failed: {safe_diagnostic(error)}"
            )


def handle(context: StageContext) -> StageResult:
    snapshot_path = context.directory / "snapshot.json"
    existing = _snapshot(snapshot_path)
    # An export-partial ("incomplete") snapshot still froze a complete listing,
    # selection and per-doc versions. Reuse that observation on resume rather than
    # refetching the remote and mixing new input into the old batch; only a
    # snapshot whose listing itself never completed is re-observed.
    listing_complete = (
        existing is not None and (existing.get("listing") or {}).get("complete") is True
    )
    if existing is not None and (
        existing.get("status") in COMPLETE or listing_complete
    ):
        # A finished observation is never re-exported; resume reuses its evidence.
        context.log.write(f"reusing complete snapshot {snapshot_path}")
        existing = _revalidate_reused_raws(context, existing, snapshot_path)
        _persist_capture_evidence(context, existing)
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
        pending_followups=[
            {
                "document_id": row["document_id"],
                "version_stamp": row["version_stamp"],
            }
            for row in load_followups(context.config, context.book.key)[
                "followups"
            ].values()
            if row.get("status") == "pending"
        ],
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
    _persist_capture_evidence(context, snapshot)
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
