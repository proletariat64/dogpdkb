"""Verify captured original-to-prepared-to-OCR evidence without executing OCR."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import InputError, InputReader
from .model import Document

CONTRACT = "mdocr-ocr-2"


def _outcome(status: str, reason: str) -> dict[str, Any]:
    return {"status": status, "reason_codes": [reason]}


def _settings(value: Any) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return None


def _records(document: Document) -> dict[str, dict[str, Any]] | None:
    evidence = document.ocr
    if not evidence or evidence.get("contract") != CONTRACT:
        return None
    images = evidence.get("images")
    if not isinstance(images, list):
        return None
    if len(images) > 10_000:
        raise InputError("OCR_RECORD_BUDGET_EXCEEDED")
    result = {}
    for record in images:
        if not isinstance(record, dict):
            return None
        identifier = record.get("resource_id")
        if not isinstance(identifier, str) or not identifier or identifier in result:
            return None
        result[identifier] = record
    return result


@dataclass(frozen=True)
class _ReuseInputs:
    old_records: dict[str, dict[str, Any]] | None
    new_records: dict[str, dict[str, Any]] | None
    old_resources: dict[str, dict[str, Any]]
    new_resources: dict[str, dict[str, Any]]


def _resource_index(document: Document) -> dict[str, dict[str, Any]]:
    result = {}
    for entry in document.resources:
        identifier = entry.get("resource_id")
        if isinstance(identifier, str):
            result.setdefault(identifier, entry)
    return result


def _reuse_inputs(old: Document, new: Document) -> _ReuseInputs:
    return _ReuseInputs(
        _records(old), _records(new), _resource_index(old), _resource_index(new)
    )


def _path(document: Document, record: dict[str, Any], key: str) -> Path | None:
    value = record.get(key)
    return document.resolve(value) if isinstance(value, str) and value else None


def _v2_record(document, record, resource, reader):
    source = _path(document, record, "source_path")
    original = _path(document, resource, "path")
    artifact = _path(document, record, "artifact")
    if not source or not original or not artifact:
        return None, _outcome("UNKNOWN", "OCR_SOURCE_MAPPING_INCOMPLETE")
    data = reader.read_bytes(source)
    digest = hashlib.sha256(data).hexdigest()
    if digest != record.get("source_sha256") or data != reader.read_bytes(original):
        return None, _outcome("UNKNOWN", "OCR_SOURCE_MAPPING_MISMATCH")
    block = reader.read_bytes(artifact)
    if hashlib.sha256(block).hexdigest() != record.get("artifact_sha256"):
        return None, _outcome("CHANGED", "OCR_ARTIFACT_CHANGED")
    mode = document.ocr["configuration"].get("mode")
    header = re.compile(
        rb"<!-- mdocr:ocr-begin v=2 id=[a-zA-Z0-9-]+ -->\n"
        rb"<!-- mdocr:meta mode=(paddle|llm) sha256=([a-f0-9]{64}) -->\n"
        rb"> Original image: \[source\]\(<[^<>\r\n]+>\)\n\n"
    ).match(block)
    end = b"\n<!-- mdocr:ocr-end -->"
    if (
        header is None
        or header[1].decode() != mode
        or header[2].decode() != digest
        or not block.endswith(end)
        or not block[header.end() : -len(end)].strip()
        or re.search(rb"<!--\s*mdocr:", block[header.end() : -len(end)], re.IGNORECASE)
    ):
        return None, _outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_INVALID")
    return digest, None


def _verify_v2(old, new, identifier, previous, desired, reader, inputs):
    before = old.ocr.get("configuration")
    after = new.ocr.get("configuration")
    if _settings(before) is None or _settings(after) is None:
        return _outcome("UNKNOWN", "OCR_CONFIGURATION_UNVERIFIED")
    if _settings(before) != _settings(after):
        return _outcome("CHANGED", "OCR_CONFIGURATION_INCOMPATIBLE")
    if before.get("mode") not in {"paddle", "llm"}:
        return _outcome("UNKNOWN", "OCR_CONFIGURATION_UNVERIFIED")
    if before["mode"] == "llm":
        old_context, new_context = (
            old.ocr.get("context_sha256"),
            new.ocr.get("context_sha256"),
        )
        if not all(
            isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v)
            for v in (old_context, new_context)
        ):
            return _outcome("UNKNOWN", "OCR_CONTEXT_UNVERIFIED")
        if old_context != new_context:
            return _outcome("CHANGED", "OCR_CONTEXT_CHANGED")
    left, issue = _v2_record(
        old, previous, inputs.old_resources.get(identifier, {}), reader
    )
    if issue:
        return issue
    right, issue = _v2_record(
        new, desired, inputs.new_resources.get(identifier, {}), reader
    )
    if issue:
        return issue
    if left != right:
        return _outcome("CHANGED", "OCR_SOURCE_MAPPING_MISMATCH")
    return _outcome("UNCHANGED", "OCR_REUSE_VERIFIED")


def _verify(
    old: Document,
    new: Document,
    identifier: str,
    reader: InputReader,
    inputs: _ReuseInputs,
) -> dict[str, Any]:
    if inputs.old_records is None or inputs.new_records is None:
        return _outcome("UNKNOWN", "OCR_PROVENANCE_INCOMPLETE")
    previous = inputs.old_records.get(identifier)
    desired = inputs.new_records.get(identifier)
    if previous is None:
        return _outcome("CHANGED", "OCR_SUCCESS_RECORD_MISSING")
    if previous.get("status") in ("failed", "pending"):
        return _outcome("CHANGED", "OCR_SUCCESS_RECORD_UNSUCCESSFUL")
    if previous.get("status") != "success" or desired is None:
        return _outcome("UNKNOWN", "OCR_PROVENANCE_INCOMPLETE")
    assert old.ocr is not None and new.ocr is not None
    return _verify_v2(old, new, identifier, previous, desired, reader, inputs)


def _removed_contributions(old: Document | None, new: Document) -> list[str]:
    """Prior OCR successes whose original image is absent from the incoming raw.

    Compile replaces an image embed with its OCR block, so a removed image surfaces
    as a dropped OCR contribution rather than a removed embed. Track the original
    resource identity instead of parsing Markdown links so the
    detection survives the raw-to-published representation change.
    """
    if old is None:
        return []
    try:
        records = _records(old)
    except InputError:
        return []
    if records is None:
        return []
    incoming = {entry.get("resource_id") for entry in new.resources}
    return sorted(
        identifier
        for identifier, record in records.items()
        if record.get("status") == "success" and identifier not in incoming
    )


def compare_ocr(
    old: Document | None,
    new: Document,
    resources: dict[str, Any],
    reader: InputReader,
) -> dict[str, Any]:
    items = []
    inputs = None
    inputs_error = None
    removed_contributions = _removed_contributions(old, new)
    for resource in resources.get("items", []):
        if resource.get("kind") != "image" or resource.get("operation") == "removed":
            continue
        identifier = resource["resource_id"]
        if resource["status"] in ("NEW", "CHANGED") or old is None:
            outcome = _outcome("NEW", "OCR_PROCESS_RESOURCE")
        elif resource["status"] == "UNKNOWN":
            outcome = _outcome("UNKNOWN", "OCR_RESOURCE_UNVERIFIED")
        elif old.ocr is None:
            outcome = _outcome("CHANGED", "OCR_SUCCESS_RECORD_MISSING")
        else:
            # Index only when an unchanged image needs reuse proof; keep a
            # document-wide mapping failure from triggering repeated scans.
            if inputs is None and inputs_error is None:
                try:
                    inputs = _reuse_inputs(old, new)
                except InputError as exc:
                    inputs_error = exc.reason_code
            if inputs_error is not None:
                outcome = _outcome("UNKNOWN", inputs_error)
            else:
                assert inputs is not None
                try:
                    outcome = _verify(old, new, identifier, reader, inputs)
                except InputError as exc:
                    outcome = _outcome("UNKNOWN", exc.reason_code)
        items.append({"resource_id": identifier, **outcome})
    statuses = {item["status"] for item in items}
    if resources["status"] == "UNKNOWN":
        statuses.add("UNKNOWN")
    status = (
        "UNKNOWN"
        if "UNKNOWN" in statuses
        else "CHANGED"
        if statuses & {"NEW", "CHANGED"}
        else "UNCHANGED"
        if items
        else "NOT_APPLICABLE"
    )
    reasons = {reason for item in items for reason in item["reason_codes"]}
    if resources["status"] == "UNKNOWN":
        reasons.add("OCR_RESOURCE_UNVERIFIED")
    return {
        "status": status,
        "reason_codes": sorted(reasons),
        "items": items,
        "reusable": [
            item["resource_id"] for item in items if item["status"] == "UNCHANGED"
        ],
        "processing_required": [
            item["resource_id"]
            for item in items
            if item["status"] in ("NEW", "CHANGED")
        ],
        "removed_contributions": removed_contributions,
    }
