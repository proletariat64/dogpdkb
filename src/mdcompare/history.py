"""Strict comparison never treats enriched Markdown as raw input."""

from dataclasses import dataclass
from typing import Any

from .model import CompareOptions, Document
from .text import compare_text

_MARKER = b"<!-- mdocr:ocr-"


@dataclass(frozen=True)
class HistoryComparison:
    text: dict[str, Any]
    old_bytes: bytes | None
    new_bytes: bytes


def compare_history(
    old: Document | None,
    new: Document,
    old_bytes: bytes | None,
    new_bytes: bytes,
    options: CompareOptions,
) -> HistoryComparison | None:
    if new.content_kind == "raw" and (old is None or old.content_kind == "raw"):
        return None
    # Stage-to-stage edits are observable, not established changes to raw input.
    result = compare_text(old_bytes, new_bytes, options)
    result["observed_status"] = result["status"]
    result["status"] = "UNKNOWN"
    old_kind = old.content_kind.upper() if old else "NONE"
    result["comparison_scope"] = f"{old_kind}_TO_{new.content_kind.upper()}"
    result["provenance_verified"] = False
    result["reason_codes"] += ["HISTORICAL_RAW_UNAVAILABLE"]
    if _MARKER not in (old_bytes or b"") + new_bytes:
        result["reason_codes"].append("HISTORICAL_UNMARKED_CONTENT")
    return HistoryComparison(result, old_bytes, new_bytes)
