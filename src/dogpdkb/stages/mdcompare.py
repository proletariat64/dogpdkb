"""mdcompare: whole-batch offline comparison, completed before plan begins.

Reuses the existing comparator and its body/OCR scope without re-implementing
any comparison algorithm:

* body knowledge via ``mdcompare.knowledge.compare_knowledge_documents``
* text / resources / metadata / OCR via ``mdcompare.comparison.compare_documents``

Resource declarations are derived from the captured raw markdown so the OCR
dimension is exercised (the legacy inline planner passed ``resources=[]``). The
stage publishes one per-document comparison report; the plan stage only consumes
it and never re-compares or calls OCR/Graphify. No network or paid service is
used here: image bytes are read only when already present locally.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from dogpdkb.knowledge_plan import (
    PlanError,
    _compare,
    _document,
    _document_id,
    _freeze_raw,
    _graph_document,
    _load_snapshot,
    _load_state,
    _relative_raw,
    _resolve_raw,
    state_lock,
)
from dogpdkb.sync_stages import StageContext, StageResult
from dogpdkb.sync_time import parse_timestamp
from dogpdkb.workflows.get_sync_updates import safe_diagnostic
from mdcompare.comparison import compare_documents
from mdcompare.io import InputReader, publish_reports, validate_outputs
from mdcompare.model import CompareOptions, Document
from mdcompare.resources import _uses

REPORT_SCHEMA = "unified-mdcompare-1.0"

#: Capture reasons that mean the CURRENT complete raw is unavailable. They are
#: handed to the plan stage, which archives an anomaly rather than fabricating
#: input or silently compiling a partial source. A missing OLD input is distinct:
#: the current raw can still be fully compiled, so it is not listed here.
INPUT_UNAVAILABLE = {
    "CAPTURE_EXPORT_FAILED",
    "CAPTURE_RAW_UNAVAILABLE",
}


def _resource_catalog(data: bytes, base_dir: Path) -> list[dict[str, Any]]:
    """Declare every resource the raw markdown uses, reusing the comparator parser.

    The resource identity is the reference URL itself (existing identity, no new
    fingerprint). Local bytes are attached only when already present; remote
    images stay byte-less so the comparator decides reuse from S3 version
    evidence or marks them for OCR. The OCR stage owns downloading/preparing.
    """
    uses, _reasons = _uses(data, {})
    catalog: dict[str, dict[str, Any]] = {}
    for use in uses:
        url = use["url"]
        if url in catalog:
            # A plain link (attachment use) must not shadow a later image embed
            # of the same URL: upgrade the kind so the image still reaches OCR.
            # The resource identity stays the URL, so OCR evidence and removed-
            # contribution detection are unchanged ([22]).
            if use["kind"] == "image":
                catalog[url]["kind"] = "image"
            continue
        entry: dict[str, Any] = {
            "resource_id": url,
            "url": url,
            "kind": use["kind"],
        }
        parts = urlsplit(url)
        if not parts.scheme and not url.startswith("//"):
            local = unquote(parts.path)
            if local:
                candidate = (base_dir / local).resolve()
                if candidate.is_file():
                    entry["path"] = str(candidate)
        catalog[url] = entry
    return [catalog[key] for key in sorted(catalog)]


def _incoming(
    document: dict[str, Any],
    book_id: int,
    frozen: Path,
    reader: InputReader,
    base_dir: Path,
) -> Document:
    base = _document(document, book_id, frozen)
    data = reader.read_bytes(frozen)
    # Freezing copies only the markdown, not sibling resources: resolve relative
    # images against the original raw-export dir so their bytes stay available.
    return replace(base, resources=_resource_catalog(data, base_dir))


def _baseline(
    doc_id: str,
    graph: dict[str, Any],
    book_id: int,
    state_path: Path,
    frozen_old: Path | None,
    reader: InputReader,
) -> Document | None:
    old = _graph_document(doc_id, graph, book_id, state_path)
    if old is None:
        return None
    base_dir = old.base_dir
    if frozen_old is not None:
        old = replace(old, path=frozen_old, base_dir=frozen_old.parent)
    data = reader.read_bytes(old.path)
    evidence = graph.get("ocr") if isinstance(graph.get("ocr"), dict) else None
    return replace(old, resources=_resource_catalog(data, base_dir), ocr=evidence)


def _capture_anomalies(
    snapshot: dict[str, Any], doc_id: str, numeric_id: int, export: dict[str, Any]
) -> list[str]:
    codes: list[str] = []
    anomalies = snapshot.get("anomalies") or []
    if isinstance(anomalies, list):
        for row in anomalies:
            if isinstance(row, dict) and row.get("id") == numeric_id:
                codes.append(str(row.get("code") or "CAPTURE_ANOMALY"))
    if export.get("status") == "failed":
        codes.append("CAPTURE_EXPORT_FAILED")
    return codes


def _compare_document(
    old: Document | None, new: Document, reader: InputReader
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = _compare(old, new)
    full = compare_documents(
        [old] if old is not None else [], [new], CompareOptions(), reader
    )
    if len(full["results"]) != 1:
        raise PlanError("comparison did not conserve the selected document identity")
    return body, full["results"][0]


def _rows(context: StageContext, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot_path = context.require("capture", "snapshot")
    book_id = snapshot["book"]["book_id"]
    state_path = context.corpus / ".sync-state.json"
    staging = context.directory
    report_path = staging / "report.json"
    state = _load_state(state_path, snapshot["book"])
    exports = snapshot.get("exports") or {}
    selected = {_document_id(value) for value in snapshot.get("selected") or []}
    changes = snapshot.get("changes") or {}
    reader = InputReader(CompareOptions.max_file_bytes)
    rows: list[dict[str, Any]] = []
    for doc_id, document in sorted(
        snapshot["documents"].items(), key=lambda item: _document_id(item[0])
    ):
        numeric_id = _document_id(doc_id)
        previous = state["documents"].get(doc_id) or {}
        graph = previous.get("graph") or {
            "status": "uncovered",
            "version": None,
            "raw": None,
        }
        labels = sorted(
            label
            for label in ("new", "updated", "renamed", "moved")
            if numeric_id in (changes.get(label) or [])
        )
        export = exports.get(doc_id) or {}
        raw_reference = document.get("raw") or export.get("raw")
        raw_path = _resolve_raw(raw_reference, snapshot_path)
        capture_anomalies = _capture_anomalies(snapshot, doc_id, numeric_id, export)
        try:
            raw_missing = raw_path is not None and not raw_path.is_file()
        except OSError:
            raw_missing = True
        if raw_missing:
            capture_anomalies.append("CAPTURE_RAW_UNAVAILABLE")
        graph_version = graph.get("version")
        graph_needs_comparison = graph.get("status") == "covered" and (
            not isinstance(graph_version, dict)
            or parse_timestamp(graph_version["utc"])
            != parse_timestamp(document["version"]["utc"])
        )
        previous_pending = [
            reason
            for reason in (previous.get("pending") or [])
            if reason != "GRAPH_SOURCE_NOT_REGISTERED"
        ]
        # Capture's selection is the batch scope. An unselected document (e.g.
        # deferred by --max-select) is only rechecked when its raw is actually
        # available; without input it is deferred to a later batch, never an
        # unavailable-input anomaly (plan: a healthy unselected doc is NO_WORK).
        work = numeric_id in selected or (
            raw_path is not None
            and (
                bool(previous_pending)
                or graph_needs_comparison
                or graph.get("status") != "covered"
            )
        )
        row: dict[str, Any] = {
            "book_id": book_id,
            "document_id": numeric_id,
            "labels": labels,
            "selected": numeric_id in selected,
            "source_version": document["version"],
            "graph_status": graph.get("status"),
            "previous_graph_version": graph_version,
            "input_available": False,
            "raw": None,
            "previous_raw": None,
            "previous_kind": None,
            "metadata": None,
            "body": None,
            "comparison": None,
            "resources": [],
            "reason_codes": sorted(set(capture_anomalies)),
            "anomaly": None,
        }
        unavailable = [c for c in capture_anomalies if c in INPUT_UNAVAILABLE]
        if not work:
            row["reason_codes"] = sorted(set(row["reason_codes"]) | {"NO_WORK"})
            rows.append(row)
            continue
        if unavailable or raw_path is None:
            code = (unavailable or ["CAPTURE_RAW_UNAVAILABLE"])[0]
            row["anomaly"] = {"code": code, "reason": code}
            rows.append(row)
            continue
        old_initial = _graph_document(doc_id, graph, book_id, state_path)
        previous_missing = graph.get("status") == "covered" and old_initial is None
        protected = {snapshot_path, report_path, raw_path}
        try:
            frozen = _freeze_raw(
                raw_path, staging, book_id, numeric_id, protected, state_path
            )
        except OSError as error:
            row["anomaly"] = {
                "code": "CAPTURE_RAW_UNAVAILABLE",
                "reason": safe_diagnostic(error),
            }
            rows.append(row)
            continue
        frozen_old = None
        if old_initial is not None:
            try:
                frozen_old = _freeze_raw(
                    old_initial.path,
                    staging,
                    book_id,
                    numeric_id,
                    protected,
                    state_path,
                    previous=True,
                )
            except OSError:
                # The old input is gone but the current raw is complete: the plan
                # fully recompiles this source rather than holding or fabricating.
                previous_missing = True
        new = _incoming(document, book_id, frozen, reader, raw_path.parent)
        old = (
            _baseline(doc_id, graph, book_id, state_path, frozen_old, reader)
            if frozen_old is not None
            else None
        )
        body, comparison = _compare_document(old, new, reader)
        extra = {"PREVIOUS_INPUT_MISSING"} if previous_missing else set()
        row.update(
            input_available=True,
            raw=_relative_raw(frozen, report_path),
            previous_raw=_relative_raw(frozen_old, report_path)
            if frozen_old is not None
            else None,
            previous_kind=old.content_kind if old is not None else None,
            metadata=new.metadata,
            body=body,
            comparison=comparison,
            resources=new.resources,
            reason_codes=sorted(
                set(row["reason_codes"])
                | set(comparison.get("reason_codes") or [])
                | extra
            ),
        )
        rows.append(row)
    return rows


def handle(context: StageContext) -> StageResult:
    snapshot_path = context.require("capture", "snapshot")
    snapshot = _load_snapshot(snapshot_path)
    state_path = context.corpus / ".sync-state.json"
    staging = context.directory
    report_path = staging / "report.json"
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    with state_lock(state_path):
        rows = _rows(context, snapshot)
    protected = {snapshot_path, state_path}
    for row in rows:
        for key in ("raw", "previous_raw"):
            reference = row.get(key)
            if isinstance(reference, dict):
                resolved = _resolve_raw(reference, report_path)
                if resolved is not None:
                    protected.add(resolved)
    validate_outputs([report_path], protected)
    compared = sum(1 for row in rows if row["input_available"])
    unavailable = sum(1 for row in rows if row["anomaly"] is not None)
    report = {
        "schema_version": REPORT_SCHEMA,
        "book": snapshot["book"],
        "observation": snapshot["observation"],
        "snapshot_status": snapshot["status"],
        "documents": rows,
        "counts": {
            "documents": len(rows),
            "compared": compared,
            "input_unavailable": unavailable,
            "external_calls": 0,
        },
    }
    publish_reports(
        [(report_path, json.dumps(report, ensure_ascii=False, indent=1) + "\n")]
    )
    context.count(documents=len(rows), compared=compared, input_unavailable=unavailable)
    status = "completed"
    if not rows:
        status = "skipped"
    elif unavailable:
        status = "partial"
    return StageResult(
        status=status,
        counts=report["counts"],
        artifacts={"report": context.layout.relative(report_path)},
        inputs={"snapshot": context.layout.relative(snapshot_path)},
        reason=None if status == "completed" else snapshot["status"],
    )


HANDLER = handle
