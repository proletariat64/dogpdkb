"""OCR equality requires its own success and compatibility evidence."""

from typing import Any

from .io import InputReader
from .model import CompareOptions, Document


def compare_ocr(
    old: Document | None,
    new: Document,
    resources: dict[str, Any],
    reader: InputReader,
    options: CompareOptions,
) -> dict[str, Any]:
    if resources["status"] == "NOT_APPLICABLE":
        return {"status": "NOT_APPLICABLE", "reason_codes": []}
    return {"status": "UNKNOWN", "reason_codes": ["OCR_REUSE_UNVERIFIED"]}
