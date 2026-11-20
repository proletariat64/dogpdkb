"""Prepare local vision attachments without changing original evidence bytes."""

from __future__ import annotations

import base64
import errno
import io
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageOps, UnidentifiedImageError

from .prepare import ImageError

_NATIVE_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
_SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
_SVG_MAX_EDGE = 8192
_SVG_MAX_AREA = 24_000_000


class SvgCleanupError(ImageError):
    """The local SVG profile could not be removed before provider conversion."""


def _cleanup_svg_workspace(directory):
    # A Chrome helper may still be writing profile data after Chrome exits.
    # Retry this particular teardown race without rerendering or resending.
    for delay in (0.05, 0.15, 0.30, None):
        try:
            directory.cleanup()
        except OSError as exc:
            if exc.errno != errno.ENOTEMPTY:
                raise ImageError("SVG profile cleanup failed") from exc
            if delay is None:
                raise SvgCleanupError("SVG profile cleanup did not settle") from exc
            time.sleep(delay)
        else:
            return


@contextmanager
def _svg_workspace():
    directory = tempfile.TemporaryDirectory(prefix="mdocr-svg-")
    try:
        yield directory.name
    except BaseException:
        try:
            _cleanup_svg_workspace(directory)
        except ImageError:
            logging.getLogger(__name__).warning("SVG profile cleanup also failed")
        raise
    else:
        _cleanup_svg_workspace(directory)


def vision_image(data: bytes) -> tuple[bytes, str]:
    """Use format sniffing, not filename suffixes, for provider attachments."""
    try:
        image = Image.open(io.BytesIO(data))
    except UnidentifiedImageError:
        return _render_svg(data), "image/png"
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageError("invalid or oversized image") from exc
    try:
        with image:
            if image.format in {"EPS", "PDF"}:
                raise ImageError(
                    "document formats requiring an external interpreter are not images"
                )
            image.load()
            if mime := _NATIVE_MIME.get(image.format or ""):
                return data, mime
            png = io.BytesIO()
            ImageOps.exif_transpose(image).convert("RGBA").save(png, "PNG")
            return png.getvalue(), "image/png"
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageError("unsupported or unreadable image") from exc


def _svg_size(svg):
    viewbox = re.split(r"[\s,]+", svg.get("viewBox", "").strip())

    def pixels(name, axis, default):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?|\.\d+)(?:px)?\s*", svg.get(name, ""))
        raw = match[1] if match else viewbox[axis] if len(viewbox) == 4 else default
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("invalid SVG dimensions")
        return value

    width, height = pixels("width", 2, 300), pixels("height", 3, 150)
    scale = min(
        1,
        _SVG_MAX_EDGE / width,
        _SVG_MAX_EDGE / height,
        math.sqrt(_SVG_MAX_AREA / width / height),
    )
    return max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))


def _render_svg(data: bytes) -> bytes:
    # Reject entity declarations before parsing untrusted SVG, including UTF-16.
    if re.search(
        rb"<!\s*(?:DOCTYPE|ENTITY)\b", data.replace(b"\0", b""), re.IGNORECASE
    ):
        raise ImageError("SVG document type and entity declarations are unsupported")
    try:
        svg = ElementTree.fromstring(data)
        if svg.tag not in {"svg", _SVG_NAMESPACE + "svg"}:
            raise ValueError("not an SVG")
        width, height = _svg_size(svg)
    except (ElementTree.ParseError, ValueError) as exc:
        raise ImageError("unsupported or invalid raster/SVG image") from exc
    for element in svg.iter():
        if element.tag.rsplit("}", 1)[-1] not in {"image", "use", "feImage", "img"}:
            continue
        for name, reference in element.attrib.items():
            if name.rsplit("}", 1)[-1] in {
                "href",
                "src",
            } and not reference.strip().lower().startswith(("#", "data:image/")):
                raise ImageError("SVG references a separate image; embed it before OCR")
    chrome = next(
        (
            path
            for name in ("google-chrome", "chromium", "chromium-browser")
            if (path := shutil.which(name))
        ),
        None,
    )
    if not chrome:
        raise ImageError("SVG OCR requires Google Chrome or Chromium on PATH")
    with _svg_workspace() as tmp:
        root = Path(tmp)
        wrapper, screenshot = root / "image.html", root / "image.png"
        source = base64.b64encode(data).decode("ascii")
        # Chrome's SVG-as-image mode does not execute embedded script or fetch
        # subresources. Do not open the supplied SVG as a top-level document.
        wrapper.write_text(
            '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">"
            "<style>body{margin:0;background:white}img{display:block;width:100vw;height:100vh}</style>"
            f'<img src="data:image/svg+xml;base64,{source}">',
            encoding="ascii",
        )
        try:
            subprocess.run(
                [
                    chrome,
                    "--headless",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-extensions",
                    "--no-first-run",
                    "--disable-sync",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--no-default-browser-check",
                    "--proxy-server=http://127.0.0.1:9",
                    "--host-resolver-rules=MAP * ~NOTFOUND",
                    "--force-device-scale-factor=1",
                    f"--user-data-dir={root / 'profile'}",
                    f"--window-size={width},{height}",
                    f"--screenshot={screenshot}",
                    wrapper.as_uri(),
                ],
                # Do not expose the user's Chrome profile, config or provider
                # environment to the renderer (including via launcher wrappers).
                env={
                    "PATH": os.environ.get("PATH", os.defpath),
                    "HOME": tmp,
                    "XDG_CONFIG_HOME": tmp,
                    "XDG_CACHE_HOME": tmp,
                    "XDG_DATA_HOME": tmp,
                },
                capture_output=True,
                check=True,
                timeout=45,
            )
            with Image.open(screenshot) as rendered:
                rendered.load()
                if rendered.format != "PNG" or rendered.size != (width, height):
                    raise ImageError("SVG renderer returned an unexpected image")
            return screenshot.read_bytes()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ImageError(
                "could not render SVG with a sandboxed Chrome process"
            ) from exc
