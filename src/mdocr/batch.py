"""Generic manifest-driven OCR with per-document and per-resource outcomes."""

import json
import os
import time
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests

from . import __version__
from .markdown import iter_image_matches, render_image, transform_images
from .ocr import (
    _OCR_BLOCK_RE,
    _protect_existing_blocks,
    _restore_blocks,
    build_ocr_block,
)
from .paddle import _MODEL, OcrSharedError, PaddleClient
from .prepare import JPEG_QUALITY, ImageError, ImageSkip, prepare_one


def _local_image_path(target, source):
    parsed = urlsplit(target)
    if not parsed.scheme and not parsed.netloc:
        return (source.parent / target).resolve()
    return None


def _documents(manifest: Path) -> list[dict]:
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "mdocr-batch-input-1.0"
    ):
        raise ValueError("INVALID_BATCH_MANIFEST")
    documents = value.get("documents")
    if not isinstance(documents, list):
        raise TypeError("INVALID_BATCH_DOCUMENTS")
    seen = set()
    for row in documents:
        if not isinstance(row, dict):
            raise TypeError("INVALID_BATCH_DOCUMENT")
        identifier, path = row.get("document_id"), row.get("path")
        resources = row.get("resources")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("INVALID_DOCUMENT_ID")
        if not isinstance(path, str) or not path:
            raise ValueError("INVALID_DOCUMENT_PATH")
        if resources is not None and (
            not isinstance(resources, list)
            or any(not isinstance(v, str) or not v for v in resources)
        ):
            raise ValueError("INVALID_RESOURCE_SELECTION")
        seen.add(identifier)
    return documents


class Batch:
    def __init__(self, args):
        self.args = args
        self.cache = (args.cache_dir or args.output_dir / ".cache").resolve()
        self.deadline = time.monotonic() + args.max_seconds
        self.session = requests.Session()
        self.client = None
        self.stop = None
        self.preprocessing = {"name": "mdocr", "version": __version__}
        self.configuration = {
            "engine": "paddleocr",
            "model": _MODEL,
            "jpeg_quality": JPEG_QUALITY,
        }

    def resource(self, ref, source, output, selected):
        record = {"resource_id": ref.target, "status": "unselected"}
        if time.monotonic() >= self.deadline:
            if selected:
                self.stop = self.stop or "BATCH_DEADLINE_EXCEEDED"
                record.update(status="unattempted", reason=self.stop)
            return record, None
        try:
            prepared = prepare_one(
                ref,
                source,
                output.parent,
                download=self.args.download,
                session=self.session,
                retain_original=True,
            )
            # prepare_one already computes this original-byte identity. Reusing
            # its full digest avoids collisions between cache records; byte and
            # configuration comparisons below remain the reuse authority.
            entry = self.cache / __version__ / _MODEL / prepared.identity
            cached = self.read_cache(entry, prepared)
            if cached is not None:
                record.update(status="reused", **cached)
                return record, build_ocr_block(
                    cached["prepared_image"],
                    cached["text"],
                )
            if not selected:
                return record, None
            if self.stop:
                record.update(status="unattempted", reason=self.stop)
                return record, None
            if time.monotonic() >= self.deadline:
                self.stop = "BATCH_DEADLINE_EXCEEDED"
                record.update(status="unattempted", reason=self.stop)
                return record, None
            token = os.environ.get("PADDLEOCR_TOKEN")
            if not token:
                raise OcrSharedError("token unavailable", code="OCR_AUTH_UNAVAILABLE")
            if self.client is None:
                self.client = PaddleClient(
                    token, session=self.session, endpoint=self.args.endpoint
                )
            text = self.client.convert(prepared.path)
            if not text.strip():
                raise ValueError("empty OCR text")
            cached = self.write_cache(entry, prepared, text)
            record.update(status="success", **cached)
            return record, build_ocr_block(cached["prepared_image"], text)
        except OcrSharedError as exc:
            self.stop = exc.code
            record.update(status="failed", reason=exc.code)
        except (
            ImageError,
            ImageSkip,
            OSError,
            ValueError,
            TypeError,
            RuntimeError,
            requests.RequestException,
        ):
            if selected:
                record.update(status="failed", reason="IMAGE_PROCESSING_FAILED")
        return record, None

    def read_cache(self, entry, prepared):
        try:
            value = json.loads((entry / "record.json").read_text(encoding="utf-8"))
            if (
                value["preprocessing"] != self.preprocessing
                or value["configuration"] != self.configuration
            ):
                return None
            if (entry / "prepared.jpg").read_bytes() != prepared.path.read_bytes():
                return None
            if (entry / "original").read_bytes() != prepared.original.read_bytes():
                return None
            text = value["text"]
            if not isinstance(text, str) or not text.strip():
                return None
            if (entry / "ocr.md").read_text(encoding="utf-8") != build_ocr_block(
                "prepared.jpg", text
            ):
                return None
            return self.evidence(entry, text)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def evidence(entry, text):
        return {
            "source_path": str(entry / "original"),
            "prepared_image": str(entry / "prepared.jpg"),
            "artifact": str(entry / "ocr.md"),
            "text": text,
        }

    def write_cache(self, entry, prepared, text):
        from .cli import atomic_write

        entry.mkdir(parents=True, exist_ok=True)
        (entry / "original").write_bytes(prepared.original.read_bytes())
        (entry / "prepared.jpg").write_bytes(prepared.path.read_bytes())
        atomic_write(entry / "ocr.md", build_ocr_block("prepared.jpg", text))
        atomic_write(
            entry / "record.json",
            json.dumps(
                {
                    "preprocessing": self.preprocessing,
                    "configuration": self.configuration,
                    "text": text,
                }
            ),
        )
        return self.evidence(entry, text)

    def document(self, text, source, output, selection):
        records = {}
        replacements = {}
        protected, placeholders = _protect_existing_blocks(text)
        relocated = []
        for marker, block in placeholders:
            reference = _OCR_BLOCK_RE.fullmatch(block).group(1)
            image = _local_image_path(reference, source)
            if image is not None:
                block = block.replace(f'source="{reference}"', f'source="{image}"', 1)
                block = block.replace(
                    f"> Original image: [{reference}]({reference})",
                    f"> Original image: [{image}]({image})",
                )
            relocated.append((marker, block))

        def transform(ref):
            if ref.target not in records:
                selected = selection is None or ref.target in selection
                records[ref.target], replacements[ref.target] = self.resource(
                    ref, source, output, selected
                )
            replacement = replacements[ref.target]
            if replacement is None and not ref.is_remote and not ref.unsupported:
                return render_image(
                    ref.alt, str((source.parent / ref.target).resolve()), ref.title
                )
            return replacement

        transformed, warnings, errors = transform_images(protected, transform)
        if selection is not None:
            for target in selection:
                if target not in records:
                    records[target] = {
                        "resource_id": target,
                        "status": "unattempted",
                        "reason": "RESOURCE_NOT_FOUND",
                    }
        return (
            _restore_blocks(transformed, relocated),
            list(records.values()),
            warnings,
            errors,
        )


def run_batch(args) -> int:
    from .cli import atomic_write

    documents = _documents(args.manifest)
    if not 0 <= args.max_seconds < float("inf"):
        raise ValueError("INVALID_BATCH_DEADLINE")
    protected = {args.manifest.resolve()} | {
        (args.manifest.parent / row["path"]).resolve() for row in documents
    }
    for document in documents:
        source = (args.manifest.parent / document["path"]).resolve()
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for _match, ref in iter_image_matches(text):
            # Supported local targets are decoded by the parser: match
            # prepare_one's exact read path. Other local URL forms remain
            # encoded and need one decode for input protection only.
            image = (
                (source.parent / ref.target).resolve()
                if not ref.is_remote and not ref.unsupported
                else _local_image_path(unquote(ref.target), source)
            )
            if image is not None:
                protected.add(image)
        for block in _OCR_BLOCK_RE.finditer(text):
            image = _local_image_path(block.group(1), source)
            if image is not None:
                protected.add(image)
    outputs = [
        (args.output_dir / str(i) / "document.md").resolve()
        for i in range(len(documents))
    ]
    if (
        args.report.resolve() in protected
        or any(path in protected for path in outputs)
        or args.report.resolve() in outputs
    ):
        raise ValueError("OUTPUT_OVERLAPS_INPUT")
    batch = Batch(args)
    rows = []
    try:
        for document, output in zip(documents, outputs):
            source = (args.manifest.parent / document["path"]).resolve()
            row = {
                "document_id": document["document_id"],
                "path": str(source),
                "output": None,
                "status": "failed",
                "warnings": [],
                "resources": [],
                "ocr": {
                    "contract": "prepared-jpeg-ocr-v1",
                    "preprocessing": batch.preprocessing,
                    "configuration": batch.configuration,
                    "images": [],
                },
            }
            try:
                text = source.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                row["error"] = "DOCUMENT_READ_FAILED"
            else:
                text, resources, warnings, errors = batch.document(
                    text, source, output, document.get("resources")
                )
                atomic_write(output, text)
                images = []
                for resource in resources:
                    if resource["status"] in {"success", "reused"}:
                        images.append(
                            {
                                key: value
                                for key, value in resource.items()
                                if key not in {"text", "status"}
                            }
                            | {"status": "success"}
                        )
                    resource.pop("text", None)
                row["ocr"]["images"] = images
                partial = bool(errors) or any(
                    r["status"] in {"failed", "unattempted"} for r in resources
                )
                row.update(
                    output=str(output),
                    status="partial" if partial else "completed",
                    resources=resources,
                    warnings=warnings,
                    errors=errors,
                )
            rows.append(row)
    finally:
        batch.session.close()
    partial = any(row["status"] != "completed" for row in rows)
    report = {
        "schema_version": "mdocr-batch-1.0",
        "status": "partial" if partial else "completed",
        "documents": rows,
        "counts": dict(
            Counter(resource["status"] for row in rows for resource in row["resources"])
        ),
        "causes": [batch.stop] if batch.stop else [],
    }
    atomic_write(args.report, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 2 if partial else 0
