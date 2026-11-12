"""Publish the tool's Markdown resource inventory as an ordinary manifest."""

import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .io import InputReader, publish_reports, referenced_paths, validate_outputs
from .manifest import ManifestError, load_manifest
from .model import CompareOptions
from .resources import _uses


def catalog_manifest(source: Path, output: Path) -> None:
    reader = InputReader(CompareOptions.max_file_bytes)
    documents = load_manifest(source, reader, body_only=True)
    value = json.loads(reader.read_bytes(source))
    originals = {(r["knowledge_base"], r["document_id"]): r for r in value["documents"]}
    rows = []
    protected = {source}
    for document in documents:
        row = dict(originals[(document.knowledge_base, document.document_id)])
        if not isinstance(row.get("resources", []), list):
            raise ManifestError("RESOURCE_SCHEMA_INVALID")
        ocr = row.get("ocr")
        if ocr is not None:
            if not isinstance(ocr, dict):
                raise ManifestError("PROVENANCE_SCHEMA_INVALID")
            if isinstance(ocr.get("images"), list):
                for image in ocr["images"]:
                    if isinstance(image, dict):
                        for key in ("source_path", "prepared_image", "artifact"):
                            if isinstance(image.get(key), str):
                                image[key] = str(document.resolve(image[key]).resolve())
        declared = {
            r["resource_id"]: dict(r)
            for r in row.get("resources", [])
            if isinstance(r, dict) and isinstance(r.get("resource_id"), str)
        }
        valid = {
            key: entry
            for key, entry in declared.items()
            if isinstance(entry.get("url"), str)
        }
        uses, warnings = _uses(reader.read_bytes(document.path), valid)
        resources = {}
        for use in uses:
            target = use["url"]
            entry = dict(
                next((r for r in declared.values() if r.get("url") == target), {})
            )
            entry.update(
                resource_id=entry.get("resource_id", target),
                url=target,
                kind=use["kind"],
            )
            if isinstance(entry.get("path"), str):
                entry["path"] = str(document.resolve(entry["path"]).resolve())
            elif not urlsplit(target).scheme:
                path = (document.path.parent / unquote(urlsplit(target).path)).resolve()
                if path.is_file():
                    entry["path"] = str(path)
            resources.setdefault(entry["resource_id"], entry)
        row.update(
            path=str(document.path.resolve()),
            resources=list(resources.values()),
            resource_warnings=warnings,
        )
        rows.append(row)
        protected.update(referenced_paths(document))
        protected.update(Path(r["path"]) for r in resources.values() if "path" in r)
    validate_outputs([output], protected | reader.paths)
    output.parent.mkdir(parents=True, exist_ok=True)
    publish_reports(
        [
            (
                output,
                json.dumps(
                    {"schema_version": "1.0", "documents": rows},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        ]
    )
