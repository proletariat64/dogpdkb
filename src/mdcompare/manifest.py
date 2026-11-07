"""Validate manifests before establishing any document pairing."""

import json
from pathlib import Path
from typing import Any

from .io import InputError, InputReader
from .model import SCHEMA_VERSION, Document


class ManifestError(ValueError):
    """The complete input cannot be interpreted safely."""


def _string(value: Any, *, nonempty: bool = True) -> bool:
    return isinstance(value, str) and (bool(value) or not nonempty)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def load_manifest(path: Path, reader: InputReader | None = None) -> list[Document]:
    try:
        reader = reader or InputReader(16 * 1024 * 1024)
        data = reader.read_bytes(path, max_bytes=16 * 1024 * 1024)
        value = json.loads(data, object_pairs_hook=_unique_object)
    except (ManifestError, InputError):
        raise
    except (OSError, ValueError) as exc:
        raise ManifestError("MANIFEST_UNREADABLE_OR_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != SCHEMA_VERSION
        or not isinstance(value.get("documents"), list)
    ):
        raise ManifestError("MANIFEST_SCHEMA_INVALID")
    documents = []
    seen = set()
    for row in value["documents"]:
        if not isinstance(row, dict):
            raise ManifestError("DOCUMENT_SCHEMA_INVALID")
        for name in ("knowledge_base", "document_id", "path"):
            if not _string(row.get(name)):
                raise ManifestError("DOCUMENT_SCHEMA_INVALID")
        if row.get("content_kind") not in ("raw", "historical_enriched"):
            raise ManifestError("CONTENT_KIND_INVALID")
        metadata = row.get("metadata")
        if not isinstance(metadata, dict) or any(
            not _string(metadata.get(k), nonempty=False)
            for k in ("name", "logical_path", "source_ref")
        ):
            raise ManifestError("METADATA_SCHEMA_INVALID")
        if "sync_timestamp" in metadata and not _string(
            metadata["sync_timestamp"], nonempty=False
        ):
            raise ManifestError("METADATA_SCHEMA_INVALID")
        resources = row.get("resources")
        if not isinstance(resources, list) or any(
            not isinstance(r, dict) for r in resources
        ):
            raise ManifestError("RESOURCE_SCHEMA_INVALID")
        ocr = row.get("ocr")
        provenance = row.get("provenance", {})
        if (ocr is not None and not isinstance(ocr, dict)) or not isinstance(
            provenance, dict
        ):
            raise ManifestError("PROVENANCE_SCHEMA_INVALID")
        identity = row["knowledge_base"], row["document_id"]
        if identity in seen:
            raise ManifestError("DUPLICATE_DOCUMENT_ID")
        seen.add(identity)
        reference = Path(row["path"])
        documents.append(
            Document(
                *identity,
                row["content_kind"],
                reference if reference.is_absolute() else path.parent / reference,
                metadata,
                resources,
                path.parent,
                ocr,
                provenance,
            )
        )
    return sorted(documents, key=lambda d: d.identity)
