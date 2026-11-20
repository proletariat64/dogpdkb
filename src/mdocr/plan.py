"""Immutable, deterministic public planning contract for mdocr."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import requests
from markdown_it import MarkdownIt
from markdown_it.common.html_re import HTML_TAG_RE

from .llm import DEFAULT_EFFORT, DEFAULT_MODEL
from .markdown import IMAGE_RE, parse_image_match
from .prepare import (
    DEFAULT_ENABLED_EXTENSIONS,
    ImageError,
    _fetch_remote,
)

DEFAULT_PROMPT = (
    Path(__file__).with_name("default_prompt.txt").read_text(encoding="utf-8").strip()
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mdocr-plan-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.link(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


_MARKDOWN_BLOCKS = MarkdownIt("commonmark")


def _inline_code_ranges(text, offset):
    runs = list(re.finditer(r"`+", text))
    next_run, closing = {}, {}
    for i in reversed(range(len(runs))):
        width = len(runs[i].group())
        if width in next_run:
            closing[i] = next_run[width]
        next_run[width] = i
    i = 0
    while i < len(runs):
        start = runs[i].start()
        escape = start
        while escape > 0 and text[escape - 1] == "\\":
            escape -= 1
        if i in closing and (start - escape) % 2 == 0:
            j = closing[i]
            yield offset + start, offset + runs[j].end()
            i = j + 1
        else:
            i += 1


def _masked_ranges(markdown: str) -> list[tuple[int, int]]:
    """Use CommonMark block boundaries and preserve original source offsets."""
    line_offsets = [0, *(m.end() for m in re.finditer(r"\r\n|\r|\n", markdown))]
    line_offsets.append(len(markdown))
    ranges = [
        m.span()
        for m in re.finditer(
            r"<!-- mdocr:ocr-begin\b.*?<!-- mdocr:ocr-end -->", markdown, re.DOTALL
        )
    ]
    for token in _MARKDOWN_BLOCKS.parse(markdown):
        if not token.map:
            continue
        start, end = (line_offsets[line] for line in token.map)
        if token.type in {"fence", "code_block", "html_block"}:
            ranges.append((start, end))
        elif token.type == "inline":
            text = markdown[start:end]
            ranges.extend(_inline_code_ranges(text, start))
            for marker in re.finditer("<", text):
                left = marker.start()
                if tag := HTML_TAG_RE.match(text[left:]):
                    ranges.append((start + left, start + left + tag.end()))
    return sorted(ranges)


def _images(markdown: str, source: Path, selection: set[str] | None) -> list[dict]:
    blocked = _masked_ranges(markdown)
    occurrences = []
    prior = 0
    for match in IMAGE_RE.finditer(markdown):
        while prior < len(blocked) and blocked[prior][1] <= match.start():
            prior += 1
        if (
            prior < len(blocked)
            and blocked[prior][0] <= match.start() < blocked[prior][1]
        ):
            continue
        ref = parse_image_match(match)
        remote = ref.is_remote
        target_url = ref.target
        resource_id = target_url
        image_path = None
        content = None
        error = None
        if ref.unsupported:
            error = "UNSUPPORTED_IMAGE_TARGET"
        elif remote:
            error = "REMOTE_IMAGE_NOT_DOWNLOADED"
        else:
            image_path = (source.parent / target_url).resolve()
            try:
                content = image_path.read_bytes()
            except OSError:
                content = None
                error = "IMAGE_NOT_FOUND"
        identity = f"{match.start()}:{match.end()}:{resource_id}"
        occurrences.append(
            {
                "id": f"image-{len(occurrences) + 1}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}",
                "resource_id": resource_id,
                "original_link": target_url
                if remote or image_path is None
                else str(image_path),
                "start": match.start(),
                "end": match.end(),
                "source_path": str(image_path) if image_path is not None else None,
                "sha256": _digest(content) if content is not None else None,
                "selected": selection is None
                or ref.target in selection
                or resource_id in selection,
                "error": error,
            }
        )
    return occurrences


def _read_inputs(args) -> tuple[list[tuple[str, Path]], str]:
    shapes = [args.input is not None, args.list is not None, args.directory is not None]
    if sum(shapes) != 1:
        raise ValueError("exactly one of --input, --list, or --directory is required")
    if args.input is not None:
        if args.output_dir is not None:
            raise ValueError("--output-dir cannot be used with --input")
        path = Path(args.input).resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".md":
            raise ValueError("--input must name an existing Markdown file")
        output = Path(args.output).resolve() if args.output else None
        if output is None:
            raise ValueError("--output is required with --input")
        return [(str(path), path)], str(output)
    if args.output is not None:
        raise ValueError("--output is only valid with --input")
    if args.list is not None:
        list_path = Path(args.list).resolve(strict=True)
        raw = json.loads(list_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
            raise ValueError(
                "--list must contain a JSON array of Markdown path strings"
            )
        files = [
            (
                str((list_path.parent / x).resolve(strict=True)),
                (list_path.parent / x).resolve(strict=True),
            )
            for x in raw
        ]
        if any(not p.is_file() or p.suffix.lower() != ".md" for _, p in files):
            raise ValueError("every --list entry must be an existing Markdown file")
        if not args.output_dir:
            raise ValueError("--output-dir is required with --list")
        return files, str(Path(args.output_dir).resolve())
    directory = Path(args.directory).resolve(strict=True)
    if not directory.is_dir() or not args.output_dir:
        raise ValueError("--directory must be a directory and requires --output-dir")
    outdir = Path(args.output_dir).resolve()
    if outdir == directory or directory in outdir.parents:
        raise ValueError("output directory cannot be inside the input directory")
    files = [
        (str(path.resolve()), path.resolve())
        for path in sorted(directory.rglob("*"))
        if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".md"
    ]
    return files, str(outdir)


def plan_command(args) -> int:
    files, output_root = _read_inputs(args)
    download_sources = None
    if args.download_list:
        if not args.download:
            raise ValueError("--download-list requires --download")
        listed = json.loads(Path(args.download_list).read_bytes())
        if not isinstance(listed, list) or any(
            not isinstance(path, str) for path in listed
        ):
            raise ValueError(
                "--download-list must contain a JSON array of absolute input paths"
            )
        download_sources = set(listed)
        if download_sources - {str(path) for _, path in files}:
            raise ValueError("--download-list names an unknown input document")
    if args.mode not in {"paddle", "llm"}:
        raise ValueError("--mode must be paddle or llm")
    if args.mode == "paddle" and any(
        value is not None
        for value in (
            args.model,
            args.effort,
            args.prompt_file,
            args.token_budget,
        )
    ):
        raise ValueError(
            "model, effort, prompt and token budget are only valid in llm mode"
        )
    if args.mode == "llm" and args.endpoint is not None:
        raise ValueError("--endpoint is only valid in paddle mode")
    if any(
        value is not None and value < 0
        for value in (args.token_budget, args.request_budget)
    ):
        raise ValueError("budgets must be nonnegative")
    if not 0 <= args.max_seconds < float("inf"):
        raise ValueError("deadline must be finite and nonnegative")
    model = args.model or (DEFAULT_MODEL if args.mode == "llm" else None)
    effort = args.effort or (DEFAULT_EFFORT if args.mode == "llm" else None)
    if args.effort and args.effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("--effort must be low, medium, high, or xhigh")
    prompt = (
        Path(args.prompt_file).read_text(encoding="utf-8")
        if args.prompt_file
        else DEFAULT_PROMPT
    )
    selection = None
    if args.selection:
        selected = json.loads(Path(args.selection).read_text(encoding="utf-8"))
        if not isinstance(selected, dict) or any(
            not isinstance(k, str)
            or not isinstance(v, list)
            or any(not isinstance(x, str) for x in v)
            for k, v in selected.items()
        ):
            raise ValueError(
                "--selection must be a JSON object mapping source paths to target arrays"
            )
        selection = selected
    docs = []
    seen_sources = set()
    seen_outputs = set()
    single = args.input is not None
    for document_id, path in files:
        canonical = str(path.resolve(strict=True))
        if canonical in seen_sources:
            raise ValueError(f"duplicate input path: {canonical}")
        seen_sources.add(canonical)
        output = (
            Path(output_root)
            if single
            else Path(output_root) / path.relative_to(Path(args.directory).resolve())
            if args.directory
            else Path(output_root) / path.name
        )
        output = output.resolve()
        if str(output) in seen_outputs:
            raise ValueError(f"output collision: {output}")
        seen_outputs.add(str(output))
        raw = path.read_bytes()
        markdown = raw.decode("utf-8")
        images = _images(
            markdown,
            path,
            set(selection.get(canonical, [])) if selection is not None else None,
        )
        docs.append(
            {
                "document_id": document_id,
                "path": canonical,
                "sha256": _digest(raw),
                "output": str(output),
                "images": images,
            }
        )
    plan_path = Path(args.plan).resolve()
    source_paths = {doc["path"] for doc in docs}
    output_paths = {doc["output"] for doc in docs}
    image_paths = {
        image["source_path"]
        for doc in docs
        for image in doc["images"]
        if image["source_path"]
    }
    extra_inputs = {
        str(Path(value).resolve())
        for value in (args.prompt_file, args.selection, args.list, args.download_list)
        if value
    }
    protected_inputs = source_paths | image_paths | extra_inputs
    if output_paths & protected_inputs:
        raise ValueError("output path overlaps an input or source image")
    if str(plan_path) in protected_inputs | output_paths:
        raise ValueError("plan path overlaps an input, image, or output")
    if len(output_paths) != len(docs):
        raise ValueError("output collision")
    for destination in (*output_paths, str(plan_path)):
        if Path(destination).exists():
            raise ValueError(f"destination already exists: {destination}")
    if selection is not None and selection.keys() - source_paths:
        raise ValueError("selection names an unknown input document")
    if args.download:
        remote_images = [
            image
            for doc in docs
            if download_sources is None or doc["path"] in download_sources
            for image in doc["images"]
            if image["selected"] and image["error"] == "REMOTE_IMAGE_NOT_DOWNLOADED"
        ]
        if remote_images:
            assets = plan_path.with_name(f"{plan_path.name}.images")
            if any(
                Path(path).is_relative_to(assets)
                for path in protected_inputs | output_paths
            ):
                raise ValueError(
                    "image staging directory overlaps a planned input or output"
                )
            assets.mkdir(parents=True, exist_ok=False)
            _bind_remote_images(remote_images, assets, args.mode)
    settings = {
        "mode": args.mode,
        "model": model,
        "effort": effort,
        "prompt": prompt,
        "token_budget": args.token_budget,
        "request_budget": args.request_budget,
        "concurrency": args.concurrency,
        "endpoint": args.endpoint,
        "max_seconds": args.max_seconds,
        "cache_dir": str(Path(args.cache_dir).resolve()) if args.cache_dir else None,
    }
    result = {"schema_version": "mdocr-plan-2", "settings": settings, "documents": docs}
    _atomic_json(plan_path, result)
    return 0


def _bind_remote_images(images, assets, mode):
    seen = {}
    with requests.Session() as session:
        for image in images:
            url = image["resource_id"]
            if url not in seen:
                ext = Path(urlsplit(url).path).suffix.lower()
                if mode == "paddle" and ext not in DEFAULT_ENABLED_EXTENSIONS:
                    seen[url] = {"error": "REMOTE_IMAGE_FORMAT_UNSUPPORTED"}
                else:
                    try:
                        data = _fetch_remote(url, session)
                    except ImageError:
                        seen[url] = {"error": "REMOTE_IMAGE_DOWNLOAD_FAILED"}
                    else:
                        digest = _digest(data)
                        suffix = ext if mode == "paddle" else ""
                        path = assets / f"{_digest(url.encode())}{suffix}"
                        with path.open("xb") as stream:
                            stream.write(data)
                        seen[url] = {
                            "source_path": str(path),
                            "sha256": digest,
                            "error": None,
                        }
            image.update(seen[url])


def read_plan(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "mdocr-plan-2":
        raise ValueError("unsupported or invalid mdocr plan")
    return value
