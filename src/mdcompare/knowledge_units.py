"""Located, body-only knowledge units and bounded, explainable alignment.

Normalized equality supports a narrow set of presentation/quantity equivalences.
Similarity only locates possible revisions; it never certifies semantic equality.
Images and explicitly marked OCR regions are absent from the body view.
"""

import html
import json
import re
import unicodedata
from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal, localcontext
from html.parser import HTMLParser


@dataclass(frozen=True)
class Unit:
    kind: str
    text: str
    key: str
    context: tuple[str, ...]
    start_line: int
    end_line: int


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+")
_BULLET = re.compile(r"^(\s*)[-+*]\s+(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_QUANTITY = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<magnitude>万亿|亿|万)?\s*"
    r"(?P<unit>人民币元|美元|港元|欧元|日元|万元|元|笔|个|次|人|天|日|年|秒|分钟|小时|[%％‰])"
    r"(?![A-Za-z0-9_]|\.\d)"
)
_TAG = r"<%s\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>"
_OCR_BEGIN = re.compile(r"<!--\s*mdocr:ocr-begin\b.*?-->", re.IGNORECASE | re.DOTALL)
_OCR_END = re.compile(r"<!--\s*mdocr:ocr-end\s*-->", re.IGNORECASE)
_WORKFLOW = re.compile(
    r"流程|步骤|顺序|\b(?:workflow|procedure|steps|sequence)\b", re.IGNORECASE
)
_CONDITION = re.compile(
    r"条件|适用|仅限|前提|除非|针对|对于|如果|主体|账户|企业|付款方|收款方|商户|角色|规则|当.*时|\b(?:if|when|unless|only|applies|applicable|for|scope|subject|role|actor|approver|payer|payee|account|company|enterprise|merchant|MerchantId)\b",
    re.IGNORECASE,
)
_COSMETIC_TAG = re.compile(
    r"<(?P<closing>/)?(?P<tag>font|span|u|div|p|center|b|strong|em|i)\b(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
    re.IGNORECASE,
)
_COSMETIC_TAGS = {
    "font",
    "span",
    "u",
    "div",
    "p",
    "center",
    "b",
    "strong",
    "em",
    "i",
    "br",
}
_ATTRIBUTE = re.compile(
    r"""\s+([A-Za-z_:][A-Za-z0-9_:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?"""
)
_COSMETIC_PROPERTIES = {
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "color",
    "background-color",
    "line-height",
    "letter-spacing",
    "text-align",
    "vertical-align",
    "width",
    "height",
    "table-layout",
    "border-collapse",
    "border-spacing",
    "border",
    "border-width",
    "border-style",
    "border-color",
    "padding",
    "margin",
}


def _cosmetic_attrs(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        if name not in {"style", "class", "id", "color", "face", "size"}:
            return False
        if name == "style":
            for declaration in (value or "").split(";"):
                if not declaration.strip():
                    continue
                prop, separator, setting = declaration.partition(":")
                prop = prop.strip().casefold()
                if not separator or (
                    prop not in _COSMETIC_PROPERTIES
                    and not (
                        prop == "text-decoration"
                        and setting.strip().casefold() in {"none", "underline"}
                    )
                ):
                    return False
    return True


def _cosmetic_markup(text: str) -> str:
    def replace(match):
        if match["closing"]:
            return ""
        attrs = []
        cursor = 0
        for attribute in _ATTRIBUTE.finditer(match["attrs"]):
            if match["attrs"][cursor : attribute.start()].strip():
                return match[0]
            attrs.append(
                (
                    attribute[1].casefold(),
                    next(
                        (
                            value
                            for value in attribute.groups()[1:]
                            if value is not None
                        ),
                        None,
                    ),
                )
            )
            cursor = attribute.end()
        if match["attrs"][cursor:].strip() or not _cosmetic_attrs(attrs):
            return match[0]
        return ""

    text = _COSMETIC_TAG.sub(replace, text)
    return re.sub(_TAG % "br", " ", text, flags=re.IGNORECASE)


class _SimpleTable(HTMLParser):
    """Canonicalize only flat, ordinary row/cell tables, retaining cell bindings."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.depth = 0
        self.tables = 0
        self.failed = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in {"table", "tr", "td", "th", "thead", "tbody", "tfoot"}:
            layout = {
                "width",
                "height",
                "align",
                "valign",
                "border",
                "cellpadding",
                "cellspacing",
                "frame",
                "rules",
                "rowspan",
                "colspan",
            }
            self.failed |= not _cosmetic_attrs(
                [(name, value) for name, value in attrs if name not in layout]
            )
        if tag == "table":
            self.depth += 1
            self.tables += 1
            self.failed |= self.depth != 1 or self.tables != 1
        elif tag in {"thead", "tbody", "tfoot"} and self.depth:
            return
        elif tag == "tr" and self.depth:
            self.failed |= self.row is not None
            self.row = []
        elif tag in {"td", "th"} and self.depth:
            self.failed |= self.row is None or self.cell is not None
            self.failed |= any(
                name in {"rowspan", "colspan"} and value != "1" for name, value in attrs
            )
            self.cell = []
        elif self.cell is not None:
            if tag in {"font", "span", "u"} and _cosmetic_attrs(attrs):
                return
            if tag in {"br", "p", "div"}:
                self.cell.append(" ")
            elif tag in {"b", "strong"}:
                self.cell.append("**")
            elif tag in {"em", "i"}:
                self.cell.append("*")
            elif tag == "code":
                self.cell.append("`")
            elif tag == "a" and isinstance(dict(attrs).get("href"), str):
                self.links.append(dict(attrs)["href"])
                self.cell.append("[")
            else:
                self.failed = True
        else:
            self.failed = True

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            if self.cell is None or self.row is None:
                self.failed = True
            else:
                self.row.append(_prose_key("".join(self.cell)))
            self.cell = None
        elif tag == "tr":
            if self.row is None or self.cell is not None:
                self.failed = True
            else:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table":
            self.depth -= 1
        elif tag in {"thead", "tbody", "tfoot"}:
            return
        elif self.cell is not None:
            if tag in {"font", "span", "u"}:
                return
            if tag in {"p", "div"}:
                self.cell.append(" ")
            elif tag in {"b", "strong"}:
                self.cell.append("**")
            elif tag in {"em", "i"}:
                self.cell.append("*")
            elif tag == "code":
                self.cell.append("`")
            elif tag == "a" and self.links:
                self.cell.append("](" + self.links.pop() + ")")
            else:
                self.failed = True
        else:
            self.failed = True

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        elif data.strip():
            self.failed = True

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.handle_starttag(tag, attrs)
        else:
            self.failed = True


def _table_key(text: str) -> str:
    if re.search(r"<table\b", text, re.IGNORECASE):
        parser = _SimpleTable()
        try:
            parser.feed(text)
            parser.close()
        except (ValueError, AssertionError):
            return "unsupported-html-table:" + text
        if (
            parser.failed
            or parser.depth != 0
            or parser.row is not None
            or parser.cell is not None
            or parser.links
            or not parser.rows
        ):
            return "unsupported-html-table:" + text
        rows = parser.rows
    else:
        rows = []
        for line in text.splitlines():
            if not _TABLE_SEPARATOR.fullmatch(line):
                # Escaped separators remain in their cells; inline code with
                # unescaped pipes is conservatively kept as a distinct view.
                cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
                rows.append([_prose_key(cell) for cell in cells])
    # A wholly empty body row carries no cell value or subject binding. Keep
    # the header and every nonempty row/cell position exactly ordered.
    if rows:
        rows = [rows[0], *[row for row in rows[1:] if any(row)]]
    return "table:" + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _blank(text: str) -> str:
    """Mask syntax without moving any source line."""
    return re.sub(r"[^\n]", " ", text)


def _code_ranges(text: str) -> list[tuple[int, int]]:
    ranges = []
    offset = 0
    start = None
    delimiter = ""
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        if start is None:
            if match:
                start, delimiter = offset, match[1]
            elif line.startswith(("    ", "\t")):
                ranges.append((offset, offset + len(line)))
        elif re.fullmatch(
            r" {0,3}" + re.escape(delimiter[0]) + "{" + str(len(delimiter)) + r",}\s*",
            line,
        ):
            ranges.append((start, offset + len(line)))
            start = None
        offset += len(line)
    if start is not None:
        ranges.append((start, len(text)))
    for match in re.finditer(r"(?<!`)(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", text):
        ranges.append(match.span())
    return ranges


def _outside_code(position: int, ranges: list[tuple[int, int]]) -> bool:
    return not any(start <= position < end for start, end in ranges)


def _closing(text: str, start: int, opener: str, closer: str) -> int | None:
    depth = 1
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _body_view(text: str) -> tuple[str, set[int]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    original_lines = text.splitlines()
    line_ends = [index for index, char in enumerate(text) if char == "\n"]
    code = _code_ranges(text)
    spans: list[tuple[int, int]] = []
    for begin in _OCR_BEGIN.finditer(text):
        if not _outside_code(begin.start(), code):
            continue
        end = _OCR_END.search(text, begin.end())
        # An explicit but unterminated excluded region extends to EOF. It does
        # not create an image/OCR task or gate the remaining extracted body.
        spans.append((begin.start(), len(text) if end is None else end.end()))
    # These are explicit historical generation delimiters, not OCR judgments.
    for match in re.finditer(
        r"<!--\s*这是一张图片[，,]\s*ocr\s*内容为[：:].*?-->",
        text,
        re.IGNORECASE | re.DOTALL,
    ):
        if _outside_code(match.start(), code):
            spans.append(match.span())
    legacy_begin = list(re.finditer(r"(?m)^\s*>\s*\*\*图片转写[：:]\*\*[^\n]*", text))
    for index, begin in enumerate(legacy_begin):
        if not _outside_code(begin.start(), code):
            continue
        end = re.search(
            r"(?m)^\s*>\s*(?:\*\*)?转写说明[：:](?:\*\*)?[^\n]*", text[begin.end() :]
        )
        next_begin = (
            legacy_begin[index + 1].start()
            if index + 1 < len(legacy_begin)
            else len(text)
        )
        if end and begin.end() + end.end() <= next_begin:
            spans.append((begin.start(), begin.end() + end.end()))
        else:
            # An unpaired generated label is excluded alone; do not guess where
            # its unmarked following text stops or remove a business section.
            spans.append(begin.span())
    for match in re.finditer(r"\[PaddleOCR image region: untranscribed\]", text):
        if _outside_code(match.start(), code):
            spans.append(match.span())
    for tag in ("picture", "svg"):
        for begin in re.finditer(_TAG % tag, text, re.IGNORECASE):
            if not _outside_code(begin.start(), code):
                continue
            end = re.search(r"</" + tag + r"\s*>", text[begin.end() :], re.IGNORECASE)
            if end is None:
                # SVG permits a self-closing root element.
                if begin[0].rstrip().endswith("/>"):
                    spans.append(begin.span())
                    continue
                spans.append((begin.start(), len(text)))
                continue
            spans.append((begin.start(), begin.end() + end.end()))
    for match in re.finditer(_TAG % "(?:img|source)", text, re.IGNORECASE):
        if _outside_code(match.start(), code):
            spans.append(match.span())

    image_labels = set()
    for match in re.finditer(r"!\[", text):
        start = match.start()
        before = start
        while before > 0 and text[before - 1] == "\\":
            before -= 1
        if (start - before) % 2:
            continue
        if not _outside_code(start, code):
            continue
        close = _closing(text, start + 1, "[", "]")
        if close is None:
            continue
        label = text[start + 2 : close].strip().casefold()
        end = close + 1
        if end < len(text) and text[end] in "([":
            opener = text[end]
            closing = _closing(text, end, opener, ")" if opener == "(" else "]")
            if closing is not None:
                if opener == "[":
                    image_labels.add(
                        text[end + 1 : closing].strip().casefold() or label
                    )
                end = closing + 1
        else:
            image_labels.add(label)
        spans.append((start, end))

    for start, end in sorted(spans, reverse=True):
        text = text[:start] + _blank(text[start:end]) + text[end:]
    excluded_lines = set()
    for start, end in spans:
        excluded_lines.update(
            range(bisect_right(line_ends, start), bisect_right(line_ends, end) + 1)
        )
    # A link around an image contains no body label after the image is removed.
    text = re.sub(r"\[\s*\]\([^\n)]*\)", lambda m: _blank(m[0]), text)
    # Remove image-only reference declarations. A shared ordinary link keeps its target.
    ordinary_labels = {
        (match[2] or match[1]).strip().casefold()
        for match in re.finditer(r"(?<!!)\[([^]\n]+)\]\[([^]\n]*)\]", text)
    }
    ordinary_labels.update(
        match[1].strip().casefold()
        for match in re.finditer(r"(?<!!)\[([^]\n]+)\](?![\[:(])", text)
    )
    for match in list(
        re.finditer(r"(?m)^ {0,3}\[([^]\n]+)\]:[^\n]*(?:\n[ \t]+[^\n]*)*", text)
    ):
        label = match[1].strip().casefold()
        if label in image_labels and label not in ordinary_labels:
            text = text[: match.start()] + _blank(match[0]) + text[match.end() :]
    # Bind ordinary reference links to their actual targets before unit matching.
    # Otherwise a label could be reused even when its endpoint changed elsewhere.
    definitions = {}
    for match in re.finditer(
        r"(?m)^ {0,3}\[([^]\n]+)\]:([^\n]*(?:\n[ \t]+[^\n]*)*)", text
    ):
        definitions.setdefault(match[1].strip().casefold(), " ".join(match[2].split()))
    code = _code_ranges(text)
    replacements = []
    for match in re.finditer(r"(?<!!)\[([^]\n]+)\](?:\[([^]\n]*)\])?", text):
        if not _outside_code(match.start(), code):
            continue
        if text[match.end() : match.end() + 1] in ("(", ":"):
            continue
        target = definitions.get((match[2] or match[1]).strip().casefold())
        if target is not None:
            replacements.append((match.start(), match.end(), f"[{match[1]}]({target})"))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    # Presentation-only wrappers around removed images contain no body knowledge.
    empty_wrapper = re.compile(
        r"<(?P<tag>div|span|p|a|figure|center)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>\s*</(?P=tag)\s*>",
        re.IGNORECASE,
    )
    while True:
        cleaned = empty_wrapper.sub(lambda match: _blank(match[0]), text)
        if cleaned == text:
            break
        text = cleaned
    ignored = {
        index
        for index, line in enumerate(text.splitlines())
        if not line.strip()
        and (index in excluded_lines or original_lines[index].strip())
    }
    return text, ignored


def _quantity(match: re.Match) -> str:
    with localcontext() as context:
        context.prec = len(match["number"]) + 32
        number = Decimal(match["number"].replace(",", ""))
        number *= {None: 1, "万": 10_000, "亿": 100_000_000, "万亿": 1_000_000_000_000}[
            match["magnitude"]
        ]
    unit = match["unit"].replace("％", "%")
    value = format(number, "f")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value + unit


def _protected_ranges(text: str, *, include_cosmetic: bool) -> list[tuple[int, int]]:
    protected = [
        match.span()
        for match in re.finditer(r"(?<!`)(`+)(?!`)([^\n]*?)(?<!`)\1(?!`)", text)
    ]
    protected += [
        match.span()
        for match in re.finditer(
            r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)[^\s<>]+|(?<!\S)(?:/(?!>)|\./|\.\./)\S+",
            text,
        )
    ]
    for match in re.finditer(
        r"</?(?P<tag>[A-Za-z][A-Za-z0-9:-]*)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", text
    ):
        if include_cosmetic or match["tag"].casefold() not in _COSMETIC_TAGS:
            protected.append(match.span())
    for match in re.finditer(r"\]\(", text):
        end = _closing(text, match.end() - 1, "(", ")")
        if end is not None:
            protected.append((match.end(), end))
    return protected


def _prose_key(text: str) -> str:
    # Strip only whitelisted cosmetic tags outside literal code and link targets.
    # Unknown markup, deletion/hidden styles and XML payload stay significant.
    parts = []
    cursor = 0
    for start, end in sorted(_protected_ranges(text, include_cosmetic=False)):
        if end <= cursor:
            continue
        if start > cursor:
            parts.append(_cosmetic_markup(text[cursor:start]))
        parts.append(text[max(cursor, start) : end])
        cursor = end
    parts.append(_cosmetic_markup(text[cursor:]))
    text = "".join(parts)
    protected = _protected_ranges(text, include_cosmetic=True)

    def normalize(value):
        value = html.unescape(value).replace("\xa0", " ")
        value = unicodedata.normalize("NFC", value)
        value = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m[1] or m[2], value)
        value = re.sub(r"[ \t\n]+", " ", value)
        return _QUANTITY.sub(_quantity, value)

    parts = []
    cursor = 0
    for start, end in sorted(protected):
        if end <= cursor:
            continue
        if start > cursor:
            parts.append(normalize(text[cursor:start]))
        parts.append(text[max(cursor, start) : end])
        cursor = end
    parts.append(normalize(text[cursor:]))
    return "".join(parts).strip()


def _key(kind: str, text: str) -> str:
    if kind == "code":
        return text.rstrip("\n")
    if kind == "table":
        return _table_key(text)
    return _prose_key(text)


def extract_units(text: str) -> list[Unit]:
    """Extract body units with original one-based line locations.

    Ordered lists and tables stay whole to preserve step order and value bindings.
    Unordered sibling facts can align independently; nesting supplies context.
    """
    body, ignored_lines = _body_view(text)
    lines = body.splitlines()
    units = []
    headings: list[tuple[int, str]] = []
    bullet_parents: list[tuple[int, str]] = []
    leadin = ""
    list_scope = ""

    def append(kind, start, end, value, extra=()):
        key = _key(kind, value)
        if key:
            units.append(
                Unit(
                    kind,
                    value,
                    key,
                    tuple(v for _, v in headings)
                    + ((leadin,) if leadin else ())
                    + (
                        (list_scope,)
                        if list_scope
                        and kind in {"bullet", "ordered", "table", "code"}
                        and list_scope != leadin
                        else ()
                    )
                    + tuple(extra),
                    start + 1,
                    end,
                )
            )

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = _HEADING.fullmatch(line)
        if heading:
            level = len(heading[1])
            while headings and headings[-1][0] >= level:
                headings.pop()
            leadin = list_scope = ""
            append("heading", index, index + 1, heading[2])
            headings.append((level, _prose_key(heading[2])))
            bullet_parents.clear()
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            end = index + 1
            while end < len(lines):
                if re.fullmatch(
                    r" {0,3}"
                    + re.escape(fence[1][0])
                    + "{"
                    + str(len(fence[1]))
                    + r",}\s*",
                    lines[end],
                ):
                    end += 1
                    break
                end += 1
            append("code", index, end, "\n".join(lines[index:end]))
            list_scope = units[-1].key if units else ""
            index = end
            continue
        if (
            index + 1 < len(lines)
            and "|" in line
            and _TABLE_SEPARATOR.fullmatch(lines[index + 1])
        ):
            end = index + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            append("table", index, end, "\n".join(lines[index:end]))
            list_scope = units[-1].key if units else ""
            index = end
            continue
        if re.match(r"\s*<table\b", line, re.IGNORECASE):
            end = index + 1
            while end < len(lines) and not re.search(
                r"</table\s*>", lines[end - 1], re.IGNORECASE
            ):
                end += 1
            append("table", index, end, "\n".join(lines[index:end]))
            list_scope = units[-1].key if units else ""
            index = end
            continue
        if _ORDERED.match(line):
            end = index + 1
            while (
                end < len(lines)
                and lines[end].strip()
                and (_ORDERED.match(lines[end]) or lines[end].startswith((" ", "\t")))
            ):
                end += 1
            append("ordered", index, end, "\n".join(lines[index:end]))
            list_scope = units[-1].key if units else ""
            index = end
            continue
        bullet = _BULLET.match(line)
        if bullet:
            if _WORKFLOW.search(" ".join(v for _, v in headings) + " " + list_scope):
                end = index + 1
                while (
                    end < len(lines)
                    and lines[end].strip()
                    and (
                        _BULLET.match(lines[end]) or lines[end].startswith((" ", "\t"))
                    )
                ):
                    end += 1
                append("ordered", index, end, "\n".join(lines[index:end]))
                list_scope = units[-1].key if units else ""
                index = end
                continue
            indent = len(bullet[1].expandtabs(4))
            while bullet_parents and bullet_parents[-1][0] >= indent:
                bullet_parents.pop()
            end = index + 1
            while (
                end < len(lines)
                and lines[end].strip()
                and lines[end].startswith(" " * (indent + 2))
                and not (_BULLET.match(lines[end]) or _ORDERED.match(lines[end]))
            ):
                end += 1
            value = "\n".join([bullet[2], *(v.strip() for v in lines[index + 1 : end])])
            append("bullet", index, end, value, (v for _, v in bullet_parents))
            bullet_parents.append((indent, _prose_key(value)))
            index = end
            continue
        if line.startswith(("    ", "\t")):
            end = index + 1
            while end < len(lines) and lines[end].startswith(("    ", "\t")):
                end += 1
            append("code", index, end, "\n".join(lines[index:end]))
            list_scope = units[-1].key if units else ""
            index = end
            continue
        end = index + 1
        while (
            end < len(lines)
            and (lines[end].strip() or end in ignored_lines)
            and (
                end in ignored_lines
                or not (
                    _HEADING.match(lines[end])
                    or _FENCE.match(lines[end])
                    or _BULLET.match(lines[end])
                    or _ORDERED.match(lines[end])
                    or lines[end].startswith(("    ", "\t"))
                    or re.match(r"\s*<table\b", lines[end], re.IGNORECASE)
                )
            )
        ):
            if (
                end + 1 < len(lines)
                and "|" in lines[end]
                and _TABLE_SEPARATOR.fullmatch(lines[end + 1])
            ):
                break
            end += 1
        value = "\n".join(lines[index:end])
        append("paragraph", index, end, value)
        is_scope = (
            value.rstrip().endswith((":", "："))
            or _CONDITION.search(value)
            or _WORKFLOW.search(value)
        )
        list_scope = _prose_key(value) if is_scope else ""
        if is_scope:
            leadin = list_scope
        bullet_parents.clear()
        index = end
    return units


def _grams(text: str) -> set[str]:
    return {text[i : i + 3] for i in range(max(1, len(text) - 2))}


def align_units(old: list[Unit], new: list[Unit], *, max_work: int = 200_000) -> dict:
    """Reuse normalized equivalents; propose bounded local revisions otherwise.

    Exact matches precede fuzzy work and survive a zero budget. Similar units are
    revision candidates, not semantic equivalence assertions. Unobserved old units
    always remain visible and are never interpreted as remote deletions.
    """
    exact = defaultdict(list)
    for index, unit in enumerate(old):
        exact[(unit.kind, unit.context, unit.key)].append(index)
    used = set()
    items: list[dict | None] = [None] * len(new)
    unmatched = []

    def item(kind, before, after, basis, similarity=None):
        return {
            "kind": kind,
            "old": before,
            "new": after,
            "basis": basis,
            "similarity": similarity,
        }

    for index, unit in enumerate(new):
        matches = exact.get((unit.kind, unit.context, unit.key))
        if matches:
            used.update(matches)
            items[index] = item(
                "reuse", old[matches[0]], unit, "NORMALIZED_BODY_EQUIVALENT", 1.0
            )
        else:
            unmatched.append(index)

    candidates = defaultdict(deque)
    for index, unit in enumerate(old):
        if index not in used:
            candidates[(unit.kind, unit.context)].append(index)
    grams = {}
    work = 0
    complete = True
    for index in unmatched:
        unit = new[index]
        group = candidates[(unit.kind, unit.context)]
        scored = []
        after_grams = _grams(unit.key)
        exhausted = False
        for previous in group:
            if previous in used:
                continue
            before = old[previous]
            cost = len(before.key) + len(unit.key)
            if work + cost > max_work:
                exhausted = True
                break
            work += cost
            before_grams = grams.setdefault(previous, _grams(before.key))
            score = (
                2
                * len(before_grams & after_grams)
                / (len(before_grams) + len(after_grams))
            )
            if score >= 0.6:
                scored.append((score, previous))
        scored.sort(reverse=True)
        if exhausted:
            complete = False
            items[index] = item("unresolved", None, unit, "BODY_MATCH_BUDGET_EXCEEDED")
        elif scored and len(scored) > 1 and scored[0][0] - scored[1][0] < 0.03:
            items[index] = item(
                "unresolved", None, unit, "AMBIGUOUS_BODY_ALIGNMENT", scored[0][0]
            )
        elif scored:
            score, previous = scored[0]
            used.add(previous)
            items[index] = item(
                "revise", old[previous], unit, "SIMILAR_BODY_REVISION_CANDIDATE", score
            )
        else:
            items[index] = item("add", None, unit, "NO_MATCHED_BASELINE_BODY_UNIT")
    for index, unit in enumerate(old):
        if index not in used:
            items.append(item("old_only_review", unit, None, "OLD_BODY_NOT_OBSERVED"))
    return {"items": items, "complete": complete}
