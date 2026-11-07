"""Offline byte evidence and independent resource-use comparison."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .io import InputError, InputReader
from .model import CompareOptions, Document

MAX_RESOURCE_REFERENCES = 10_000
INLINE = re.compile(
    r'(!?)\[([^\]\n]*)\]\((?:<([^>\n]+)>|([^\s)]+))(?:\s+([\'"])(.*?)\5)?\)'
)
REFERENCE = re.compile(r"(!?)\[([^\]\n]*)\](?:\[([^\]\n]*)\])?")
DEFINITION = re.compile(
    r'^ {0,3}\[([^\]]+)\]:[ \t]*(?:<([^>]+)>|(\S+))(?:[ \t]+([\'"])(.*?)\4)?[ \t]*$',
    re.MULTILINE,
)
DOWNLOAD_SUFFIXES = {
    ".pdf",
    ".csv",
    ".tsv",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".zip",
    ".gz",
    ".tar",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".mp3",
    ".mp4",
    ".wav",
    ".webm",
}
RESOURCE_TAG = re.compile(
    r"<(img|video|audio|source|iframe|object|embed|script|link|image|use)\b",
    re.IGNORECASE,
)


def _label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _download(url: str) -> bool:
    try:
        path = unquote(urlsplit(url).path).lower()
    except ValueError:
        return True  # A malformed target cannot establish a resource-free document.
    return any(path.endswith(suffix) for suffix in DOWNLOAD_SUFFIXES)


def _escaped(text: str, offset: int) -> bool:
    start = offset
    while start > 0 and text[start - 1] == "\\":
        start -= 1
    return (offset - start) % 2 == 1


def _spaces(fragment: str) -> str:
    return "".join(char if char in "\r\n" else " " for char in fragment)


def _mask_spans(line: str) -> str:
    """Mask only exact-run code spans proven within one physical line."""
    runs = list(re.finditer(r"`+", line))
    following = {}
    closers = {}
    for index in range(len(runs) - 1, -1, -1):
        length = len(runs[index].group())
        closers[index] = following.get(length)
        following[length] = index
    # Backticks inside an HTML-looking tag are not proven span openers.
    angles = list(re.finditer(r"<[^>\r\n]*(?:>|$)", line))
    angle_index = 0
    index = 0
    parts = []
    previous = 0
    while index < len(runs):
        run = runs[index]
        while angle_index < len(angles) and angles[angle_index].end() <= run.start():
            angle_index += 1
        in_angle = (
            angle_index < len(angles) and angles[angle_index].start() <= run.start()
        )
        end_index = closers[index]
        if _escaped(line, run.start()) or in_angle or end_index is None:
            index += 1
            continue
        end = runs[end_index].end()
        parts.extend((line[previous : run.start()], _spaces(line[run.start() : end])))
        previous = end
        index = end_index + 1
    parts.append(line[previous:])
    return "".join(parts)


def _mask_code(text: str) -> str:
    lines = text.splitlines(keepends=True)
    fence = None
    prior_html = False
    for i, line in enumerate(lines):
        if fence is not None:
            marker = re.fullmatch(r" {0,3}(`+|~+)[ \t]*(?:\r?\n)?", line)
            if (
                marker
                and marker.group(1)[0] == fence[0]
                and len(marker.group(1)) >= len(fence)
            ):
                fence = None
            lines[i] = _spaces(line)
            continue
        # Container-indented or HTML-context fences are not proven top-level code.
        marker = re.fullmatch(r"(`{3,}|~{3,})([^\r\n]*)(?:\r?\n)?", line)
        if (
            marker
            and not prior_html
            and not (marker.group(1)[0] == "`" and "`" in marker.group(2))
        ):
            fence = marker.group(1)
            lines[i] = _spaces(line)
        else:
            lines[i] = _mask_spans(line)
            prior_html = prior_html or "<" in line
    return "".join(lines)


class _EmbeddedResources(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.uses: list[dict[str, Any]] = []
        self.reasons: list[str] = []
        self.tags = 0

    def handle_starttag(self, tag, attrs):
        pairs = {name: value or "" for name, value in attrs}
        relevant = tag in {
            "img",
            "video",
            "audio",
            "source",
            "iframe",
            "object",
            "embed",
            "script",
            "link",
            "image",
            "use",
        }
        if relevant:
            self.tags += 1
        if len(pairs) != len(attrs) and relevant:
            self.reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
        targets = []
        if tag in {"img", "video", "audio", "source", "iframe", "embed", "script"}:
            if pairs.get("src"):
                targets.append(("image" if tag == "img" else "media", pairs["src"]))
            if pairs.get("srcset"):
                # Commas in data URLs need a richer parser: preserve uncertainty.
                if "data:" in pairs["srcset"]:
                    self.reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
                else:
                    targets.extend(
                        ("image", part.strip().split()[0])
                        for part in pairs["srcset"].split(",")
                        if part.strip()
                    )
            if pairs.get("poster"):
                targets.append(("image", pairs["poster"]))
        elif tag == "object" and pairs.get("data"):
            targets.append(("media", pairs["data"]))
        elif tag in {"image", "use"}:
            target = pairs.get("href") or pairs.get("xlink:href")
            if target and not target.startswith("#"):
                targets.append(("image", target))
        elif (
            tag == "link"
            and pairs.get("href")
            or tag == "a"
            and pairs.get("href")
            and ("download" in pairs or _download(pairs["href"]))
        ):
            targets.append(("attachment", pairs["href"]))
        if relevant and not targets:
            self.reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
        for kind, url in targets:
            self.uses.append(
                {
                    "kind": kind,
                    "url": url,
                    "alt": pairs.get("alt", ""),
                    "title": pairs.get("title"),
                    "offset": self.line_starts[self.getpos()[0] - 1] + self.getpos()[1],
                }
            )

    handle_startendtag = handle_starttag


def _uses(
    data: bytes, declarations: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        text = _mask_code(data.decode("utf-8"))
    except UnicodeDecodeError:
        return [], ["RESOURCE_SYNTAX_UNVERIFIED"]
    if text.count("![") + len(RESOURCE_TAG.findall(text)) > MAX_RESOURCE_REFERENCES:
        return [], ["RESOURCE_BUDGET_EXCEEDED"]
    declared_urls = {entry["url"] for entry in declarations.values()}
    uses = []
    reasons = []
    covered: list[tuple[int, int]] = []
    definitions = {}
    for m in DEFINITION.finditer(text):
        label = _label(m.group(1))
        if label in definitions:
            reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
        definitions[label] = (m.group(2) or m.group(3), m.group(5))
        covered.append(m.span())
    for m in INLINE.finditer(text):
        url = m.group(3) or m.group(4)
        escaped = _escaped(text, m.start())
        if escaped and not m.group(1):
            continue
        kind = "image" if m.group(1) and not escaped else "attachment"
        if "(" in url and m.group(3) is None:
            reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
        if kind == "image" or _download(url) or url in declared_urls:
            uses.append(
                {
                    "kind": kind,
                    "url": url,
                    "alt": m.group(2),
                    "title": m.group(6),
                    "offset": m.start(),
                }
            )
        covered.append(m.span())
    reference_text = list(text)
    for start, end in covered:
        reference_text[start:end] = " " * (end - start)
    unclaimed_text = "".join(reference_text)
    for m in re.finditer(r"<(https?://[^>\s]+)>", unclaimed_text):
        if _download(m.group(1)):
            uses.append(
                {
                    "kind": "attachment",
                    "url": m.group(1),
                    "alt": "",
                    "title": None,
                    "offset": m.start(),
                }
            )
    for m in REFERENCE.finditer(unclaimed_text):
        escaped = _escaped(text, m.start())
        if escaped and not m.group(1):
            continue
        image = bool(m.group(1)) and not escaped
        target = definitions.get(_label(m.group(3) or m.group(2)))
        if image and target is None:
            reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
        elif target is not None:
            url, title = target
            if m.group(1) or _download(url) or url in declared_urls:
                uses.append(
                    {
                        "kind": "image" if image else "attachment",
                        "url": url,
                        "alt": m.group(2),
                        "title": title,
                        "offset": m.start(),
                    }
                )
    image_count = sum(not _escaped(text, m.start()) for m in re.finditer(r"!\[", text))
    if image_count != sum(u["kind"] == "image" for u in uses):
        reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
    html = _EmbeddedResources(text)
    try:
        html.feed(text)
        html.close()
    except (ValueError, AssertionError):
        reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
    if len(RESOURCE_TAG.findall(text)) != html.tags:
        reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
    if re.search(r"url\s*\(", text, re.IGNORECASE):
        reasons.append("RESOURCE_SYNTAX_UNVERIFIED")
    uses.extend(html.uses)
    uses.sort(key=lambda use: use["offset"])
    reasons.extend(html.reasons)
    if len(uses) > MAX_RESOURCE_REFERENCES:
        return [], ["RESOURCE_BUDGET_EXCEEDED"]
    return uses, reasons


def _catalog(doc: Document | None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    entries = {}
    reasons = []
    if doc and len(doc.resources) > MAX_RESOURCE_REFERENCES:
        return {}, ["RESOURCE_BUDGET_EXCEEDED"]
    for entry in doc.resources if doc else []:
        identifier = entry.get("resource_id")
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier in entries
            or entry.get("kind") not in ("image", "attachment", "media")
            or not isinstance(entry.get("url"), str)
            or not entry["url"]
        ):
            reasons.append("RESOURCE_DECLARATION_INVALID")
            continue
        entries[identifier] = entry
    return entries, reasons


def _immutable_version(entry: dict[str, Any]) -> tuple[str, str, str] | None:
    """Only an explicitly captured non-null S3 object version is recognized."""
    evidence = entry.get("immutable")
    if (
        not isinstance(evidence, dict)
        or evidence.get("contract") != "s3-version-id-v1"
        or evidence.get("captured") is not True
    ):
        return None
    try:
        url = urlsplit(entry["url"])
        query = parse_qs(url.query, keep_blank_values=True)
        host = re.fullmatch(
            r"([a-z0-9][a-z0-9.-]*)\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com", url.netloc
        )
        versions = query.get("versionId", [])
        if (
            url.scheme != "https"
            or host is None
            or set(query) != {"versionId"}
            or len(versions) != 1
            or not versions[0]
            or versions[0].lower() == "null"
            or not url.path.startswith("/")
            or len(url.path) <= 1
        ):
            return None
        identity = host.group(1), unquote(url.path[1:]), versions[0]
        if identity != (
            evidence.get("bucket"),
            evidence.get("key"),
            evidence.get("version_id"),
        ):
            return None
        return identity
    except ValueError:
        return None


def compare_resources(
    old: Document | None,
    new: Document,
    old_bytes: bytes | None,
    new_bytes: bytes,
    reader: InputReader,
    options: CompareOptions,
) -> dict[str, Any]:
    old_entries, old_invalid = _catalog(old)
    new_entries, new_invalid = _catalog(new)
    before, old_reasons = _uses(old_bytes or b"", old_entries)
    after, new_reasons = _uses(new_bytes, new_entries)
    reasons = old_reasons + new_reasons + old_invalid + new_invalid
    items = []
    positions = []
    for side, entries in ((before, old_entries), (after, new_entries)):
        lookup = Counter((entry["url"], entry["kind"]) for entry in entries.values())
        side_positions = defaultdict(list)
        for index, use in enumerate(side):
            key = use["url"], use["kind"]
            side_positions[key].append(index)
            if lookup[key] != 1:
                reasons.append("RESOURCE_REFERENCE_UNMAPPED")
        positions.append(side_positions)
    for identifier in sorted(old_entries.keys() | new_entries.keys()):
        previous = old_entries.get(identifier)
        current = new_entries.get(identifier)
        entry = current if current is not None else previous
        assert entry is not None
        item = {
            "resource_id": identifier,
            "kind": entry["kind"],
            "status": "UNKNOWN",
            "reason_codes": [],
            "basis": None,
            "occurrences_before": positions[0].get(
                (previous["url"], previous["kind"]), []
            )
            if previous
            else [],
            "occurrences_after": positions[1].get((current["url"], current["kind"]), [])
            if current
            else [],
        }
        try:
            previous_data = (
                reader.read_bytes(old.resolve(previous["path"]))
                if old
                and previous
                and isinstance(previous.get("path"), str)
                and previous["path"]
                else None
            )
            current_data = (
                reader.read_bytes(new.resolve(current["path"]))
                if current and isinstance(current.get("path"), str) and current["path"]
                else None
            )
            if current is None:
                item.update(status="CHANGED", operation="removed")
            elif previous is None and (
                current_data is not None or _immutable_version(current) is not None
            ):
                item.update(
                    status="NEW",
                    basis="local-bytes"
                    if current_data is not None
                    else "s3-version-id-v1",
                )
            elif previous_data is not None and current_data is not None:
                item.update(
                    status="UNCHANGED" if previous_data == current_data else "CHANGED",
                    basis="local-bytes",
                )
            elif (
                previous
                and previous_data is None
                and current_data is None
                and _immutable_version(previous) is not None
                and _immutable_version(previous) == _immutable_version(current)
            ):
                item.update(status="UNCHANGED", basis="s3-version-id-v1")
            else:
                item["reason_codes"].append("RESOURCE_BYTES_UNAVAILABLE")
        except InputError as exc:
            item["reason_codes"].append(exc.reason_code)
        reasons.extend(item["reason_codes"])
        items.append(item)
    status = (
        "UNKNOWN"
        if reasons or any(i["status"] == "UNKNOWN" for i in items)
        else "NEW"
        if old is None and items
        else "CHANGED"
        if any(i["status"] in ("NEW", "CHANGED") for i in items)
        else "UNCHANGED"
        if items
        else "NOT_APPLICABLE"
    )
    return {
        "status": status,
        "reason_codes": sorted(set(reasons)),
        "items": items,
        "processing_required": [
            i["resource_id"]
            for i in items
            if i["status"] in ("NEW", "CHANGED") and i.get("operation") != "removed"
        ],
        "references_changed": before != after,
    }
