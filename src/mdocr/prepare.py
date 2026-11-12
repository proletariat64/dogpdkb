"""Image preparation: download (optional), decode, and normalize to JPEG assets."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from ._http import HttpClient
from .markdown import DocumentResult, ImageRef, render_image, transform_images

JPEG_QUALITY = 90
DOWNLOAD_TIMEOUT = 30
DEFAULT_ENABLED_EXTENSIONS = frozenset(
    {".png", ".webp", ".gif", ".jpg", ".jpeg", ".tif", ".tiff"}
)

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class ImageSkip(Exception):
    """Intentional skip (remote without --download, unsupported form). Warning only."""


class ImageError(Exception):
    """A real per-image failure; the run continues but exits nonzero."""


@dataclass(frozen=True)
class PreparedImage:
    """A validated local JPEG and its source path in output Markdown."""

    path: Path
    source: str
    original: Path | None = None
    identity: str = ""


def _safe_stem(name: str) -> str:
    stem = Path(name).stem or "image"
    cleaned = _SAFE.sub("-", stem).strip("-")
    return cleaned or "image"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def readable_extensions() -> frozenset[str]:
    """Return filename extensions the installed Pillow can decode."""
    Image.init()
    return frozenset(
        extension.lower()
        for extension, image_format in Image.registered_extensions().items()
        if image_format in Image.OPEN
    )


def _format_for_extension(extension: str) -> str | None:
    Image.init()
    return Image.registered_extensions().get(extension.lower())


def validate_image_format(data: bytes, expected_format: str) -> None:
    """Ensure image bytes load fully and match its expected Pillow format."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != expected_format:
                raise ImageError(
                    f"image content is {image.format or 'unknown'}, "
                    f"not {expected_format}"
                )
            image.load()
    except ImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError(f"not a readable image: {exc}") from exc


def open_prepared(source: str, output_dir: Path) -> PreparedImage:
    """Safely reopen a canonical prepared JPEG from the output tree."""
    source_path = PurePosixPath(source)
    if source_path.is_absolute() or ".." in source_path.parts:
        raise ImageError("source outside output tree")
    if (
        source != source_path.as_posix()
        or not source_path.parts
        or source_path.parts[0] != "assets"
        or source_path.suffix != ".jpg"
    ):
        raise ImageError("source must be a canonical .jpg path under assets")

    try:
        output_root = output_dir.resolve()
        assets_root = (output_root / "assets").resolve()
        asset = output_root.joinpath(*source_path.parts).resolve()
    except (OSError, RuntimeError) as exc:
        raise ImageError(f"could not resolve source: {exc}") from exc
    if not asset.is_relative_to(output_root):
        raise ImageError("source outside output tree")
    if not asset.is_relative_to(assets_root):
        raise ImageError("source must be a canonical .jpg path under assets")
    if not asset.is_file():
        raise ImageError("prepared JPEG asset not found")
    try:
        validate_image_format(asset.read_bytes(), "JPEG")
    except OSError as exc:
        raise ImageError(f"prepared JPEG asset unreadable: {exc}") from exc
    except ImageError as exc:
        raise ImageError(f"prepared JPEG asset invalid: {exc}") from exc
    return PreparedImage(path=asset, source=source)


def normalize_image(data: bytes, expected_format: str | None = None) -> bytes:
    """Decode with Pillow, honor EXIF orientation, take the first frame, and
    return JPEG bytes. Any decode failure raises ImageError."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if expected_format is not None and image.format != expected_format:
                raise ImageError(
                    f"image content is {image.format or 'unknown'}, "
                    f"not {expected_format}"
                )
            image = ImageOps.exif_transpose(image)
            # convert() collapses multi-frame sources (e.g. GIF) to their first frame.
            image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=JPEG_QUALITY)
            return out.getvalue()
    except ImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError(f"not a readable image: {exc}") from exc


def _fetch_remote(url: str, session: HttpClient) -> bytes:
    try:
        response = session.get(url, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ImageError(f"download failed: {exc}") from exc
    return response.content


def prepare_document(
    markdown: str,
    md_file: Path,
    output_dir: Path,
    *,
    download: bool,
    enabled_extensions: Collection[str] = DEFAULT_ENABLED_EXTENSIONS,
    session: HttpClient | None = None,
) -> DocumentResult:
    """Prepare every eligible image in one Markdown document."""
    warnings: list[str] = []
    errors: list[str] = []

    def transform(ref: ImageRef) -> str | None:
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
        return render_image(ref.alt, prepared.source, ref.title)

    transformed, warnings, errors = transform_images(
        markdown,
        transform,
        errors=errors,
        warnings=warnings,
    )
    return DocumentResult(transformed, tuple(warnings), tuple(errors))


def prepare_one(
    ref: ImageRef,
    md_file: Path,
    output_dir: Path,
    *,
    download: bool,
    session: HttpClient | None = None,
    enabled_extensions: Collection[str] = DEFAULT_ENABLED_EXTENSIONS,
    retain_original: bool = False,
) -> PreparedImage:
    """Normalize one eligible image into a JPEG asset under the output directory."""
    if ref.unsupported:
        raise ImageSkip("unsupported image target form")

    source_path = urlparse(ref.target).path if ref.is_remote else ref.target
    extension = Path(source_path).suffix.lower()
    enabled = {item.lower() for item in enabled_extensions}
    if not extension:
        raise ImageSkip("image has no filename extension")
    if extension not in enabled:
        raise ImageSkip(f"image extension {extension} is not enabled")

    expected_format = _format_for_extension(extension)
    if expected_format is None or extension not in readable_extensions():
        raise ImageError(f"image extension {extension} is not readable by Pillow")

    if ref.is_remote:
        if not download:
            raise ImageSkip("remote image; pass --download to fetch")
        session = session or requests.Session()
        data = _fetch_remote(ref.target, session)
        source_name = Path(source_path).name or "remote"
    else:
        path = (md_file.parent / ref.target).resolve()
        if not path.is_file():
            raise ImageError(f"image file not found: {path}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ImageError(f"read failed: {exc}") from exc
        source_name = path.name

    digest = _digest(data)
    if extension in {".jpg", ".jpeg"}:
        validate_image_format(data, "JPEG")
        jpeg = data
    else:
        jpeg = normalize_image(data, expected_format=expected_format)
    asset_dir = output_dir / "assets" / _safe_stem(md_file.name)
    asset = asset_dir / f"{_safe_stem(source_name)}-{digest[:8]}.jpg"
    try:
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(jpeg)
        original = asset.with_suffix(".original") if retain_original else None
        if original is not None:
            original.write_bytes(data)
    except OSError as exc:
        raise ImageError(f"write failed: {exc}") from exc
    return PreparedImage(
        path=asset,
        source=asset.relative_to(output_dir).as_posix(),
        original=original,
        identity=digest,
    )
