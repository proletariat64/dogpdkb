"""Choose a conservative document action from independent dimensions."""

from typing import Any


def choose_action(
    old_exists: bool,
    text: dict[str, Any],
    resources: dict[str, Any],
    metadata: dict[str, Any],
    ocr: dict[str, Any],
) -> str:
    if any(d["status"] == "UNKNOWN" for d in (text, resources, ocr)):
        return "UNKNOWN"
    if not old_exists:
        return "PROCESS_REQUIRED"
    if resources["status"] in ("NEW", "CHANGED"):
        return "PROCESS_CHANGED_RESOURCES"
    if ocr["status"] not in ("UNCHANGED", "NOT_APPLICABLE"):
        return "PROCESS_REQUIRED"
    if text["status"] == "CHANGED":
        return "UPDATE_TEXT_REUSE_OCR"
    if metadata["status"] == "CHANGED":
        return "UPDATE_METADATA_ONLY"
    return "SKIP_VERIFIED"
