import io
import shutil

import pytest
from PIL import Image

from mdocr.llm_image import vision_image
from mdocr.prepare import ImageError


@pytest.mark.parametrize("fmt", ["BMP", "TIFF", "ICO", "PNG", "JPEG", "WEBP", "GIF"])
def test_formats_are_decoded_from_bytes(fmt):
    source = io.BytesIO()
    Image.new("RGB", (32, 32), (30, 60, 130)).save(source, fmt)
    rendered, mime = vision_image(source.getvalue())
    if fmt in {"PNG", "JPEG", "WEBP", "GIF"}:
        assert mime == f"image/{fmt.lower()}"
        assert rendered == source.getvalue()
    else:
        assert mime == "image/png"
        with Image.open(io.BytesIO(rendered)) as png:
            assert png.size == (32, 32)
            assert png.convert("RGB").getpixel((15, 15)) == (30, 60, 130)


@pytest.mark.skipif(
    not (shutil.which("google-chrome") or shutil.which("chromium")),
    reason="requires sandboxed Chromium renderer",
)
def test_svg_foreign_object_preserves_visible_html():
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 380 180">
      <rect width="380" height="180" fill="red" />
      <foreignObject x="30" y="15" width="180" height="130">
        <div xmlns="http://www.w3.org/1999/xhtml" style="width:100%;height:100%;background:#00ff00">Title</div>
      </foreignObject>
    </svg>"""
    rendered, mime = vision_image(source)
    assert mime == "image/png"
    with Image.open(io.BytesIO(rendered)) as png:
        assert png.size == (380, 180)
        rgb = png.convert("RGB")
        assert rgb.getpixel((60, 65)) == (0, 255, 0)
        assert rgb.getpixel((260, 100)) == (255, 0, 0)


@pytest.mark.parametrize(
    "payload",
    [
        b"not an image",
        b"<html>login</html>",
        b'<!DOCTYPE svg [<!ENTITY z "unsafe">]><svg>&z;</svg>',
        b'<svg><image href="https://example.invalid/image.png" /></svg>',
    ],
)
def test_invalid_or_externally_referenced_images_are_not_sent(payload):
    with pytest.raises(ImageError):
        vision_image(payload)
