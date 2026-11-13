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


def retain_ocr_files(directory, run, result, output, resources):
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
        text = relocate_targets(
            text,
            {
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
        directory = store.input_directory(identifier)
        raw = store.document(identifier)["raw"]
        target, resources = retain_raw(
            directory,
            Path(row["raw_path"]),
            [dict(r) for r in raw.get("resources", [])],
        )
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
        retained = retain_ocr_files(directory, run, result, output, resources)
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
    """Replace declared inline destinations without changing unrelated prose."""
    for before, after in replacements.items():
        for spelling in {before, quote(before, safe="/:")}:
            for ending in (")", " ", "\t", "\n"):
                text = text.replace(
                    "](" + spelling + ending, "](" + quote(after, safe="/:") + ending
                )
            text = text.replace(
                "](<" + spelling + ">", "](<" + quote(after, safe="/:") + ">"
            )
    return text


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
    # A tool-facing copy has a different parent: relocate known relative uses too.
    replacements = {before: after for before, after in local.items() if before != after}
    text = source.read_text(encoding="utf-8")
    text = relocate_targets(text, replacements)
    copy = run.directory / "ocr-inputs" / f"{row['slot']}.md"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(text, encoding="utf-8")
    selected = row["ocr_resources"]
    if selected is not None:
        selected = [replacements.get(target, target) for target in selected]
    return str(copy), selected, {value: key for key, value in replacements.items()}
