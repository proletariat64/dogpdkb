"""Conservative resource boundary until independent verification is available."""

from typing import Any

from .io import InputReader
from .model import CompareOptions, Document


def compare_resources(
    old: Document | None,
    new: Document,
    old_bytes: bytes | None,
    new_bytes: bytes,
    reader: InputReader,
    options: CompareOptions,
) -> dict[str, Any]:
    # Empty declarations alone do not prove that Markdown has no dependencies.
    references = any(
        token in (old_bytes or b"") + new_bytes
        for token in (
            b"![",
            b"](",
            b"<img",
            b"<a ",
            b"<video",
            b"<audio",
            b"<iframe",
            b"]:",
            b"<object",
        )
    )
    if new.resources or (old and old.resources) or references:
        return {
            "status": "UNKNOWN",
            "items": [],
            "reason_codes": ["RESOURCE_EVIDENCE_UNVERIFIED"],
        }
    return {"status": "NOT_APPLICABLE", "items": [], "reason_codes": []}
