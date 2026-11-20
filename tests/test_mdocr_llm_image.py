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


@pytest.mark.parametrize(
    "persistent,bad_render", [(False, False), (True, False), (True, True)]
)
def test_svg_profile_cleanup_race_is_scoped_to_its_attachment(
    monkeypatch, tmp_path, persistent, bad_render
):
    import errno
    from pathlib import Path

    from mdocr import llm_image

    real_rmtree = shutil.rmtree
    calls = []
    monkeypatch.setattr(llm_image.shutil, "which", lambda _: "/fixture/chromium")
    monkeypatch.setattr(llm_image.tempfile, "tempdir", str(tmp_path))

    def render(argv, **kwargs):
        screenshot = Path(
            next(
                arg.split("=", 1)[1] for arg in argv if arg.startswith("--screenshot=")
            )
        )
        profile = screenshot.parent / "profile/Default"
        profile.mkdir(parents=True)
        size = (301, 150) if bad_render else (300, 150)
        Image.new("RGB", size, "red").save(screenshot)

    def clean(path, *args, **kwargs):
        if Path(path).name.startswith("mdocr-svg-"):
            calls.append(Path(path))
            if len(calls) == 1 or persistent:
                raise OSError(
                    errno.ENOTEMPTY,
                    "Directory not empty",
                    str(Path(path) / "profile/Default"),
                )
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(llm_image.subprocess, "run", render)
    monkeypatch.setattr(llm_image.shutil, "rmtree", clean)
    source = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    if bad_render:
        with pytest.raises(ImageError, match="unexpected image") as error:
            vision_image(source)
        assert not isinstance(error.value, llm_image.SvgCleanupError)
        assert len(calls) == 4
    elif persistent:
        with pytest.raises(llm_image.SvgCleanupError, match="cleanup"):
            vision_image(source)
    else:
        rendered, mime = vision_image(source)
        assert mime == "image/png"
        with Image.open(io.BytesIO(rendered)) as image:
            assert image.getpixel((0, 0)) == (255, 0, 0)
        assert len(calls) == 2
        assert not calls[0].exists()
