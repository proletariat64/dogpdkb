"""Filename limits and failure safety for Markdown output writes."""

import os
from pathlib import Path

import pytest

from mdocr import cli


@pytest.fixture(params=["a", "鲸"], ids=["ascii", "utf8"])
def long_name(tmp_path, request):
    limit = os.pathconf(tmp_path, "PC_NAME_MAX")
    char = request.param
    available = limit - len(".md")
    count, padding = divmod(available, len(char.encode("utf-8")))
    name = char * count + "a" * padding + ".md"
    assert len(name.encode("utf-8")) == limit
    return name


def test_atomic_write_long_name(tmp_path, monkeypatch, long_name):
    destination = tmp_path / "md" / long_name
    destination.parent.mkdir()
    destination.write_text("old", encoding="utf-8")
    content = "# 新内容\nComplete output\n"
    replace = os.replace
    calls = []

    def check_replace(source, target):
        source, target = Path(source), Path(target)
        assert source.parent == destination.parent
        assert target == destination
        assert destination.read_text(encoding="utf-8") == "old"
        assert source.read_text(encoding="utf-8") == content
        calls.append(source)
        replace(source, target)

    monkeypatch.setattr(cli.os, "replace", check_replace)
    cli.atomic_write(destination, content)

    assert len(calls) == 1
    assert destination.read_text(encoding="utf-8") == content
    assert list(destination.parent.iterdir()) == [destination]


def test_ocr_cli_long_name(tmp_path, long_name, patch_client, ocr_env):
    patch_client()
    source = tmp_path / long_name
    content = "# Whale Configuration MCP\n测试内容\n"
    source.write_text(content, encoding="utf-8")
    output = tmp_path / "md"
    output.mkdir()
    (output / long_name).write_text("old", encoding="utf-8")

    assert (
        cli.main(["ocr", "-o", str(output), "--download", "--force", "-f", str(source)])
        == 0
    )
    assert (output / long_name).read_text(encoding="utf-8") == content
    assert source.read_text(encoding="utf-8") == content
    assert not list(output.glob(".*.tmp"))


@pytest.mark.parametrize("existing", [False, True])
def test_atomic_write_encoding_failure_cleans_temporary(tmp_path, existing):
    destination = tmp_path / "md" / "report.md"
    destination.parent.mkdir()
    if existing:
        destination.write_text("old", encoding="utf-8")

    with pytest.raises(UnicodeEncodeError):
        cli.atomic_write(destination, "partial\ud800")

    assert list(destination.parent.iterdir()) == ([destination] if existing else [])
    if existing:
        assert destination.read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_atomic_write_replace_failure_cleans_temporary(tmp_path, monkeypatch, error):
    destination = tmp_path / "report.md"
    destination.write_text("old", encoding="utf-8")

    def fail_replace(source, target):
        assert Path(source).read_text(encoding="utf-8") == "complete"
        raise error("replacement failed")

    monkeypatch.setattr(cli.os, "replace", fail_replace)
    with pytest.raises(error, match="replacement failed"):
        cli.atomic_write(destination, "complete")

    assert destination.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.iterdir()) == [destination]
