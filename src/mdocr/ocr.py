"""OCR block lifecycle and Markdown document orchestration."""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path
from typing import Protocol

from ._http import HttpClient
from .markdown import DocumentResult, transform_images
from .prepare import (
    DEFAULT_ENABLED_EXTENSIONS,
    ImageError,
    ImageSkip,
    open_prepared,
    prepare_one,
)

_OCR_BEGIN = '<!-- mdocr:ocr-begin source="{source}" -->'
_OCR_END = "<!-- mdocr:ocr-end -->"
_OCR_BLOCK_RE = re.compile(
    r"<!--\s*mdocr:ocr-begin\s+source=\"([^\"]+)\"\s*-->"
    r".*?"
    r"<!--\s*mdocr:ocr-end\s*-->",
    re.DOTALL,
)


class _OcrClient(Protocol):
    """Internal conversion seam for document orchestration."""

    def convert(self, image_path: Path) -> str: ...


def build_ocr_block(source_rel: str, ocr_markdown: str) -> str:
    """Wrap OCR Markdown in mdocr markers with a quoted original-image link."""
    return (
        f"{_OCR_BEGIN.format(source=source_rel)}\n\n"
        f"{ocr_markdown.strip()}\n\n"
        f"> Original image: [{source_rel}]({source_rel})\n\n"
        f"{_OCR_END}"
    )


def _reocr_existing_blocks(
    text: str, output_dir: Path, client: _OcrClient, errors: list[str]
) -> str:
    """Under --force, re-OCR valid prepared JPEGs named by existing blocks."""

    def replace(match: re.Match[str]) -> str:
        source = match.group(1)
        try:
            prepared = open_prepared(source, output_dir)
        except ImageError as exc:
            errors.append(f"{source}: re-OCR skipped: {exc}")
            return match.group(0)

        try:
            result = client.convert(prepared.path)
        except Exception as exc:  # noqa: BLE001 - per-image failure is recorded
            errors.append(f"{source}: re-OCR failed: {exc}")
            return match.group(0)
        return build_ocr_block(prepared.source, result)

    return _OCR_BLOCK_RE.sub(replace, text)


def _protect_existing_blocks(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Mask existing OCR blocks with NUL markers so inner content is untouched.

    Returns ``(masked_text, [(marker, original_block), ...])``; restore with
    ``_restore_blocks``. Images inside a preserved block must not be treated
    as new images (spec: existing blocks are preserved idempotently).
    """
    placeholders: list[tuple[str, str]] = []

    def protect(match: re.Match[str]) -> str:
        marker = f"\x00mdocr{len(placeholders)}\x00"
        placeholders.append((marker, match.group(0)))
        return marker

    return _OCR_BLOCK_RE.sub(protect, text), placeholders


def _restore_blocks(text: str, placeholders: list[tuple[str, str]]) -> str:
    for marker, original in placeholders:
        text = text.replace(marker, original)
    return text


def ocr_document(
    text: str,
    md_file: Path,
    output_dir: Path,
    client: _OcrClient,
    *,
    download: bool,
    force: bool,
    enabled_extensions: Collection[str] = DEFAULT_ENABLED_EXTENSIONS,
    session: HttpClient | None = None,
) -> DocumentResult:
    """OCR every eligible image in one Markdown document."""
    if force:
        errors: list[str] = []
        text = _reocr_existing_blocks(text, output_dir, client, errors)
        placeholders: list[tuple[str, str]] = []
    else:
        errors = []
        text, placeholders = _protect_existing_blocks(text)
    warnings: list[str] = []

    def transform(ref):
        try:
            prepared = prepare_one(
                ref,
                md_file,
                output_dir,
                download=download,
                session=session,
                enabled_extensions=enabled_extensions,
            )
        except (ImageSkip, ImageError) as exc:
            messages = warnings if isinstance(exc, ImageSkip) else errors
            messages.append(f"{ref.target}: {exc}")
            return None
        try:
            result = client.convert(prepared.path)
        except Exception as exc:  # noqa: BLE001 - per-image failure is recorded
            errors.append(f"{prepared.source}: OCR failed: {exc}")
            return None
        return build_ocr_block(prepared.source, result)

    new_text, warnings, errors = transform_images(
        text, transform, errors=errors, warnings=warnings
    )
    return DocumentResult(
        _restore_blocks(new_text, placeholders),
        tuple(warnings),
        tuple(errors),
    )
