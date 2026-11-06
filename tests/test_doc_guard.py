"""Regression checks for the engineering-document gate."""

import subprocess
from pathlib import Path

import pytest

from scripts import doc_guard


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "AGENTS.md",
        "LEGAL.md",
        "docs/doc-guard.md",
        "docs/spec/mdocr.md",
        ".scratch/doc-guard/issues/01-add-check.md",
        "skylark_books/roots.md",
    ],
)
def test_document_locations(path):
    assert doc_guard.check_path(Path(path)) == []


@pytest.mark.parametrize(
    "path",
    [
        "notes.md",
        "docs/BadName.md",
        "docs/bad_name.md",
        "docs/BadDir/file.md",
        "docs/guide.MD",
        "graphify_flatten_ready/doc.md",
        "src/notes.md",
    ],
)
def test_invalid_document_locations(path):
    assert doc_guard.check_path(Path(path))


def test_valid_markdown_ignores_code_and_allows_hard_breaks():
    content = (
        b"# Guide\n\nA hard break  \nnext line\n\n"
        b"````markdown\n# Not another title\n```\n\tcode \n````\n"
    )
    assert doc_guard.check_content(content) == []


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff\n", "UTF-8 encoding"),
        ("\ufeff# Guide\n".encode(), "byte-order mark"),
        (b"# Guide\r\n", "LF line endings"),
        (b"# Guide", "exactly one newline"),
        (b"# Guide\n\n", "exactly one newline"),
        (b"# Guide\ntext \n", "trailing whitespace"),
        (b"# Guide\n  \ntext\n", "trailing whitespace"),
        (b"# Guide\n\ttext\n", "spaces instead of tabs"),
        (b"No title\n", "exactly one H1"),
        (b"# One\n# Two\n", "exactly one H1"),
        (b"intro\n# Guide\n", "first nonblank"),
        (b"# Guide\n~~~python\nprint(1)\n", "close the fenced"),
        (b"# Guide\n````\n```\n", "close the fenced"),
        (b"# Guide\n~~~\n```\n", "close the fenced"),
    ],
)
def test_format_failures(content, message):
    assert any(message in problem for _, problem in doc_guard.check_content(content))


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments])


def test_gate_checks_tracked_and_new_docs_but_not_ignored_corpus(tmp_path, capsys):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("graphify*/\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Guide\n", encoding="utf-8")
    git(tmp_path, "add", ".gitignore", "README.md")
    corpus = tmp_path / "graphify_flatten_ready"
    corpus.mkdir()
    (corpus / "bad.md").write_bytes(b"\xff")
    docs = tmp_path / "docs"
    docs.mkdir()
    bad = docs / "new-guide.md"
    bad.write_text("missing title\n", encoding="utf-8")
    assert doc_guard.document_paths(tmp_path) == [
        Path("README.md"),
        Path("docs/new-guide.md"),
    ]
    assert doc_guard.run(tmp_path) == 1
    assert "docs/new-guide.md:1:" in capsys.readouterr().out
    bad.write_text("# New guide\n", encoding="utf-8")
    assert doc_guard.run(tmp_path) == 0
    assert "PASS (2 documents)" in capsys.readouterr().out


def test_tracked_deletion_and_spaces_in_paths(tmp_path, capsys):
    git(tmp_path, "init", "-q")
    removed = tmp_path / "README.md"
    removed.write_text("# Guide\n", encoding="utf-8")
    git(tmp_path, "add", "README.md")
    removed.unlink()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "bad name.md").write_text("# Guide\n", encoding="utf-8")
    assert doc_guard.run(tmp_path) == 1
    assert "docs/bad name.md:1:" in capsys.readouterr().out


def test_symlink_is_rejected_without_reading_target(tmp_path, capsys):
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").symlink_to(tmp_path / "missing")
    assert doc_guard.run(tmp_path) == 1
    assert "must not be symlinks" in capsys.readouterr().out


def test_git_discovery_failure_is_nonzero(monkeypatch, capsys):
    def fail(root):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(doc_guard, "document_paths", fail)
    assert doc_guard.main() == 1
    assert "cannot list Git documents" in capsys.readouterr().out
