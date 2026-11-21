"""Keep immutable publication inputs outside disposable run directories."""

import hashlib
import os
import re
import shutil
import tempfile
from html import unescape
from pathlib import Path
from urllib.parse import quote


def _local_destination(path, original=""):
    """Encode a filesystem path while retaining the original URL fragment."""
    _, separator, fragment = original.partition("#")
    return quote(path, safe="/:") + separator + fragment


def retain_raw(directory, source, resources):
    """Copy catalogued files with their raw-relative filesystem layout intact."""
    root = directory / "raw"
    if source.is_relative_to(root):
        return source, resources
    directory.mkdir(parents=True, exist_ok=True)
    local = [
        (index, Path(item["path"]))
        for index, item in enumerate(resources)
        if item.get("path") and Path(item["path"]).is_file()
    ]
    relatives = {
        index: Path(os.path.relpath(path, source.parent)) for index, path in local
    }
    depth = max(
        (
            sum(1 for part in relative.parts if part == "..")
            for relative in relatives.values()
        ),
        default=0,
    )
    parent = Path(*(["source"] * (depth + 1)))
    staging = Path(tempfile.mkdtemp(prefix=".raw-", dir=directory))
    try:
        target = staging / parent / "source.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        for index, path in local:
            retained = (target.parent / relatives[index]).resolve()
            retained.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, retained)
            resources[index] = {
                **resources[index],
                "path": str(root / retained.relative_to(staging)),
            }
        old = directory / ".previous-raw"
        if old.exists():
            shutil.rmtree(old)
        if root.exists():
            root.rename(old)
        staging.rename(root)
        if old.exists():
            shutil.rmtree(old)
        return root / parent / "source.md", resources
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def retain_blocked_raw(store, rows):
    """Keep acquired bytes durable even when Corpus publication is blocked."""
    changed = False
    for row in rows:
        identifier = row["document_id"]
        raw = store.document(identifier)["raw"]
        source = Path(raw["path"])
        if not source.is_relative_to(store.runs):
            continue  # A resumed failure may already point to durable raw.
        parent = store.input_directory(identifier)
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="retained-", dir=parent))
        target, resources = retain_raw(
            directory, source, [dict(r) for r in raw.get("resources") or []]
        )
        if hashlib.sha256(target.read_bytes()).hexdigest() != raw["sha256"]:
            raise ValueError("blocked raw changed during retention")
        raw.update(path=str(target), resources=resources)
        changed = True
    if changed:
        store.save(durable=True)


def retain_inputs(store, run, rows, results):
    admitted = bool(run.data.get("artifacts", {}).get("admitted_run"))
    for row in rows:
        identifier = row["document_id"]
        raw = store.document(identifier)["raw"]
        result = results.setdefault(identifier, {})
        if admitted:
            # Retain catalogued files for durable publication, but keep the
            # admitted raw record byte-exact: no raw.update here.
            parent = store.input_directory(identifier)
            parent.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix="retained-", dir=parent))
            target, resources = retain_raw(
                directory,
                Path(row["raw_path"]),
                [dict(r) for r in (row.get("resources") or [])],
            )
            row["resources"] = resources
        else:
            parent = store.input_directory(identifier)
            parent.mkdir(parents=True, exist_ok=True)
            # Published Markdown can still refer to earlier retained generations.
            directory = Path(tempfile.mkdtemp(prefix="retained-", dir=parent))
            target, resources = retain_raw(
                directory,
                Path(row["raw_path"]),
                [dict(r) for r in raw.get("resources") or []],
            )
            raw.update(
                path=str(target),
                resources=resources,
                sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            )
            row["resources"] = resources
        output = directory / "corpus-input.md"
        enriched = result.get("status") == "completed"
        source = Path(result["output"]) if enriched else target
        output.write_bytes(
            publication_body(source, resources, result if enriched else {})
        )
        result["output"] = str(output)
    store.save()


def publication_body(source, resources, result):
    """Relocate catalogued destinations without editing the original tool output."""
    targets = {
        r["resource_id"]: _local_destination(r["path"], r["resource_id"])
        for r in resources
        if r.get("path") and Path(r["path"]).is_file()
    }
    if result:
        # Respell remaining relative links only in the publication copy.
        # The original tool output and v2 artifact keep their exact bytes.
        targets.update(
            {
                _local_destination(relative, asset["resource_id"]): targets.get(
                    asset["resource_id"],
                    _local_destination(asset["path"], asset["resource_id"]),
                )
                for relative, asset in result["assets"].items()
            }
        )
    return relocate_targets(source.read_bytes().decode("utf-8"), targets).encode(
        "utf-8"
    )


def relocate_targets(text, replacements):
    """Replace declared targets with URL destinations, preserving literals."""
    targets = {
        spelling: after
        for before, after in replacements.items()
        for spelling in {before, quote(before, safe="/:")}
    }
    parts, start = [], 0
    for left, right, html in _relocation_regions(text):
        parts.append(
            _relocate_markdown(
                text[start:left],
                targets,
                line_start=start == 0 or text[start - 1] == "\n",
            )
        )
        parts.append(
            _relocate_html(text[left:right], targets) if html else text[left:right]
        )
        start = right
    parts.append(
        _relocate_markdown(
            text[start:],
            targets,
            line_start=start == 0 or text[start - 1] == "\n",
        )
    )
    return "".join(parts)


def _relocation_regions(text):
    """Separate HTML tags from explicit fences, comments and code spans."""
    tokens = re.compile(
        r"(?m)^ {0,3}(?P<fence>`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)"
        r"|(?P<comment><!--)"
        r"|(?P<definition>^ {0,3}\[[^\]\r\n]+\]:[^\r\n]*(?:\r?\n|$))"
        r"|(?P<tag><[A-Za-z][A-Za-z0-9:-]*(?=[\s>]|/>)(?:[^>\"']|\"[^\"]*\"|'[^']*')*>)"
        r"|(?P<ticks>`+)"
    )
    following, closers = {}, {}
    for run in reversed(list(re.finditer(r"`+|^[ \t]*\r?$", text, re.MULTILINE))):
        if not run.group().startswith("`"):
            following.clear()  # Inline code cannot cross a blank paragraph.
            continue
        length = len(run.group())
        closers[run.start()] = following.get(length)
        following[length] = run
    position = 0
    while match := tokens.search(text, position):
        position = match.end()
        if match.group("definition"):
            continue  # Backticks in a reference label are literal label text.
        if match.group("tag"):
            line = text[text.rfind("\n", 0, match.start()) + 1 : match.start()]
            if text[max(0, match.start() - 2) : match.start()] == "](" or re.fullmatch(
                r" {0,3}\[[^\]\r\n]+\]:[ \t]*", line
            ):
                continue  # Angle-wrapped Markdown targets are not HTML tags.
            yield match.start(), position, True
            continue
        fence = match.group("fence")
        left = match.start()
        if (
            fence
            and fence[0] == "`"
            and "`" in match.group()[match.group().index(fence) + len(fence) :]
        ):
            # An invalid fence may instead be a same-line code span.
            left = match.start("fence")
            position = left + len(fence)
            fence = None
        if fence:
            closing = re.compile(
                r"(?m)^ {0,3}"
                + re.escape(fence[0])
                + "{"
                + str(len(fence))
                + r",}[ \t]*(?:\r?\n|$)",
            ).search(text, match.end())
            end = closing.end() if closing else len(text)
        elif match.group("comment"):
            closing = text.find("-->", match.end())
            end = closing + 3 if closing >= 0 else len(text)
        else:
            before = left
            while before and text[before - 1] == "\\":
                before -= 1
            closing = closers[left]
            if (left - before) % 2 or closing is None:
                continue
            end = closing.end()
        yield left, end, False
        position = end


def _relocate_markdown(text, targets, *, line_start=True):
    """Rewrite declared Markdown destinations outside literal regions and HTML."""
    for before, after in targets.items():
        for ending in (")", " ", "\t", "\n"):
            text = text.replace("](" + before + ending, "](" + after + ending)
        text = text.replace("](<" + before + ">", "](<" + after + ">")

    def definition(match):
        if match.start() == 0 and not line_start:
            return match.group()
        angle, plain = match.group("angle", "plain")
        value = angle if angle is not None else plain
        replacement = targets.get(value, value)
        return match.group("prefix") + (
            "<" + replacement + ">" if angle is not None else replacement
        )

    return re.sub(
        r"(?m)(?P<prefix>^ {0,3}\[[^\]\r\n]+\]:[ \t]*)"
        r"(?:<(?P<angle>[^>\r\n]+)>|(?P<plain>\S+))",
        definition,
        text,
    )


def _relocate_html(text, targets):
    """Consume whole attributes before selecting destination value tokens."""

    def attribute(match):
        name = match.group("name").lower()
        if name not in {"srcset", "src", "poster", "data", "xlink:href", "href"}:
            return match.group()
        quoted, bare = match.group("quoted", "bare")
        value = quoted if quoted is not None else bare
        if name == "srcset":
            replacement = re.sub(
                r"(^|,)(\s*)([^\s,]+)",
                lambda token: (
                    token.group(1)
                    + token.group(2)
                    + targets.get(unescape(token.group(3)), token.group(3))
                ),
                value,
            )
        else:
            replacement = targets.get(unescape(value), value)
        if replacement == value:
            return match.group()
        marker = match.group("quote") or ""
        return match.group("prefix") + marker + replacement + marker

    return re.sub(
        r"(?P<prefix>\s(?P<name>[^\s/>=]+)\s*=\s*)"
        r"(?:(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)|(?P<bare>[^\s>]+))",
        attribute,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
