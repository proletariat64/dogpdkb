"""Unit tests for mdocr.ocr block construction and orchestration."""

from __future__ import annotations

import pytest

from mdocr.ocr import build_ocr_block, ocr_document

from .conftest import StubClient, make_image


def test_build_ocr_block_exact_structure():
    block = build_ocr_block("assets/doc/pic.jpg", "OCR text line")
    assert block == (
        '<!-- mdocr:ocr-begin source="assets/doc/pic.jpg" -->\n\n'
        "OCR text line\n\n"
        "> Original image: [assets/doc/pic.jpg](assets/doc/pic.jpg)\n\n"
        "<!-- mdocr:ocr-end -->"
    )


def test_ocr_document_replaces_embed_with_block(tmp_path):
    md_file = tmp_path / "doc.md"
    (tmp_path / "pic.png").write_bytes(make_image("PNG"))
    md_file.write_text("before ![a](pic.png) after")
    client = StubClient("test-token")
    output = tmp_path / "out"

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=False
    )

    new_text, warnings, errors = result.markdown, result.warnings, result.errors

    assert not errors
    assert not warnings
    assert new_text.startswith("before ")
    assert new_text.endswith(" after")
    block_start = new_text.index("<!-- mdocr:ocr-begin")
    assert new_text[block_start:].startswith(
        '<!-- mdocr:ocr-begin source="assets/doc/pic-'
    )
    assert "> Original image: [" in new_text
    # The OCR block references a real prepared JPEG under the output tree.
    assert len(client.calls) == 1
    assert client.calls[0].is_file()
    assert str(client.calls[0]).endswith(".jpg")


def test_ocr_document_skips_remote_without_download(tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("![a](https://example.com/a.png)")
    client = StubClient("test-token")

    result = ocr_document(
        md_file.read_text(),
        md_file,
        tmp_path / "out",
        client,
        download=False,
        force=False,
    )

    new_text, warnings, errors = result.markdown, result.warnings, result.errors

    assert not errors
    assert len(warnings) == 1
    assert "remote image" in warnings[0]
    assert new_text == "![a](https://example.com/a.png)"
    assert client.calls == []


def test_ocr_document_failed_image_keeps_embed(tmp_path):
    md_file = tmp_path / "doc.md"
    (tmp_path / "pic.png").write_bytes(make_image("PNG"))
    md_file.write_text("![a](pic.png)")
    client = StubClient("test-token", fail={"pic-": "x"})

    result = ocr_document(
        md_file.read_text(),
        md_file,
        tmp_path / "out",
        client,
        download=False,
        force=False,
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert new_text == "![a](pic.png)"  # embed preserved
    assert len(errors) == 1
    assert "OCR failed" in errors[0]


def test_ocr_document_missing_image_reports_error(tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("![a](nope.png)")
    client = StubClient("test-token")

    result = ocr_document(
        md_file.read_text(),
        md_file,
        tmp_path / "out",
        client,
        download=False,
        force=False,
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert new_text == "![a](nope.png)"
    assert len(errors) == 1
    assert "image file not found" in errors[0]


def test_ocr_document_idempotent_on_existing_blocks(tmp_path):
    block = build_ocr_block("assets/doc/pic-aaaaaaaa.jpg", "OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(f"text\n\n{block}\n")
    client = StubClient("test-token")
    output = tmp_path / "out"
    (output / "assets/doc").mkdir(parents=True)
    (output / "assets/doc/pic-aaaaaaaa.jpg").write_bytes(b"x")

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=False
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert new_text == f"text\n\n{block}\n"  # untouched
    assert not errors
    assert client.calls == []  # no re-OCR


def test_ocr_document_force_reocrs_existing_blocks(tmp_path):
    block = build_ocr_block("assets/doc/pic-aaaaaaaa.jpg", "OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(f"text\n\n{block}\n")
    client = StubClient("test-token")
    output = tmp_path / "out"
    (output / "assets/doc").mkdir(parents=True)
    (output / "assets/doc/pic-aaaaaaaa.jpg").write_bytes(make_image("JPEG"))

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=True
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert not errors
    assert "OLD OCR" not in new_text
    assert "OCR of pic-aaaaaaaa.jpg" in new_text
    assert len(client.calls) == 1
    assert str(client.calls[0]).endswith("assets/doc/pic-aaaaaaaa.jpg")


def test_ocr_document_force_keeps_block_on_reocr_failure(tmp_path):
    block = build_ocr_block("assets/doc/pic-aaaaaaaa.jpg", "OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(block)
    client = StubClient("test-token", fail={"pic-aaaaaaaa.jpg"})
    output = tmp_path / "out"
    (output / "assets/doc").mkdir(parents=True)
    (output / "assets/doc/pic-aaaaaaaa.jpg").write_bytes(make_image("JPEG"))

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=True
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert block in new_text  # preserved on failure
    assert len(errors) == 1


@pytest.mark.parametrize(
    ("source", "image_format"),
    [("elsewhere/pic.jpg", "JPEG"), ("assets/doc/pic.png", "PNG")],
)
def test_ocr_document_force_rejects_non_prepared_asset_paths(
    tmp_path, source, image_format
):
    block = build_ocr_block(source, "OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(block)
    client = StubClient("test-token")
    output = tmp_path / "out"
    asset = output / source
    asset.parent.mkdir(parents=True)
    asset.write_bytes(make_image(image_format))

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=True
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert new_text == block
    assert len(errors) == 1
    assert "canonical .jpg path under assets" in errors[0]
    assert client.calls == []


def test_ocr_document_force_rejects_invalid_prepared_jpeg(tmp_path):
    block = build_ocr_block("assets/doc/pic.jpg", "OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(block)
    client = StubClient("test-token")
    output = tmp_path / "out"
    asset = output / "assets/doc/pic.jpg"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"not a JPEG")

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=True
    )

    new_text, _, errors = result.markdown, result.warnings, result.errors

    assert new_text == block
    assert len(errors) == 1
    assert "prepared JPEG asset invalid" in errors[0]
    assert client.calls == []


def test_ocr_document_force_rejects_asset_symlink_outside_assets(tmp_path):
    block = build_ocr_block("assets/doc/pic.jpg", "OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(block)
    client = StubClient("test-token")
    output = tmp_path / "out"
    outside_asset = output / "elsewhere.jpg"
    outside_asset.parent.mkdir(parents=True)
    outside_asset.write_bytes(make_image("JPEG"))
    asset = output / "assets/doc/pic.jpg"
    asset.parent.mkdir(parents=True)
    asset.symlink_to(outside_asset)

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=True
    )

    new_text, _, errors = result.markdown, result.warnings, result.errors

    assert new_text == block
    assert len(errors) == 1
    assert "canonical .jpg path under assets" in errors[0]
    assert client.calls == []


def test_ocr_document_ignores_images_inside_existing_blocks(tmp_path):
    block = build_ocr_block("assets/doc/pic-aaaaaaaa.jpg", "![x](inside.png) OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(f"text\n\n{block}\n")
    client = StubClient("test-token")
    output = tmp_path / "out"
    (output / "assets/doc").mkdir(parents=True)
    (output / "assets/doc/pic-aaaaaaaa.jpg").write_bytes(b"x")

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=False
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert new_text == f"text\n\n{block}\n"  # block (incl. inner image) untouched
    assert not errors
    assert client.calls == []


def test_ocr_document_force_rejects_source_outside_output(tmp_path):
    block = build_ocr_block("../outside.jpg", "OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(block)
    client = StubClient("test-token")
    output = tmp_path / "out"
    output.mkdir()

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=True
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert block in new_text  # rejected source preserved
    assert len(errors) == 1
    assert "outside output tree" in errors[0]
    assert client.calls == []


def test_ocr_document_force_rejects_absolute_source(tmp_path):
    block = build_ocr_block(str(tmp_path / "abs.jpg"), "OLD OCR")
    md_file = tmp_path / "doc.md"
    md_file.write_text(block)
    client = StubClient("test-token")
    output = tmp_path / "out"
    output.mkdir()

    result = ocr_document(
        md_file.read_text(), md_file, output, client, download=False, force=True
    )

    new_text, _warnings, errors = result.markdown, result.warnings, result.errors

    assert block in new_text
    assert len(errors) == 1
    assert "outside output tree" in errors[0]
    assert client.calls == []
