"""Check Git-visible engineering Markdown without touching ignored corpus data."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT_DOCUMENTS = {
    "AGENTS.md",
    "README.md",
    "GLOSSARY.md",
    "GLOSSARY-MAP.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE.md",
    "LEGAL.md",
}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+\S")
Issue = tuple[int, str]


def document_paths(root: Path) -> list[Path]:
    """Include tracked files and new, non-ignored documents; never walk the corpus."""
    output = subprocess.check_output(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            ":(icase)*.md",
        ],
        cwd=root,
    )
    return sorted({Path(os.fsdecode(name)) for name in output.split(b"\0") if name})


def check_path(path: Path) -> list[Issue]:
    if path.parent == Path(".") and path.name in ROOT_DOCUMENTS:
        return []
    if path == Path("skylark_books/roots.md"):
        return []
    if path.parts[0] not in {"docs", ".scratch"} or len(path.parts) < 2:
        return [(1, "put engineering documents in docs/ (or .scratch/ for tickets)")]
    if path.suffix != ".md" or not all(
        SLUG.fullmatch(part) for part in (*path.parts[1:-1], path.stem)
    ):
        return [(1, "use lowercase kebab-case directories and .md filenames")]
    return []


def check_content(content: bytes) -> list[Issue]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return [(1, "use UTF-8 encoding")]
    issues: list[Issue] = []
    if text.startswith("\ufeff"):
        issues.append((1, "remove the UTF-8 byte-order mark"))
    if "\r" in text:
        issues.append((1, "use LF line endings"))
    if not text.endswith("\n") or text.endswith("\n\n"):
        issues.append((max(1, len(text.splitlines())), "end with exactly one newline"))
    fence: str | None = None
    fence_line = 0
    titles = []
    first_nonblank = None
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip() and first_nonblank is None:
            first_nonblank = number
        marker = FENCE.match(line)
        if marker:
            token, rest = marker.groups()
            if fence is None:
                fence, fence_line = token, number
            elif token[0] == fence[0] and len(token) >= len(fence) and not rest.strip():
                fence = None
            continue
        if fence is not None:
            continue
        if "\t" in line:
            issues.append((number, "use spaces instead of tabs outside code fences"))
        trailing = line[len(line.rstrip(" \t")) :]
        if trailing and (trailing != "  " or not line.strip()):
            issues.append(
                (number, "remove trailing whitespace (two-space hard breaks allowed)")
            )
        heading = HEADING.match(line)
        if heading and len(heading.group(1)) == 1:
            titles.append(number)
    if len(titles) != 1:
        issues.append((1, "use exactly one H1 title outside code fences"))
    elif titles[0] != first_nonblank:
        issues.append((titles[0], "make the H1 title the first nonblank line"))
    if fence is not None:
        issues.append((fence_line, "close the fenced code block"))
    return issues


def run(root: Path) -> int:
    failures = []
    checked = 0
    for relative in document_paths(root):
        path = root / relative
        if not path.exists() and not path.is_symlink():
            continue  # A tracked deletion in the working tree is not a document.
        checked += 1
        issues = check_path(relative)
        if path.is_symlink():
            issues.append((1, "engineering documents must not be symlinks"))
        else:
            try:
                issues.extend(check_content(path.read_bytes()))
            except OSError as error:
                issues.append((1, f"cannot read document: {error.strerror}"))
        failures.extend(f"{relative}:{line}: {message}" for line, message in issues)
    for failure in failures:
        print(failure)
    if failures:
        print(f"doc-guard: FAIL ({len(failures)} issues in {checked} documents)")
        return 1
    print(f"doc-guard: PASS ({checked} documents)")
    return 0


def main() -> int:
    try:
        return run(Path(__file__).resolve().parents[1])
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"doc-guard: cannot list Git documents: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
