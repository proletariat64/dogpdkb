"""Immutable, deterministic public planning contract for mdocr."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .markdown import IMAGE_RE, parse_image_match

DEFAULT_PROMPT = (
    "Convert each image into faithful, searchable Markdown. Do not invent content. "
    "Tables must be Markdown tables with headers, units, and values. Flowcharts and "
    "mind maps must be Mermaid preserving visible direction and labels. For charts "
    "and explanatory slides, provide visible transcription, separate summary, and "
    "separate Simplified Chinese translation. Summarize other images as text. Mark "
    "unreadable text and uncertain relationships explicitly."
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
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _masked_ranges(markdown: str) -> list[tuple[int, int]]:
    """Ranges within which image syntax is literal or already generated."""
    ranges = []
    fences = re.compile(r"(?m)^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n")
    opened = None
    marker = None
    for match in fences.finditer(markdown):
        token = match.group(1)
        if opened is None:
            opened, marker = match.start(), token[0]
        elif token[0] == marker and len(token) >= len(re.match(r"[`~]+", markdown[opened:]).group(0)):
            ranges.append((opened, match.end()))
            opened = marker = None
    if opened is not None:
        ranges.append((opened, len(markdown)))
    for pattern in (r"<!-- mdocr:ocr-begin\b.*?<!-- mdocr:ocr-end -->", r"`+[^`\n]*`+"):
        ranges.extend((m.start(), m.end()) for m in re.finditer(pattern, markdown, re.DOTALL if "ocr-begin" in pattern else 0))
    return sorted(ranges)


def _images(markdown: str, source: Path, selection: set[str] | None) -> list[dict]:
    blocked = _masked_ranges(markdown)
    occurrences = []
    prior = 0
    for match in IMAGE_RE.finditer(markdown):
        while prior < len(blocked) and blocked[prior][1] <= match.start():
            prior += 1
        if prior < len(blocked) and blocked[prior][0] <= match.start() < blocked[prior][1]:
            continue
        ref = parse_image_match(match)
        remote = ref.is_remote
        target_url = ref.target
        resource_id = target_url
        image_path = None
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
        occurrences.append({
            "id": f"image-{len(occurrences) + 1}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}",
            "resource_id": resource_id,
            "original_link": target_url if remote or image_path is None else str(image_path),
            "start": match.start(),
            "end": match.end(),
            "source_path": str(image_path) if image_path is not None else None,
            "sha256": _digest(content) if content is not None else None,
            "selected": selection is None or ref.target in selection or resource_id in selection,
            "error": error,
        })
    return occurrences


def _read_inputs(args) -> tuple[list[tuple[str, Path]], str]:
    shapes = [args.input is not None, args.list is not None, args.directory is not None]
    if sum(shapes) != 1:
        raise ValueError("exactly one of --input, --list, or --directory is required")
    if args.input is not None:
        path = Path(args.input).resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".md":
            raise ValueError("--input must name an existing Markdown file")
        output = Path(args.output).resolve() if args.output else None
        if output is None:
            raise ValueError("--output is required with --input")
        return [(str(path), path)], str(output)
    if args.list is not None:
        list_path = Path(args.list).resolve(strict=True)
        raw = json.loads(list_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
            raise ValueError("--list must contain a JSON array of Markdown path strings")
        files = [(str((list_path.parent / x).resolve(strict=True)), (list_path.parent / x).resolve(strict=True)) for x in raw]
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
    files = [(str(path.resolve()), path.resolve()) for path in sorted(directory.rglob("*.md")) if outdir not in path.resolve().parents]
    return files, str(outdir)


def plan_command(args) -> int:
    files, output_root = _read_inputs(args)
    if args.mode not in {"paddle", "llm"}:
        raise ValueError("--mode must be paddle or llm")
    if args.mode == "paddle" and (args.model or args.effort):
        raise ValueError("--model and --effort are only valid in llm mode")
    model = args.model or ("openai/gpt-6.1-terra" if args.mode == "llm" else None)
    effort = args.effort or ("medium" if args.mode == "llm" else None)
    if args.effort and args.effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("--effort must be low, medium, high, or xhigh")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else DEFAULT_PROMPT
    selection = None
    if args.selection:
        selected = json.loads(Path(args.selection).read_text(encoding="utf-8"))
        if not isinstance(selected, dict) or any(not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(x, str) for x in v) for k, v in selected.items()):
            raise ValueError("--selection must be a JSON object mapping source paths to target arrays")
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
        output = Path(output_root) if single else Path(output_root) / path.relative_to(Path(args.directory).resolve()) if args.directory else Path(output_root) / path.name
        output = output.resolve()
        if str(output) in seen_outputs:
            raise ValueError(f"output collision: {output}")
        seen_outputs.add(str(output))
        markdown = path.read_text(encoding="utf-8")
        images = _images(markdown, path, set(selection.get(canonical, [])) if selection is not None else None)
        docs.append({"document_id": document_id, "path": canonical, "sha256": _digest(markdown.encode()), "output": str(output), "images": images})
    plan_path = Path(args.plan).resolve()
    source_paths = {doc["path"] for doc in docs}
    output_paths = {doc["output"] for doc in docs}
    image_paths = {image["source_path"] for doc in docs for image in doc["images"] if image["source_path"]}
    extra_inputs = {str(Path(value).resolve()) for value in (args.prompt_file, args.selection, args.list) if value}
    protected_inputs = source_paths | image_paths | extra_inputs
    if output_paths & protected_inputs:
        raise ValueError("output path overlaps an input or source image")
    if str(plan_path) in source_paths | output_paths | image_paths:
        raise ValueError("plan path overlaps an input, image, or output")
    if len(output_paths) != len(docs):
        raise ValueError("output collision")
    for destination in (*output_paths, str(plan_path)):
        if Path(destination).exists():
            raise ValueError(f"destination already exists: {destination}")
    settings = {"mode": args.mode, "model": model, "effort": effort, "prompt": prompt, "token_budget": args.token_budget, "request_budget": args.request_budget, "concurrency": args.concurrency, "endpoint": args.endpoint, "max_seconds": args.max_seconds, "cache_dir": str(Path(args.cache_dir).resolve()) if args.cache_dir else None}
    result = {"schema_version": "mdocr-plan-2", "settings": settings, "documents": docs}
    _atomic_json(plan_path, result)
    return 0


def read_plan(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "mdocr-plan-2":
        raise ValueError("unsupported or invalid mdocr plan")
    return value
