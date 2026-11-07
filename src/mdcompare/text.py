"""Exact raw comparison; extended by the text-diff ticket."""

from typing import Any

from .model import CompareOptions


def compare_text(
    old: bytes | None, new: bytes, options: CompareOptions
) -> dict[str, Any]:
    if old is None:
        return {"status": "NEW", "reason_codes": ["NEW_DOCUMENT"], "differences": []}
    same = len(old) == len(new) and old == new
    return {
        "status": "UNCHANGED" if same else "CHANGED",
        "reason_codes": ["TEXT_BYTES_EQUAL" if same else "TEXT_BYTES_DIFFER"],
        "differences": [],
    }
