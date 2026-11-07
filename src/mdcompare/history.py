"""History comparison requires an exact raw snapshot and complete source mapping."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import InputError, InputReader
from .model import CompareOptions, Document
from .prepared_image import is_prepared_jpeg
from .text import compare_text

# A deliberately narrow mapping contract, not a Markdown cleaner. Unsupported
# source forms remain unknown. These markers are mdocr's existing block format.
_IMAGE = re.compile(rb"!\[[^\]\r\n]*\]\(([^\s()<>]+)\)")
_MARKER = b"<!-- mdocr:ocr-"
MAX_PROVENANCE_RECORDS = 10_000


@dataclass(frozen=True)
class HistoryComparison:
    text: dict[str, Any]
    old_bytes: bytes | None
    new_bytes: bytes


class ProvenanceError(ValueError):
    pass


def _source_view(
    doc: Document, data: bytes, reader: InputReader, options: CompareOptions
) -> bytes:
    if doc.content_kind == "raw":
        return data
    provenance = doc.provenance
    if (
        provenance.get("contract") != "mdocr-raw-snapshot-v1"
        or not isinstance(provenance.get("raw_path"), str)
        or not provenance["raw_path"]
        or not isinstance(provenance.get("blocks"), list)
    ):
        raise ProvenanceError("HISTORICAL_BLOCK_PROVENANCE_INCOMPLETE")
    if (
        len(provenance["blocks"]) > MAX_PROVENANCE_RECORDS
        or len(doc.resources) > MAX_PROVENANCE_RECORDS
    ):
        raise ProvenanceError("HISTORICAL_PROVENANCE_BUDGET_EXCEEDED")
    raw = reader.read_bytes(doc.resolve(provenance["raw_path"]))
    if _MARKER in raw:
        raise ProvenanceError("HISTORICAL_RAW_STAGE_UNVERIFIED")
    if any(not isinstance(r.get("resource_id"), str) for r in doc.resources):
        raise ProvenanceError("HISTORICAL_BLOCK_PROVENANCE_INCOMPLETE")
    resource_by_id = {r["resource_id"]: r for r in doc.resources}
    if len(resource_by_id) != len(doc.resources):
        raise ProvenanceError("HISTORICAL_BLOCK_PROVENANCE_INCOMPLETE")
    pieces: list[bytes] = []
    offset = 0
    total = 0
    validated_prepared: set[Path] = set()
    for block in provenance["blocks"]:
        if not isinstance(block, dict):
            raise ProvenanceError("HISTORICAL_BLOCK_PROVENANCE_INCOMPLETE")
        start, end = block.get("raw_start_byte"), block.get("raw_end_byte")
        if (
            type(start) is not int
            or type(end) is not int
            or not offset <= start < end <= len(raw)
            or any(
                not isinstance(block.get(k), str) or not block[k]
                for k in (
                    "resource_id",
                    "prepared_image",
                    "source_reference",
                    "artifact_path",
                )
            )
        ):
            raise ProvenanceError("HISTORICAL_BLOCK_PROVENANCE_INCOMPLETE")
        original = _IMAGE.fullmatch(raw[start:end])
        resource = resource_by_id.get(block["resource_id"])
        if original is None or resource is None or resource.get("kind") != "image":
            raise ProvenanceError("HISTORICAL_BLOCK_SOURCE_MISMATCH")
        try:
            target = original.group(1).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProvenanceError("HISTORICAL_BLOCK_SOURCE_MISMATCH") from exc
        if resource.get("url") != target:
            raise ProvenanceError("HISTORICAL_BLOCK_SOURCE_MISMATCH")
        source = block["source_reference"]
        if any(c in source for c in ('"', "\n", "\r", "\x00", "[", "]", "(", ")")):
            raise ProvenanceError("HISTORICAL_BLOCK_SOURCE_MISMATCH")
        prepared_path = doc.resolve(block["prepared_image"])
        if (doc.path.parent / Path(source)).resolve() != prepared_path.resolve():
            raise ProvenanceError("HISTORICAL_BLOCK_SOURCE_MISMATCH")
        prepared_data = reader.read_bytes(prepared_path)
        prepared_identity = prepared_path.resolve()
        if prepared_identity not in validated_prepared:
            if not is_prepared_jpeg(prepared_data):
                raise ProvenanceError("HISTORICAL_PREPARED_IMAGE_INVALID")
            validated_prepared.add(prepared_identity)
        try:
            artifact = reader.read_bytes(doc.resolve(block["artifact_path"])).decode(
                "utf-8"
            )
        except UnicodeDecodeError as exc:
            raise ProvenanceError("HISTORICAL_BLOCK_SOURCE_MISMATCH") from exc
        replacement = (
            f'<!-- mdocr:ocr-begin source="{source}" -->\n\n'
            f"{artifact.strip()}\n\n"
            f"> Original image: [{source}]({source})\n\n"
            "<!-- mdocr:ocr-end -->"
        ).encode()
        if _MARKER in artifact.encode("utf-8"):
            raise ProvenanceError("HISTORICAL_BLOCK_SOURCE_MISMATCH")
        total += start - offset + len(replacement)
        if total > options.max_file_bytes:
            raise ProvenanceError("HISTORICAL_RECONSTRUCTION_BUDGET_EXCEEDED")
        pieces.extend((raw[offset:start], replacement))
        offset = end
    if total + len(raw) - offset > options.max_file_bytes:
        raise ProvenanceError("HISTORICAL_RECONSTRUCTION_BUDGET_EXCEEDED")
    pieces.append(raw[offset:])
    if b"".join(pieces) != data:
        raise ProvenanceError("HISTORICAL_RECONSTRUCTION_MISMATCH")
    return raw


def compare_history(
    old: Document | None,
    new: Document,
    old_bytes: bytes | None,
    new_bytes: bytes,
    reader: InputReader,
    options: CompareOptions,
) -> HistoryComparison | None:
    if new.content_kind == "raw" and (old is None or old.content_kind == "raw"):
        return None
    reasons = []
    old_view, new_view = old_bytes, new_bytes
    for is_old, doc, data in ((True, old, old_bytes), (False, new, new_bytes)):
        if doc is None or data is None or doc.content_kind == "raw":
            continue
        try:
            view = _source_view(doc, data, reader, options)
        except ProvenanceError as exc:
            reasons.append(str(exc))
        except InputError as exc:
            reasons.extend(["HISTORICAL_EVIDENCE_READ_FAILED", exc.reason_code])
        except (ValueError, OSError, RuntimeError):
            reasons.append("HISTORICAL_BLOCK_PROVENANCE_INVALID")
        else:
            if is_old:
                old_view = view
            else:
                new_view = view
    if reasons:
        # These are observed stage-to-stage edits, not established source edits.
        result = compare_text(old_bytes, new_bytes, options)
        result["observed_status"] = result["status"]
        result["status"] = "UNKNOWN"
        old_kind = old.content_kind.upper() if old else "NONE"
        result["comparison_scope"] = f"{old_kind}_TO_{new.content_kind.upper()}"
        result["provenance_verified"] = False
        result["reason_codes"] += ["HISTORICAL_RAW_UNAVAILABLE", *reasons]
        if _MARKER not in (old_bytes or b"") + new_bytes:
            result["reason_codes"].append("HISTORICAL_UNMARKED_CONTENT")
        return HistoryComparison(result, old_bytes, new_bytes)
    result = compare_text(old_view, new_view, options)
    result["comparison_scope"] = "VERIFIED_RAW_SNAPSHOTS"
    result["provenance_verified"] = True
    result["reason_codes"].append("HISTORICAL_PROVENANCE_VERIFIED")
    return HistoryComparison(result, old_view, new_view)
