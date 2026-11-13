"""Keep immutable publication inputs outside disposable run directories."""

import os
import re
import shutil
import tempfile
from html import unescape
from pathlib import Path
from urllib.parse import quote

from dogpdkb.records import atomic_json, read_json


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


def retain_ocr_files(directory, run, result, output, resources, original_paths):
    """Relocate only file paths declared by the public OCR result contract."""
    mappings = {}
    records = [*result.get("resources", []), *result.get("ocr", {}).get("images", [])]
    for item in records:
        for field in ("source_path", "prepared_image", "artifact"):
            value = item.get(field)
            if not isinstance(value, str):
                continue
            source = Path(value)
            if not source.is_relative_to(run.directory) or not source.is_file():
                continue
            if value not in mappings:
                retained = directory / "ocr-files" / source.relative_to(run.directory)
                retained.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, retained)
                mappings[value] = str(retained)
            item[field] = mappings[value]

    def relocate(text):
        text = relocate_targets(
            text,
            original_paths
            | {
                r["resource_id"]: r["path"]
                for r in resources
                if r.get("path") and Path(r["path"]).is_file()
            },
        )
        for before, after in mappings.items():
            text = text.replace(quote(before, safe="/:"), quote(after, safe="/:"))
            text = text.replace(before, after)
        return text

    retained = directory / "ocr.md"
    retained.write_text(relocate(output.read_text(encoding="utf-8")), encoding="utf-8")
    for target in mappings.values():
        artifact = Path(target)
        if artifact.suffix.lower() == ".md":
            artifact.write_text(
                relocate(artifact.read_text(encoding="utf-8")), encoding="utf-8"
            )
    return retained


def retain_inputs(store, run, rows, results):
    for row in rows:
        identifier = row["document_id"]
        parent = store.input_directory(identifier)
        parent.mkdir(parents=True, exist_ok=True)
        # Published Markdown can still refer to any earlier retained generation.
        # A resume may publish twice in one run, so the run ID is not sufficient.
        directory = Path(tempfile.mkdtemp(prefix="retained-", dir=parent))
        raw = store.document(identifier)["raw"]
        original_resources = [dict(r) for r in raw.get("resources") or []]
        target, resources = retain_raw(
            directory,
            Path(row["raw_path"]),
            [dict(r) for r in original_resources],
        )
        original_paths = {
            before["path"]: after["path"]
            for before, after in zip(original_resources, resources)
            if before.get("path") and after.get("path")
        }
        raw.update(path=str(target), resources=resources)
        row["resources"] = resources
        result = results.setdefault(identifier, {})
        if not Path(result.get("output") or "").is_file():
            fallback = directory / "corpus-input.md"
            targets = {
                r["resource_id"]: r["path"]
                for r in resources
                if r.get("path") and Path(r["path"]).is_file()
            }
            fallback.write_text(
                relocate_targets(target.read_text(encoding="utf-8"), targets),
                encoding="utf-8",
            )
            result["output"] = str(fallback)
        previous = store.step(identifier, "ocr") or {}
        if (
            previous.get("run_id") != run.id
            or previous.get("status") not in {"completed", "failed"}
            or previous.get("exit_code") not in {0, 2}
        ):
            continue
        request_path, report_path = Path(previous["input"]), Path(previous["report"])
        try:
            request, report = read_json(request_path), read_json(report_path)
            result = results.get(identifier) or next(
                (
                    r
                    for r in report.get("documents", [])
                    if str(r["document_id"]) == identifier
                ),
                {},
            )
        except (ValueError, KeyError, TypeError):
            continue
        output = Path(result.get("output") or "")
        if not output.is_file():
            continue
        retained = retain_ocr_files(
            directory, run, result, output, resources, original_paths
        )
        report["documents"] = [result]
        for item in request["documents"]:
            if str(item["document_id"]) == identifier:
                item["path"] = str(target)
        result["output"] = str(retained)
        result["path"] = str(target)
        results[identifier] = result
        atomic_json(directory / "ocr-request.json", request)
        atomic_json(directory / "ocr-report.json", report)
        previous.update(
            input=str(directory / "ocr-request.json"),
            report=str(directory / "ocr-report.json"),
        )
    store.save()


def relocate_targets(text, replacements):
    """Relocate destinations without changing explicit literal examples."""
    targets = {
        spelling: quote(after, safe="/:")
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


def ocr_request_input(row, run):
    """Relocate catalog-declared absolute targets in a tool-facing copy only."""
    source = Path(row["raw_path"])
    local = {
        resource["resource_id"]: resource["path"]
        for resource in row.get("resources") or []
        if isinstance(resource.get("resource_id"), str)
        and resource.get("path")
        and Path(resource["path"]).is_file()
    }
    if not any(
        Path(target).is_absolute() and target != retained
        for target, retained in local.items()
    ):
        return str(source), row["ocr_resources"], {}
    copy = run.directory / "ocr-inputs" / f"{row['slot']}.md"
    # mdocr accepts relative local targets; keep selections in the same spelling.
    replacements = {
        before: os.path.relpath(after, copy.parent) for before, after in local.items()
    }
    text = source.read_text(encoding="utf-8")
    text = relocate_targets(text, replacements)
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(text, encoding="utf-8")
    selected = row["ocr_resources"]
    if selected is not None:
        selected = [replacements.get(target, target) for target in selected]
    return str(copy), selected, {value: key for key, value in replacements.items()}
