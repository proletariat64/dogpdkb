"""Verify captured original-to-prepared-to-OCR evidence without executing OCR."""

import io
import json
import warnings
from pathlib import Path
from typing import Any

from PIL import Image

from .io import InputError, InputReader
from .model import CompareOptions, Document

CONTRACT = "prepared-jpeg-ocr-v1"
MAX_PREPARED_PIXELS = 20_000_000


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


def _jpeg(data: bytes) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG":
                    return False
                if image.width * image.height > MAX_PREPARED_PIXELS:
                    raise InputError("OCR_PREPARED_IMAGE_BUDGET_EXCEEDED")
                image.load()
        return True
    except InputError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InputError("OCR_PREPARED_IMAGE_BUDGET_EXCEEDED") from exc
    except (OSError, ValueError, SyntaxError):
        return False


def _path(document: Document, record: dict[str, Any], key: str) -> Path | None:
    value = record.get(key)
    return document.resolve(value) if isinstance(value, str) and value else None


def _prepared(
    document: Document, record: dict[str, Any], resource_id: str, reader: InputReader
) -> tuple[bytes | None, dict[str, Any] | None]:
    declared = next(
        (r for r in document.resources if r.get("resource_id") == resource_id), {}
    )
    source = _path(document, record, "source_path")
    original = _path(document, declared, "path")
    prepared = _path(document, record, "prepared_image")
    if source is None or original is None or prepared is None:
        return None, _outcome("UNKNOWN", "OCR_SOURCE_MAPPING_INCOMPLETE")
    if reader.read_bytes(source) != reader.read_bytes(original):
        return None, _outcome("UNKNOWN", "OCR_SOURCE_MAPPING_MISMATCH")
    data = reader.read_bytes(prepared)
    if not _jpeg(data):
        return None, _outcome("CHANGED", "OCR_PREPARED_IMAGE_INVALID")
    return data, None


def _artifact(
    document: Document, record: dict[str, Any], reader: InputReader
) -> dict[str, Any] | None:
    artifact = _path(document, record, "artifact")
    prepared = _path(document, record, "prepared_image")
    if artifact is None or prepared is None:
        return _outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_INCOMPLETE")
    try:
        artifact.stat()
    except (FileNotFoundError, NotADirectoryError):
        return _outcome("CHANGED", "OCR_ARTIFACT_UNAVAILABLE")
    except (OSError, ValueError) as exc:
        raise InputError("INPUT_READ_FAILED") from exc
    try:
        content = reader.read_bytes(artifact).decode("utf-8")
    except UnicodeDecodeError:
        return _outcome("CHANGED", "OCR_ARTIFACT_INVALID")
    prefix = '<!-- mdocr:ocr-begin source="'
    if not content.startswith(prefix) or not content.endswith("<!-- mdocr:ocr-end -->"):
        return _outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_INVALID")
    source, separator, body = content[len(prefix) :].partition('" -->\n\n')
    if not separator or not source or "\n" in source or '"' in source:
        return _outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_INVALID")
    footer = f"\n\n> Original image: [{source}]({source})\n\n<!-- mdocr:ocr-end -->"
    if not body.endswith(footer):
        return _outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_INVALID")
    try:
        matched = (artifact.parent / source).resolve() == prepared.resolve()
    except (OSError, ValueError, RuntimeError):
        return _outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_UNVERIFIED")
    if not matched:
        return _outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_MISMATCH")
    text = body[: -len(footer)]
    if not text.strip():
        return _outcome("CHANGED", "OCR_ARTIFACT_EMPTY")
    if "<!-- mdocr:" in text:
        return _outcome("UNKNOWN", "OCR_ARTIFACT_MAPPING_INVALID")
    return None


def _verify(
    old: Document, new: Document, identifier: str, reader: InputReader
) -> dict[str, Any]:
    if old.ocr is None:
        return _outcome("CHANGED", "OCR_SUCCESS_RECORD_MISSING")
    old_records, new_records = _records(old), _records(new)
    if old_records is None or new_records is None:
        return _outcome("UNKNOWN", "OCR_PROVENANCE_INCOMPLETE")
    previous, desired = old_records.get(identifier), new_records.get(identifier)
    if previous is None:
        return _outcome("CHANGED", "OCR_SUCCESS_RECORD_MISSING")
    if previous.get("status") in ("failed", "pending"):
        return _outcome("CHANGED", "OCR_SUCCESS_RECORD_UNSUCCESSFUL")
    if previous.get("status") != "success" or desired is None:
        return _outcome("UNKNOWN", "OCR_PROVENANCE_INCOMPLETE")
    assert old.ocr is not None and new.ocr is not None
    for key in ("preprocessing", "configuration"):
        before, after = _settings(old.ocr.get(key)), _settings(new.ocr.get(key))
        if before is None or after is None:
            return _outcome("UNKNOWN", "OCR_CONFIGURATION_UNVERIFIED")
        if before != after:
            return _outcome("CHANGED", "OCR_CONFIGURATION_INCOMPATIBLE")
    old_image, issue = _prepared(old, previous, identifier, reader)
    if issue is not None:
        return issue
    new_image, issue = _prepared(new, desired, identifier, reader)
    if issue is not None:
        return issue
    if old_image != new_image:
        return _outcome("CHANGED", "OCR_PREPARED_IMAGE_CHANGED")
    issue = _artifact(old, previous, reader)
    return issue or _outcome("UNCHANGED", "OCR_REUSE_VERIFIED")


def compare_ocr(
    old: Document | None,
    new: Document,
    resources: dict[str, Any],
    reader: InputReader,
    options: CompareOptions,
) -> dict[str, Any]:
    items = []
    for resource in resources.get("items", []):
        if resource.get("kind") != "image" or resource.get("operation") == "removed":
            continue
        identifier = resource["resource_id"]
        if resource["status"] in ("NEW", "CHANGED") or old is None:
            outcome = _outcome("NEW", "OCR_PROCESS_RESOURCE")
        elif resource["status"] == "UNKNOWN":
            outcome = _outcome("UNKNOWN", "OCR_RESOURCE_UNVERIFIED")
        else:
            try:
                outcome = _verify(old, new, identifier, reader)
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
    }
