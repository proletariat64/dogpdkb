"""Unit tests for mdocr.prepare image normalization."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from mdocr.prepare import (
    ImageError,
    ImageSkip,
    normalize_image,
    open_prepared,
    prepare_document,
    prepare_one,
)

from .conftest import FakeSession, make_image, ref_of


def test_normalize_png_to_jpeg():
    jpeg = normalize_image(make_image("PNG"))
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (8, 8)


def test_normalize_webp():
    jpeg = normalize_image(make_image("WEBP"))
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG"


def test_normalize_multiframe_gif_uses_first_frame():
    buf = io.BytesIO()
    frames = [Image.new("RGB", (8, 8), (i, 0, 0)) for i in (10, 200)]
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=[frames[1]], duration=100
    )
    jpeg = normalize_image(buf.getvalue())
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG"
        assert image.size == (8, 8)


def test_normalize_rejects_garbage():
    with pytest.raises(ImageError):
        normalize_image(b"this is not an image")


def test_prepare_document_owns_partial_success_and_diagnostics(tmp_path):
    md_file = tmp_path / "doc.md"
    (tmp_path / "good.png").write_bytes(make_image("PNG"))
    markdown = "![good](good.png)\n![missing](missing.png)"

    result = prepare_document(
        markdown,
        md_file,
        tmp_path / "out",
        download=False,
    )

    assert result.markdown.startswith("![good](assets/doc/good-")
    assert result.markdown.endswith("![missing](missing.png)")
    assert not result.warnings
    assert len(result.errors) == 1
    assert "image file not found" in result.errors[0]


def test_prepare_one_local_writes_deterministic_asset(tmp_path):
    md_file = tmp_path / "doc.md"
    img = tmp_path / "pic.png"
    img.write_bytes(make_image("PNG"))
    md_file.write_text("x")
    output = tmp_path / "out"

    first = prepare_one(ref_of("![a](pic.png)"), md_file, output, download=False)
    second = prepare_one(ref_of("![a](pic.png)"), md_file, output, download=False)

    assert first.source == second.source  # deterministic, collision-safe
    assert first.source.startswith("assets/doc/pic-")
    assert first.source.endswith(".jpg")
    assert first.path.is_file()
    with Image.open(first.path) as image:
        assert image.format == "JPEG"
    assert first.source == first.path.relative_to(output).as_posix()


def test_prepare_one_default_tiff_extension_is_case_insensitive(tmp_path):
    md_file = tmp_path / "doc.md"
    (tmp_path / "scan.TIFF").write_bytes(make_image("TIFF"))
    md_file.write_text("x")

    prepared = prepare_one(
        ref_of("![a](scan.TIFF)"), md_file, tmp_path / "out", download=False
    )

    with Image.open(prepared.path) as image:
        assert image.format == "JPEG"


def test_prepare_one_jpeg_copies_original_bytes(tmp_path):
    md_file = tmp_path / "doc.md"
    source = make_image("JPEG")
    (tmp_path / "photo.jpeg").write_bytes(source)
    md_file.write_text("x")

    prepared = prepare_one(
        ref_of("![a](photo.jpeg)"), md_file, tmp_path / "out", download=False
    )

    assert prepared.path.read_bytes() == source


def test_prepare_one_rejects_content_not_matching_enabled_extension(tmp_path):
    md_file = tmp_path / "doc.md"
    (tmp_path / "wrong.png").write_bytes(make_image("JPEG"))
    md_file.write_text("x")

    with pytest.raises(ImageError, match="not PNG"):
        prepare_one(
            ref_of("![a](wrong.png)"), md_file, tmp_path / "out", download=False
        )


def test_prepare_one_remote_without_download_skips(tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("x")
    ref = ref_of("![a](https://example.com/a.png)")
    with pytest.raises(ImageSkip):
        prepare_one(ref, md_file, tmp_path / "out", download=False)


def test_prepare_one_remote_with_download(tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("x")

    class FakeResp:
        status_code = 200
        content = make_image("PNG")

        def raise_for_status(self):
            pass

    def handler(method, url, **kwargs):
        assert url == "https://example.com/a.png"
        return FakeResp()

    session = FakeSession(handler)
    prepared = prepare_one(
        ref_of("![a](https://example.com/a.png)"),
        md_file,
        tmp_path / "out",
        download=True,
        session=session,
    )
    assert prepared.path.is_file()


def test_prepare_one_missing_local_file_raises(tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("x")
    with pytest.raises(ImageError):
        prepare_one(ref_of("![a](nope.png)"), md_file, tmp_path / "out", download=False)


def test_prepare_one_unsupported_form_skips(tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("x")
    ref = ref_of("![a](/abs/pic.png)")
    with pytest.raises(ImageSkip):
        prepare_one(ref, md_file, tmp_path / "out", download=False)


def test_prepare_one_truncated_image_raises(tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("x")
    (tmp_path / "bad.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"truncated")
    with pytest.raises(ImageError):
        prepare_one(ref_of("![a](bad.png)"), md_file, tmp_path / "out", download=False)


def test_asset_name_uses_source_stem_and_digest(tmp_path):
    md_file = tmp_path / "doc.md"
    img = tmp_path / "architecture.png"
    img.write_bytes(make_image("PNG"))
    md_file.write_text("x")
    prepared = prepare_one(
        ref_of("![a](architecture.png)"), md_file, tmp_path / "out", download=False
    )
    assert prepared.source == "assets/doc/architecture-c8f540e9.jpg"


def test_open_prepared_reopens_valid_jpeg(tmp_path):
    output = tmp_path / "out"
    asset = output / "assets" / "doc" / "pic.jpg"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(make_image("JPEG"))

    prepared = open_prepared("assets/doc/pic.jpg", output)

    assert prepared.path == asset.resolve()
    assert prepared.source == "assets/doc/pic.jpg"


@pytest.mark.parametrize(
    "source, message",
    [
        ("../pic.jpg", "outside output tree"),
        ("pic.jpg", "canonical .jpg path under assets"),
        ("assets/doc/pic.png", "canonical .jpg path under assets"),
    ],
)
def test_open_prepared_rejects_noncanonical_source(tmp_path, source, message):
    with pytest.raises(ImageError, match=message):
        open_prepared(source, tmp_path / "out")


def test_open_prepared_rejects_invalid_jpeg(tmp_path):
    output = tmp_path / "out"
    asset = output / "assets" / "doc" / "pic.jpg"
    asset.parent.mkdir(parents=True)
    asset.write_text("not jpeg")

    with pytest.raises(ImageError, match="prepared JPEG asset invalid"):
        open_prepared("assets/doc/pic.jpg", output)
