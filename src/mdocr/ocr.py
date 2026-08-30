"""OCR orchestration: replace image embeds with marked OCR result blocks."""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path, PurePosixPath
from typing import Protocol

import requests

from .markdown import OCR_BLOCK_RE, transform_images
from .prepare import (
    DEFAULT_ENABLED_EXTENSIONS,
    ImageError,
    prepare_or_report,
    validate_image_format,
)


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
    """Under --force, re-OCR valid prepared JPEGs named by existing blocks."""
    output_root = output_dir.resolve()
    assets_root = (output_root / "assets").resolve()

    def replace(match: re.Match[str]) -> str:
        source = match.group(1)
        source_path = PurePosixPath(source)
        if source_path.is_absolute() or ".." in source_path.parts:
            errors.append(f"{source}: re-OCR skipped: source outside output tree")
            return match.group(0)
        if (
            source != source_path.as_posix()
            or not source_path.parts
            or source_path.parts[0] != "assets"
            or source_path.suffix != ".jpg"
        ):
            errors.append(
                f"{source}: re-OCR skipped: source must be a canonical .jpg path "
                "under assets"
            )
            return match.group(0)

        try:
            asset = (output_root.joinpath(*source_path.parts)).resolve()
        except (OSError, RuntimeError) as exc:
            errors.append(f"{source}: re-OCR skipped: could not resolve source: {exc}")
            return match.group(0)
        if not asset.is_relative_to(output_root):
            errors.append(f"{source}: re-OCR skipped: source outside output tree")
            return match.group(0)
        if not asset.is_relative_to(assets_root):
            errors.append(
                f"{source}: re-OCR skipped: source must be a canonical .jpg path "
                "under assets"
            )
            return match.group(0)
        if not asset.is_file():
            errors.append(f"{source}: re-OCR skipped: prepared JPEG asset not found")
            return match.group(0)
        try:
            validate_image_format(asset.read_bytes(), "JPEG")
        except OSError as exc:
            errors.append(f"{source}: re-OCR skipped: prepared JPEG asset unreadable: {exc}")
            return match.group(0)
        except ImageError as exc:
            errors.append(f"{source}: re-OCR skipped: prepared JPEG asset invalid: {exc}")
            return match.group(0)

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
    enabled_extensions: Collection[str] = DEFAULT_ENABLED_EXTENSIONS,
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
            enabled_extensions=enabled_extensions,
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
