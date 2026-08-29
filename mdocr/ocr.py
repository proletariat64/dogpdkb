"""OCR orchestration: replace image embeds with marked OCR result blocks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import requests

from .markdown import OCR_BLOCK_RE, transform_images
from .prepare import prepare_or_report


class OcrClient(Protocol):
    """Minimal OCR provider: turns a local image path into Markdown."""

    def convert(self, image_path: Path) -> str: ...


def build_ocr_block(source_rel: str, ocr_markdown: str) -> str:
    """Wrap OCR Markdown in mdocr markers with a quoted original-image link."""
    return (
        f'<!-- mdocr:ocr-begin source="{source_rel}" -->\n\n'
        f"{ocr_markdown.strip()}\n\n"
        f"> Original image: [{source_rel}]({source_rel})\n\n"
        f"<!-- mdocr:ocr-end -->"
    )


def _reocr_existing_blocks(
    text: str, output_dir: Path, client: OcrClient, errors: list[str]
) -> str:
    """Under --force, re-OCR the local JPEG recorded in each existing block.

    Sources that resolve outside the output tree are rejected: the spec
    requires the recorded source to be the prepared local JPEG.
    """
    output_root = output_dir.resolve()

    def replace(match: re.Match[str]) -> str:
        source = match.group(1)
        # Reject absolute and traversal sources: only generated relative JPEG
        # paths under the output tree may be re-OCRed.
        if Path(source).is_absolute() or not (
            output_dir / source
        ).resolve().is_relative_to(output_root):
            errors.append(f"{source}: re-OCR skipped: source outside output tree")
            return match.group(0)
        asset = (output_dir / source).resolve()
        try:
            result = client.convert(asset)
        except Exception as exc:  # noqa: BLE001 - per-image failure is recorded
            errors.append(f"{source}: re-OCR failed: {exc}")
            return match.group(0)
        return build_ocr_block(source, result)

    return OCR_BLOCK_RE.sub(replace, text)


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

    return OCR_BLOCK_RE.sub(protect, text), placeholders


def _restore_blocks(text: str, placeholders: list[tuple[str, str]]) -> str:
    for marker, original in placeholders:
        text = text.replace(marker, original)
    return text


def ocr_transform(
    text: str,
    md_file: Path,
    output_dir: Path,
    client: OcrClient,
    *,
    download: bool,
    force: bool,
    session: requests.Session | None = None,
) -> tuple[str, list[str], list[str]]:
    """Return ``(new_markdown, warnings, errors)`` for one Markdown document."""
    if force:
        errors: list[str] = []
        text = _reocr_existing_blocks(text, output_dir, client, errors)
        placeholders: list[tuple[str, str]] = []
    else:
        errors = []
        text, placeholders = _protect_existing_blocks(text)
    warnings: list[str] = []

    def transform(ref):
        prepared = prepare_or_report(
            ref,
            md_file,
            output_dir,
            download=download,
            session=session,
            warnings=warnings,
            errors=errors,
        )
        if prepared is None:
            return None
        try:
            result = client.convert(output_dir / prepared.asset_rel)
        except Exception as exc:  # noqa: BLE001 - per-image failure is recorded
            errors.append(f"{prepared.asset_rel}: OCR failed: {exc}")
            return None
        return build_ocr_block(prepared.asset_rel, result)

    new_text, warnings, errors = transform_images(
        text, transform, errors=errors, warnings=warnings
    )
    return _restore_blocks(new_text, placeholders), warnings, errors
