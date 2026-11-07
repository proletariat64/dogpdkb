"""Pair identities and combine independent comparison dimensions."""

from collections import Counter
from typing import Any

from .decisions import choose_action
from .history import compare_history
from .io import InputError, InputReader
from .model import ACTIONS, SCHEMA_VERSION, CompareOptions, Document
from .ocr import compare_ocr
from .resources import compare_resources
from .text import compare_text


def _record_error(
    errors: list[dict[str, str]],
    document: Document,
    exc: InputError,
    dimension: dict[str, Any],
) -> None:
    dimension.update(status="UNKNOWN", reason_codes=[exc.reason_code])
    errors.append(
        {
            "knowledge_base": document.knowledge_base,
            "document_id": document.document_id,
            "reason_code": exc.reason_code,
        }
    )


def compare_documents(
    baseline: list[Document],
    incoming: list[Document],
    options: CompareOptions,
    reader: InputReader,
) -> dict[str, Any]:
    old_by_id = {d.identity: d for d in baseline}
    new_ids = {d.identity for d in incoming}
    results = []
    errors = []
    for new in sorted(incoming, key=lambda d: d.identity):
        reader.begin_document(new.identity)
        old = old_by_id.get(new.identity)
        changed = [
            k
            for k in ("name", "logical_path", "source_ref")
            if old and old.metadata[k] != new.metadata[k]
        ]
        metadata = {
            "status": "NEW" if old is None else "CHANGED" if changed else "UNCHANGED",
            "changed_fields": changed,
            "sync_timestamp_changed": bool(
                old
                and old.metadata.get("sync_timestamp")
                != new.metadata.get("sync_timestamp")
            ),
        }
        row = {
            "knowledge_base": new.knowledge_base,
            "document_id": new.document_id,
            "metadata": metadata,
        }
        text = {"status": "UNKNOWN", "reason_codes": [], "differences": []}
        resources = {"status": "UNKNOWN", "reason_codes": [], "items": []}
        ocr = {"status": "UNKNOWN", "reason_codes": []}

        try:
            new_bytes = reader.read_bytes(new.path)
            old_bytes = reader.read_bytes(old.path) if old else None
        except InputError as exc:
            _record_error(errors, new, exc, text)
        else:
            try:
                history = compare_history(
                    old, new, old_bytes, new_bytes, reader, options
                )
                text = (
                    compare_text(old_bytes, new_bytes, options)
                    if history is None
                    else history
                )
            except InputError as exc:
                _record_error(errors, new, exc, text)
            try:
                resources = compare_resources(
                    old, new, old_bytes, new_bytes, reader, options
                )
            except InputError as exc:
                _record_error(errors, new, exc, resources)
            try:
                ocr = compare_ocr(old, new, resources, reader, options)
            except InputError as exc:
                _record_error(errors, new, exc, ocr)
        action = choose_action(old is not None, text, resources, metadata, ocr)
        row.update(
            text=text,
            resources=resources,
            ocr=ocr,
            action=action,
            reason_codes=sorted(
                {
                    reason
                    for d in (text, resources, ocr)
                    for reason in d.get("reason_codes", [])
                }
            ),
        )
        results.append(row)
    affected = reader.affected_documents(reader.changed_paths())
    for row in results:
        if (row["knowledge_base"], row["document_id"]) in affected:
            row["action"] = "UNKNOWN"
            row["reason_codes"] = sorted(set(row["reason_codes"]) | {"INPUT_CHANGED"})
            errors.append(
                {
                    "knowledge_base": row["knowledge_base"],
                    "document_id": row["document_id"],
                    "reason_code": "INPUT_CHANGED",
                }
            )
    counts = Counter(r["action"] for r in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "rules": [{"name": "identity", "version": "1"}],
        "results": results,
        "baseline_only": [
            {"knowledge_base": d.knowledge_base, "document_id": d.document_id}
            for d in baseline
            if d.identity not in new_ids
        ],
        "summary": {"total": len(results), "actions": {a: counts[a] for a in ACTIONS}},
        "errors": [
            {"knowledge_base": book, "document_id": identifier, "reason_code": reason}
            for book, identifier, reason in sorted(
                {
                    (
                        error["knowledge_base"],
                        error["document_id"],
                        error["reason_code"],
                    )
                    for error in errors
                }
            )
        ],
    }
