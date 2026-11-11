"""Durable OCR success cache, independent of the cleanable wash run directory.

A success result is persisted only after the existing mdocr format/artifact
checks pass; failed, blank or unparseable results never reach this store, so an
unstable interface cannot overwrite a valid cached result. The cache entry key
is the existing content-named prepared-asset stem (``<name>-<digest8>``) — no new
fingerprint. Reuse is decided by the mdcompare ``prepared-jpeg-ocr-v1`` proof.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from dogpdkb.sync_results import _slug
from dogpdkb.sync_run import RunError, atomic_json, read_json
from mdcompare.io import InputError
from mdcompare.ocr import CONTRACT
from mdcompare.prepared_image import is_prepared_jpeg
from mdocr import __version__ as MDOCR_VERSION
from mdocr.paddle import _MODEL
from mdocr.prepare import JPEG_QUALITY

#: Normalized processing identity; reuse requires old/new to match exactly.
PREPROCESSING = {"name": "mdocr", "version": MDOCR_VERSION}
CONFIGURATION = {
    "engine": "paddleocr",
    "model": _MODEL,
    "jpeg_quality": JPEG_QUALITY,
}


def cache_root(config: Any, book_key: str) -> Path:
    return Path(config.ocr_cache) / _slug(book_key)


def entry_dir(root: Path, key: str) -> Path:
    return root / key


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RunError(f"ocr cache entry must not be a symlink: {path.name}")
    if path.is_file() and path.read_bytes() == data:
        return
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def image_evidence(
    *,
    resource_id: str,
    source_path: Path,
    prepared_image: Path,
    artifact: Path,
    status: str = "success",
) -> dict[str, Any]:
    """One ``prepared-jpeg-ocr-v1`` image record with durable absolute paths."""
    return {
        "resource_id": resource_id,
        "source_path": str(Path(source_path).resolve()),
        "prepared_image": str(Path(prepared_image).resolve()),
        "status": status,
        "artifact": str(Path(artifact).resolve()),
    }


def document_evidence(images: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "preprocessing": dict(PREPROCESSING),
        "configuration": dict(CONFIGURATION),
        "images": images,
    }


def persist_success(
    root: Path,
    key: str,
    *,
    resource_id: str,
    original_name: str,
    original_bytes: bytes,
    prepared_bytes: bytes,
    artifact_text: str,
) -> dict[str, Any]:
    """Persist one verified success result durably; return its image evidence.

    Only called after the OCR result passed the format/artifact checks. The
    original, prepared JPEG and artifact are stored together so the next batch's
    mdcompare can verify reuse from durable bytes alone.
    """
    entry = entry_dir(root, key)
    original = entry / original_name
    prepared = entry / "prepared.jpg"
    artifact = entry / "artifact.md"
    _write_bytes(original, original_bytes)
    _write_bytes(prepared, prepared_bytes)
    _write_bytes(artifact, artifact_text.encode("utf-8"))
    record = image_evidence(
        resource_id=resource_id,
        source_path=original,
        prepared_image=prepared,
        artifact=artifact,
    )
    atomic_json(entry / "evidence.json", document_evidence([record]))
    return record


def load_evidence(root: Path, key: str) -> dict[str, Any] | None:
    path = entry_dir(root, key) / "evidence.json"
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except RunError:
        return None
    if not isinstance(payload, dict) or payload.get("contract") != CONTRACT:
        return None
    images = payload.get("images")
    if not isinstance(images, list) or not images:
        return None
    return payload


def _canonical_artifact(text: str, prepared: Path, artifact: Path) -> bool:
    """The same structural proof mdcompare applies to a cached OCR artifact.

    The artifact must be a canonical, non-empty mdocr block whose declared
    source resolves to the cached prepared JPEG beside it.
    """
    source = prepared.name
    prefix = f'<!-- mdocr:ocr-begin source="{source}" -->\n\n'
    footer = f"\n\n> Original image: [{source}]({source})\n\n<!-- mdocr:ocr-end -->"
    if not text.startswith(prefix) or not text.endswith(footer):
        return False
    body = text[len(prefix) : -len(footer)]
    if not body.strip() or "<!-- mdocr:" in body:
        return False
    try:
        return (artifact.parent / source).resolve() == prepared.resolve()
    except (OSError, ValueError, RuntimeError):
        return False


def verify_reuse(
    root: Path,
    key: str,
    *,
    resource_id: str,
    original_bytes: bytes,
    prepared_bytes: bytes,
) -> dict[str, Any] | None:
    """Return cached success evidence iff this exact content may reuse it.

    Reuse follows the ``prepared-jpeg-ocr-v1`` proof: compatible normalized
    preprocessing/configuration, the cached original and prepared JPEG matching
    the freshly fetched/normalized bytes (so the same URL with changed content
    never reuses, and a truncated content digest cannot alias a different
    image), a canonical prepared JPEG, and a canonical non-empty artifact. No
    new fingerprint is introduced — the bytes are compared directly.
    """
    evidence = load_evidence(root, key)
    if evidence is None:
        return None
    if evidence.get("preprocessing") != PREPROCESSING:
        return None
    if evidence.get("configuration") != CONFIGURATION:
        return None
    record = next(
        (
            row
            for row in evidence["images"]
            if isinstance(row, dict) and row.get("status") == "success"
        ),
        None,
    )
    if record is None:
        return None
    try:
        original = Path(record["source_path"])
        prepared = Path(record["prepared_image"])
        artifact = Path(record["artifact"])
        if original.read_bytes() != original_bytes:
            return None
        cached_prepared = prepared.read_bytes()
        if cached_prepared != prepared_bytes or not is_prepared_jpeg(cached_prepared):
            return None
        text = artifact.read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError, UnicodeDecodeError, InputError):
        return None
    if not _canonical_artifact(text, prepared, artifact):
        return None
    # Re-bind the evidence to this batch's resource_id; the durable artifact and
    # prepared JPEG are shared because the verified content is identical.
    return image_evidence(
        resource_id=resource_id,
        source_path=original,
        prepared_image=prepared,
        artifact=artifact,
    )
