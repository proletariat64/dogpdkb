"""End-to-end CLI tests via mdocr.cli.main. No network, no real token."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdocr import cli

from .conftest import make_image


def run_main(monkeypatch, args, env_token=None):
    if env_token:
        monkeypatch.setenv("PADDLEOCR_TOKEN", env_token)
    return cli.main(args)


def write_doc(tmp_path, body, images=None, name="in.md") -> Path:
    md_file = tmp_path / name
    md_file.write_text(body)
    for image_name, data in (images or {}).items():
        (tmp_path / image_name).write_bytes(data)
    return md_file


def test_no_args_shows_help(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "usage: mdocr" in out
    assert "prepare" in out
    assert "ocr" in out


def test_help_flag_exits_zero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["-h"])
    assert exc.value.code == 0


def test_missing_input_file(tmp_path, capsys):
    code = cli.main(
        ["prepare", "-f", str(tmp_path / "nope.md"), "-o", str(tmp_path / "out")]
    )
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_duplicate_basenames_rejected(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "same.md").write_text("x")
    (b / "same.md").write_text("y")
    code = cli.main(
        [
            "prepare",
            "-f",
            str(a / "same.md"),
            str(b / "same.md"),
            "-o",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2


def test_ocr_requires_token(tmp_path):
    write_doc(tmp_path, "text")
    code = cli.main(["ocr", "-f", str(tmp_path / "in.md"), "-o", str(tmp_path / "out")])
    assert code == 2


def test_output_collision_requires_force(tmp_path):
    write_doc(tmp_path, "text")
    out = tmp_path / "out"
    out.mkdir()
    (out / "in.md").write_text("old")
    code = cli.main(["prepare", "-f", str(tmp_path / "in.md"), "-o", str(out)])
    assert code == 2
    # With --force the existing output is replaced.
    write_doc(tmp_path, "![a](pic.png)", images={"pic.png": make_image("PNG")})
    code = cli.main(
        ["prepare", "-f", str(tmp_path / "in.md"), "-o", str(out), "--force"]
    )
    assert code == 0
    assert "![a](assets/in/pic-" in (out / "in.md").read_text()


def test_prepare_end_to_end(tmp_path, capsys):
    write_doc(
        tmp_path,
        "![a](pic.png 't') and ![r](https://x.example/i.png)",
        images={"pic.png": make_image("PNG")},
    )
    out = tmp_path / "out"
    code = cli.main(["prepare", "-f", str(tmp_path / "in.md"), "-o", str(out)])
    assert code == 0
    result = (out / "in.md").read_text()
    assert "![a](assets/in/pic-" in result
    assert ' "t"' in result  # title preserved, re-rendered with double quotes
    assert "https://x.example/i.png" in result  # remote kept
    assert (out / "assets/in").is_dir()
    err = capsys.readouterr().err
    assert "remote image" in err  # warning, not failure


def test_prepare_download_flag(tmp_path, monkeypatch):
    write_doc(tmp_path, "![a](https://x.example/i.png)")
    out = tmp_path / "out"

    import requests

    class FakeResp:
        status_code = 200
        content = make_image("PNG")

        def raise_for_status(self):
            pass

    class FakeSessionClass:
        def get(self, url, timeout):
            return FakeResp()

    monkeypatch.setattr(requests, "Session", FakeSessionClass)

    code = cli.main(
        ["prepare", "-f", str(tmp_path / "in.md"), "-o", str(out), "--download"]
    )
    assert code == 0
    # The remote URL was replaced by a local JPEG asset.
    assert "https://x.example/i.png" not in (out / "in.md").read_text()
    assert list((out / "assets/in").glob("*.jpg"))


def test_ocr_end_to_end(tmp_path, patch_client, ocr_env):
    stub = patch_client()
    write_doc(
        tmp_path,
        "intro\n\n![a](pic.png)\n\noutro",
        images={"pic.png": make_image("PNG")},
    )
    out = tmp_path / "out"
    code = cli.main(["ocr", "-f", str(tmp_path / "in.md"), "-o", str(out)])
    assert code == 0
    result = (out / "in.md").read_text()
    assert result.startswith("intro\n\n<!-- mdocr:ocr-begin")
    assert "> Original image: [assets/in/pic-" in result
    assert result.endswith("outro")
    assert len(stub.calls) == 1


def test_ocr_rerun_without_force_is_rejected(tmp_path, patch_client, ocr_env):
    stub = patch_client()
    write_doc(tmp_path, "![a](pic.png)", images={"pic.png": make_image("PNG")})
    out = tmp_path / "out"
    assert cli.main(["ocr", "-f", str(tmp_path / "in.md"), "-o", str(out)]) == 0
    first = (out / "in.md").read_text()
    # Existing output is preserved; rerun requires --force.
    assert cli.main(["ocr", "-f", str(tmp_path / "in.md"), "-o", str(out)]) == 2
    assert (out / "in.md").read_text() == first
    assert len(stub.calls) == 1  # second run submitted nothing


def test_ocr_rerun_with_force_reocrs_blocks(tmp_path, patch_client, ocr_env):
    stub = patch_client()
    write_doc(tmp_path, "![a](pic.png)", images={"pic.png": make_image("PNG")})
    out = tmp_path / "out"
    assert cli.main(["ocr", "-f", str(tmp_path / "in.md"), "-o", str(out)]) == 0
    assert (
        cli.main(["ocr", "-f", str(tmp_path / "in.md"), "-o", str(out), "--force"]) == 0
    )
    result = (out / "in.md").read_text()
    assert "OCR of pic-" in result  # blocks freshly OCRed from their recorded JPEG
    assert len(stub.calls) == 2


def test_ocr_failure_writes_partial_and_exits_nonzero(tmp_path, patch_client, ocr_env):
    patch_client(fail={"pic-"})
    write_doc(tmp_path, "![a](pic.png)", images={"pic.png": make_image("PNG")})
    out = tmp_path / "out"
    code = cli.main(["ocr", "-f", str(tmp_path / "in.md"), "-o", str(out)])
    assert code == 1
    # Partial output still written; failed embed preserved.
    assert (out / "in.md").read_text() == "![a](pic.png)"


def test_multiple_files(tmp_path):
    write_doc(
        tmp_path, "![a](pic.png)", images={"pic.png": make_image("PNG")}, name="one.md"
    )
    write_doc(
        tmp_path, "![b](pic.png)", images={"pic.png": make_image("PNG")}, name="two.md"
    )
    out = tmp_path / "out"
    code = cli.main(
        [
            "prepare",
            "-f",
            str(tmp_path / "one.md"),
            str(tmp_path / "two.md"),
            "-o",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "one.md").is_file()
    assert (out / "two.md").is_file()
