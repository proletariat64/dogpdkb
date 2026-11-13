"""Keep latest successful inputs outside disposable run directories."""

import os
import shutil
import tempfile
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


def retain_ocr_files(directory, run, result, output):
    """Relocate only file paths declared by the public OCR result contract."""
    mappings = {}
    records = [*result.get("resources", []), *result.get("ocr", {}).get("images", [])]
    files = directory / "ocr-files"
    if files.exists() and not any(
        isinstance(item.get(field), str) and Path(item[field]).is_relative_to(files)
        for item in records
        for field in ("source_path", "prepared_image", "artifact")
    ):
        shutil.rmtree(files)
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
        directory = store.input_directory(identifier)
        raw = store.document(identifier)["raw"]
        target, resources = retain_raw(
            directory,
            Path(row["raw_path"]),
            [dict(r) for r in raw.get("resources", [])],
        )
        raw.update(path=str(target), resources=resources)
        row["resources"] = resources
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
            result = next(
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
        retained = retain_ocr_files(directory, run, result, output)
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
