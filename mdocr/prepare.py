"""Image preparation: download (optional), decode, and normalize to JPEG assets."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from ._http import HttpClient
from .markdown import ImageRef

JPEG_QUALITY = 90
DOWNLOAD_TIMEOUT = 30

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class ImageSkip(Exception):
    """Intentional skip (remote without --download, unsupported form). Warning only."""


class ImageError(Exception):
    """A real per-image failure; the run continues but exits nonzero."""


@dataclass(frozen=True)
class PreparedImage:
    ref: ImageRef
    asset_rel: str  # POSIX path relative to the output directory
    digest: str


def _safe_stem(name: str) -> str:
    stem = Path(name).stem or "image"
    cleaned = _SAFE.sub("-", stem).strip("-")
    return cleaned or "image"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_image(data: bytes) -> bytes:
    """Decode with Pillow, honor EXIF orientation, take the first frame, and
    return JPEG bytes. Any decode failure raises ImageError."""
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        # convert() collapses multi-frame sources (e.g. GIF) to their first frame.
        image = image.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=JPEG_QUALITY)
        return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError(f"not a readable image: {exc}") from exc


def _fetch_remote(url: str, session: HttpClient) -> bytes:
    try:
        response = session.get(url, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ImageError(f"download failed: {exc}") from exc
    return response.content


def prepare_or_report(
    ref: ImageRef,
    md_file: Path,
    output_dir: Path,
    *,
    download: bool,
    session: HttpClient | None,
    warnings: list[str],
    errors: list[str],
) -> PreparedImage | None:
    """prepare_one with skips mapped to warnings and failures to errors.

    Returns None when the image was skipped or failed; the caller keeps the
    original embed unchanged in that case.
    """
    try:
        return prepare_one(ref, md_file, output_dir, download=download, session=session)
    except ImageSkip as exc:
        warnings.append(f"{ref.target}: {exc}")
        return None
    except ImageError as exc:
        errors.append(f"{ref.target}: {exc}")
        return None


def prepare_one(
    ref: ImageRef,
    md_file: Path,
    output_dir: Path,
    *,
    download: bool,
    session: HttpClient | None = None,
) -> PreparedImage:
    """Normalize one image into a JPEG asset under the output directory."""
    if ref.unsupported:
        raise ImageSkip("unsupported image target form")
    session = session or requests.Session()
    if ref.is_remote:
        if not download:
            raise ImageSkip("remote image; pass --download to fetch")
        data = _fetch_remote(ref.target, session)
        source_name = Path(urlparse(ref.target).path).name or "remote"
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
    jpeg = normalize_image(data)
    asset_dir = output_dir / "assets" / _safe_stem(md_file.name)
    asset = asset_dir / f"{_safe_stem(source_name)}-{digest[:8]}.jpg"
    try:
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(jpeg)
    except OSError as exc:
        raise ImageError(f"write failed: {exc}") from exc
    return PreparedImage(
        ref=ref, asset_rel=asset.relative_to(output_dir).as_posix(), digest=digest
    )
