"""Reclaim only the current finished sync's disposable files."""

import sys
from pathlib import Path

from dogpdkb.records import atomic_json, read_json


def failure_files(store, run):
    """Keep failed source bundles using the orchestrator plan and public reports."""
    keep = set()
    root = run.directory

    def retain(value):
        if not isinstance(value, str):
            return
        path = Path(value)
        if path.is_relative_to(root) and path.is_file():
            keep.add(path)

    plan_path = run.data["artifacts"].get("plan")
    rows = read_json(Path(plan_path))["documents"] if plan_path else []
    by_id = {row["document_id"]: row for row in rows}
    for failure in run.data["failures"]:
        row = by_id.get(str(failure.get("document_id")), {})
        if failure.get("step") == "graph" and row:
            corpus = store.document(row["document_id"]).get("corpus", {}).get("path")
            raw = row.get("raw_path")
            if (
                corpus
                and raw
                and Path(corpus).is_file()
                and Path(raw).is_file()
                and Path(corpus).read_bytes() == Path(raw).read_bytes()
            ):
                failure["input"] = corpus
            else:
                failure["input"] = raw
        retain(failure.get("input"))
        # A rejected export never entered the catalog/plan. Preserve its local
        # bundle, excluding the other documents' known successful raw files.
        source = Path(failure.get("input") or "")
        if not row and source.is_file() and source.is_relative_to(root):
            other_sources = {Path(item["raw_path"]) for item in rows}
            for path in source.parent.rglob("*"):
                if path not in other_sources:
                    retain(str(path))
        for value in [
            failure.get("report"),
            *failure.get("reports", []),
            *failure.get("inputs", []),
        ]:
            retain(value)
        if row:
            if failure.get("step") != "graph" or failure.get("input") == row.get(
                "raw_path"
            ):
                retain(row.get("raw_path"))
            for resource in row.get("resources", []):
                retain(resource.get("path"))
        request = root / f"ocr-{row['slot']}-request.json" if row else None
        if request and request.is_file():
            keep.add(request)
            for document in read_json(request).get("documents", []):
                retain(document.get("path"))
        report = failure.get("report")
        if report and Path(report).is_file() and failure.get("step") == "ocr":
            try:
                payload = read_json(Path(report))
                if not isinstance(payload, dict):
                    continue
                for document in payload.get("documents", []):
                    if not isinstance(document, dict):
                        continue
                    retain(document.get("path"))
                    retain(document.get("output"))
                    for resource in document.get("resources", []):
                        if not isinstance(resource, dict):
                            continue
                        for field in ("source_path", "prepared_image", "artifact"):
                            retain(resource.get(field))
            except (ValueError, KeyError, TypeError):
                pass
    return keep


def clean_run(store, run):
    if run.data["command"] != "sync" or run.data["status"] not in {
        "completed",
        "partial",
    }:
        return
    run.data["cleanup"] = {"status": "started"}
    try:
        root = run.directory
        if (
            root.is_symlink()
            or store.runs.is_symlink()
            or root.parent.resolve() != store.runs.resolve()
        ):
            raise ValueError("refusing cleanup outside the selected book wash")
        protected = (
            store.settings.state,
            store.book.corpus,
            store.settings.graph_for(store.book),
            store.settings.ocr_cache,
        )
        if any(
            root.resolve().is_relative_to(path.resolve())
            or path.resolve().is_relative_to(root.resolve())
            for path in protected
        ):
            raise ValueError("wash overlaps protected data")
        keep = {run.path, run.directory / "manifest.json"} | failure_files(store, run)
        run.save()

        def prune(directory):
            for path in directory.iterdir():
                if path.is_dir() and not path.is_symlink():
                    prune(path)
                    if not any(path.iterdir()):
                        path.rmdir()
                elif path not in keep:
                    path.unlink()

        # Only own graph-result JSON contains a discarded candidate workspace.
        for path in keep:
            if path.name == "result.json" and path.parent.name.startswith("graphify-"):
                report = read_json(path)
                report["candidate"] = None
                atomic_json(path, report)
        manifest_path = run.directory / "manifest.json"
        if manifest_path.is_file():
            manifest = read_json(manifest_path)
            manifest["failures"] = run.data["failures"]
            atomic_json(manifest_path, manifest)
        prune(run.directory)
        removed = {
            key: value
            for key, value in run.data["artifacts"].items()
            if not Path(value).exists()
        }
        run.data["artifacts"] = {
            key: value
            for key, value in run.data["artifacts"].items()
            if key not in removed
        }
        for document in store.data["documents"].values():
            for step in document.get("steps", {}).values():
                if step.get("run_id") != run.id:
                    continue
                for field in ("input", "report", "knowledge", "comparison"):
                    value = step.get(field)
                    if (
                        isinstance(value, str)
                        and Path(value).is_relative_to(run.directory)
                        and not Path(value).exists()
                    ):
                        step.pop(field)
        store.save()
        run.data["cleanup"].update(status="completed", removed_artifacts=removed)
        run.save()
    except (OSError, ValueError, KeyError, TypeError) as error:
        run.data["cleanup"].update(status="failed", reason=str(error))
        print(f"dogpdkb sync cleanup: {error}", file=sys.stderr)
        try:
            run.save()
        except (OSError, ValueError):
            pass
