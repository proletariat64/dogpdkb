"""Inline Markdown image parsing and rewriting."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

# Inline Markdown image: ![alt](target "title") with optional angle-bracket target.
# Groups: 1=alt, 2=angle-bracket target, 3=plain target, 4=title quote, 5=title.
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:<([^>]+)>|([^\s)]+))(?:\s+(['\"])(.*?)\4)?\)")

# Forms we intentionally leave unchanged but report as warnings.
HTML_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
REF_IMAGE_RE = re.compile(r"!\[[^\]]*\]\[[^\]]*\]")

SUPPORTED_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class DocumentResult:
    """Observable result of processing one Markdown document."""

    markdown: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ImageRef:
    """A parsed inline Markdown image reference."""

    alt: str
    target: str  # unquoted, fragment-stripped URL or relative path
    title: str | None
    is_remote: bool
    unsupported: bool  # recognized syntax with an unsupported target form


def parse_image_match(match: re.Match[str]) -> ImageRef:
    raw = (match.group(2) or match.group(3)).split("#", 1)[0]
    parsed = urlparse(raw)
    if parsed.scheme in SUPPORTED_SCHEMES:
        is_remote, unsupported = True, False
        target = raw  # keep URL percent-encoding intact for fetching
    elif parsed.scheme or raw.startswith(("//", "/", "data:")):
        is_remote, unsupported = False, True
        target = raw
    else:
        is_remote, unsupported = False, False
        target = unquote(raw)  # filesystem path: decode %20 etc.
    return ImageRef(
        alt=match.group(1),
        target=target,
        title=match.group(5),
        is_remote=is_remote,
        unsupported=unsupported,
    )


def iter_image_matches(markdown: str) -> Iterator[tuple[re.Match[str], ImageRef]]:
    for match in IMAGE_RE.finditer(markdown):
        yield match, parse_image_match(match)


def render_image(alt: str, target: str, title: str | None) -> str:
    """Re-render an inline Markdown image for a rewritten target."""
    target_part = target if " " not in target else f"<{target}>"
    title_part = f' "{title}"' if title else ""
    return f"![{alt}]({target_part}{title_part})"


def transform_images(
    markdown: str,
    transform,
    *,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    warn: bool = True,
) -> tuple[str, list[str], list[str]]:
    """Rewrite image embeds via ``transform(ref)``.

    ``transform`` returns the replacement text, or ``None`` to keep the
    original embed untouched. Errors and warnings recorded by the caller
    (via the ``errors`` / ``warnings`` lists) are returned alongside the
    new text. Returns ``(new_text, warnings, errors)``.
    """
    if errors is None:
        errors = []
    if warnings is None:
        warnings = []
    if warn:
        for match in HTML_IMG_RE.finditer(markdown):
            warnings.append(f"HTML <img> left unchanged: {match.group(0)[:60]}")
        for match in REF_IMAGE_RE.finditer(markdown):
            warnings.append(
                f"reference-style image left unchanged: {match.group(0)[:60]}"
            )

    parts: list[str] = []
    last = 0
    for match, ref in iter_image_matches(markdown):
        parts.append(markdown[last : match.start()])
        try:
            replacement = transform(ref)
        except Exception as exc:  # noqa: BLE001 - isolate per-image failures
            errors.append(f"{ref.target}: {exc}")
            replacement = None
        parts.append(replacement if replacement is not None else match.group(0))
        last = match.end()
    parts.append(markdown[last:])
    return "".join(parts), warnings, errors
