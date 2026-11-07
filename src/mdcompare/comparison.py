"""Pair identities and combine independent comparison dimensions."""

from collections import Counter
from typing import Any

from .decisions import choose_action
from .history import compare_history
from .io import InputError, InputReader
from .model import ACTIONS, SCHEMA_VERSION, CompareOptions, Document
from .ocr import compare_ocr
from .resources import compare_resources
from .text import compare_text, text_rules


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
    for new in incoming:
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
        try:
            new_bytes = reader.read_bytes(new.path)
            old_bytes = reader.read_bytes(old.path) if old else None
            history = compare_history(old, new, old_bytes, new_bytes, reader, options)
            if history is None:
                text = compare_text(old_bytes, new_bytes, options)
            else:
                text = history.text
                old_bytes, new_bytes = history.old_bytes, history.new_bytes
            resources = compare_resources(
                old, new, old_bytes, new_bytes, reader, options
            )
            ocr = compare_ocr(old, new, resources, reader, options)
            action = choose_action(old is not None, text, resources, metadata, ocr)
        except InputError as exc:
            text = {
                "status": "UNKNOWN",
                "reason_codes": [exc.reason_code],
                "differences": [],
            }
            resources = {"status": "UNKNOWN", "reason_codes": [], "items": []}
            ocr = {"status": "UNKNOWN", "reason_codes": []}
            action = "UNKNOWN"
            errors.append(
                {
                    "knowledge_base": new.knowledge_base,
                    "document_id": new.document_id,
                    "reason_code": exc.reason_code,
                }
            )
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
    counts = Counter(r["action"] for r in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "rules": text_rules(options),
        "results": results,
        "baseline_only": [
            {"knowledge_base": d.knowledge_base, "document_id": d.document_id}
            for d in baseline
            if d.identity not in new_ids
        ],
        "summary": {"total": len(results), "actions": {a: counts[a] for a in ACTIONS}},
        "errors": errors,
    }
