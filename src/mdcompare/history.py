"""History is a distinct input stage, never implicitly a raw baseline."""

from typing import Any

from .io import InputReader
from .model import CompareOptions, Document


def compare_history(
    old: Document | None,
    new: Document,
    old_bytes: bytes | None,
    new_bytes: bytes,
    reader: InputReader,
    options: CompareOptions,
) -> dict[str, Any] | None:
    if new.content_kind != "raw" or (old and old.content_kind != "raw"):
        return {
            "status": "UNKNOWN",
            "reason_codes": ["HISTORICAL_RAW_UNAVAILABLE"],
            "differences": [],
        }
    return None
