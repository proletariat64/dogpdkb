"""Compile full sources through Graphify's public CLI and result artifacts."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from dogpdkb.processes import run_command
from dogpdkb.records import Run, Store, atomic_json, read_json, version_key
from dogpdkb.settings import load_config, select_book


def _manifest(settings, book, store, run, reference):
    data = reference if isinstance(reference, dict) else read_json(Path(reference))
    if (
        not isinstance(data, dict)
        or data.get("schema") != "dogpdkb-compile-1"
        or data.get("book") != book.key
        or data.get("run_id") != run.id
        or not isinstance(data.get("documents"), list)
        or not isinstance(data.get("failures", []), list)
    ):
        raise ValueError("invalid compile manifest or book/run mismatch")
    seen = set()
    for row in data["documents"]:
        if not isinstance(row, dict) or not isinstance(row.get("document_id"), str):
            raise TypeError("compile source requires document_id")
        doc_id = row["document_id"]
        if (
            not doc_id
            or doc_id in seen
            or row.get("action") not in {"compile", "metadata", "reuse"}
        ):
            raise ValueError("duplicate source or invalid compile action")
        seen.add(doc_id)
        version = version_key(row.get("version"))
        if not isinstance(row.get("metadata", {}), dict):
            raise TypeError("source metadata must be an object")
        path = Path(row.get("path", ""))
        if (
            not path.is_absolute()
            or not path.is_file()
            or not path.resolve().is_relative_to(book.corpus)
        ):
            raise ValueError(
                f"full corpus source unavailable: {path}; generate a new plan"
            )
        document = store.data["documents"].get(doc_id, {})
        current = document.get("corpus", {})
        if (
            version_key(current.get("version")) != version
            or Path(current.get("path", "")).resolve() != path.resolve()
        ):
            raise ValueError(
                f"corpus version changed for {doc_id}; generate a new plan"
            )
        witness = row.get("input_copy") or current.get("input_copy")
        if (
            not witness
            or not Path(witness).is_file()
            or Path(witness).read_bytes() != path.read_bytes()
        ):
            raise ValueError(
                f"corpus input changed or missing for {doc_id}; generate a new plan"
            )
        graph = document.get("graph", {})
        if (
            graph.get("status") == "completed"
            and version_key(graph.get("version")) > version
        ):
            raise ValueError(
                f"newer graph already published for {doc_id}; generate a new plan"
            )
    return data


def _inventory(path):
    data = read_json(path)
    if (
        not isinstance(data, dict)
        or data.get("schema") != "graphify-source-inventory-1.0"
        or not isinstance(data.get("sources"), list)
    ):
        raise ValueError("invalid Graphify source inventory")
    seen = set()
    for row in data["sources"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("source_file"), str)
            or not row["source_file"]
            or row["source_file"] in seen
            or ("source_id" in row and not isinstance(row["source_id"], str))
            or not isinstance(row.get("metadata", {}), dict)
        ):
            raise ValueError("invalid or duplicate Graphify inventory source")
        seen.add(row["source_file"])
    return data["sources"]


def _report(path, requested, code, graph_path):
    data = read_json(path)
    if (
        not isinstance(data, dict)
        or data.get("schema") != "graphify-source-result-1.0"
        or not isinstance(data.get("sources"), list)
    ):
        raise ValueError("invalid Graphify source result")
    if code == 1 and data.get("status") == "failed" and not data["sources"]:
        raise ValueError(
            f"Graphify could not publish: {data.get('error', 'no source result')}"
        )
    expected = {row["source_id"]: row for row in requested}
    found, completed = {}, 0
    for row in data["sources"]:
        if not isinstance(row, dict):
            raise TypeError("invalid Graphify source result row")
        key = row.get("source_id")
        if not isinstance(key, str):
            raise TypeError("Graphify source_id must be a string")
        request = expected.get(key)
        if (
            not request
            or key in found
            or row.get("source_file") != request["source_file"]
            or row.get("action") != request["action"]
            or row.get("status") not in {"completed", "failed"}
        ):
            raise ValueError(
                "Graphify result does not cover the requested source contract"
            )
        found[key] = row
        completed += row["status"] == "completed"
    failed = len(expected) - completed
    status = "partial" if failed and completed else "failed" if failed else "completed"
    counts = data.get("counts")
    if not isinstance(counts, dict) or any(
        type(counts.get(key)) is not int for key in ("total", "completed", "failed")
    ):
        raise ValueError("Graphify counts must be integers")
    if (
        set(found) != set(expected)
        or counts != {"total": len(expected), "completed": completed, "failed": failed}
        or data.get("status") != status
        or code != (2 if failed else 0)
        or Path(data.get("graph", "")).resolve() != graph_path.resolve()
        or (completed and not graph_path.is_file())
    ):
        raise ValueError("Graphify result/count/exit/publication contract mismatch")
    return found, completed, failed


def run_graph(settings, book, store, run, manifest=None) -> int:
    """Caller holds the writer lock. Only acknowledged sources advance graph truth."""
    manifest = manifest or run.data["artifacts"].get("manifest")
    if not manifest:
        raise ValueError("a complete-source compile manifest is required")
    data = _manifest(settings, book, store, run, manifest)
    failures = bool(data.get("failures"))
    pending = []
    for row in data["documents"]:
        doc_id, version = row["document_id"], row["version"]
        previous = store.step(doc_id, "graph")
        if store.suppressed(doc_id, "graph", version):
            failures = True
            run.failure(
                document_id=doc_id,
                version=version,
                step="graph",
                reason="SAME_VERSION_NOT_RETRIED",
                previous_status=previous["status"],
            )
            continue
        if (
            previous
            and previous["status"] == "completed"
            and previous.get("run_id") == run.id
            and version_key(previous["version"]) == version_key(version)
        ):
            continue
        pending.append(row)
    if not pending:
        return 2 if failures else 0
    graph_dir = settings.graph_for(book)
    inventory_path = run.directory / "graph-inventory.json"
    run.stage("graph-inventory", total=len(pending))
    code = run_command(
        [
            *settings.command("graphify"),
            "source-inventory",
            "--graph-dir",
            str(graph_dir),
            "--output",
            str(inventory_path),
        ],
        log=run.directory / "graphify.log",
    )
    if code:
        run.failure(
            step="graph-inventory", reason="GRAPHIFY_INVENTORY_FAILED", exit_code=code
        )
        return code if code in {130, 143} else 1
    sources = _inventory(inventory_path)
    requested, by_id = [], {}
    for row in pending:
        doc_id = row["document_id"]
        source_id = f"{book.key}:{doc_id}"
        source_file = str(Path(row["path"]).resolve().relative_to(book.corpus))
        graph = store.document(doc_id).get("graph", {})
        old = [graph["source_file"]] if graph.get("source_file") else []
        for source in sources:
            match = re.search(r"-\[(\d+)\]\.md$", Path(source["source_file"]).name)
            if (
                source.get("source_id") == source_id
                or (not source.get("source_id") and match and match.group(1) == doc_id)
            ) and source["source_file"] not in old:
                old.append(source["source_file"])
        if source_file not in old:
            old.append(source_file)
        graph = store.document(doc_id).get("graph", {})
        known_version = (
            graph.get("version") if graph.get("status") == "completed" else None
        )
        reusable = bool(known_version) and version_key(known_version) in {
            version_key(row["version"]),
            version_key(row["baseline_version"])
            if row.get("baseline_version")
            else None,
        }
        action = (
            "metadata"
            if row["action"] in {"metadata", "reuse"} and reusable
            else "compile"
        )
        metadata = dict(row.get("metadata", {}))
        metadata.update(version=row["version"], book=book.key, document_id=doc_id)
        if metadata.get("source_ref"):
            metadata["source_url"] = metadata["source_ref"]
        item = {
            "source_id": source_id,
            "source_file": source_file,
            "path": row["path"],
            "replace": old,
            "metadata": metadata,
            "action": action,
        }
        requested.append(item)
        by_id[source_id] = row
    run.data["artifacts"].update(
        graph_inventory=str(inventory_path), graph_sources=str(run.directory / "graph")
    )
    completed = failed = 0
    for index, item in enumerate(requested):
        row = by_id[item["source_id"]]
        # Earlier sources may take hours; recheck this full input at its turn.
        _manifest(settings, book, store, run, {**data, "documents": [row]})
        request_path = run.directory / "graph" / str(index) / "request.json"
        report_path = request_path.with_name("result.json")
        atomic_json(
            request_path, {"schema": "graphify-source-update-1.0", "sources": [item]}
        )
        run.data["artifacts"].update(
            graph_request=str(request_path), graph_report=str(report_path)
        )
        run.stage("graphify", total=len(requested), completed=completed, failed=failed)
        store.attempt(
            row["document_id"],
            "graph",
            row["version"],
            run.id,
            input=row["path"],
            request=str(request_path),
            report=str(report_path),
        )
        report_path.unlink(missing_ok=True)
        code = run_command(
            [
                *settings.command("graphify"),
                "update-sources",
                "--graph-dir",
                str(graph_dir),
                "--request",
                str(request_path),
                "--output",
                str(report_path),
            ],
            log=run.directory / "graphify.log",
        )
        interrupted = code in {130, 143}
        try:
            if interrupted or code == 124:
                raise ValueError("Graphify interrupted before a known result")
            result, source_completed, source_failed = _report(
                report_path, [item], code, graph_dir / "graph.json"
            )
        except (TypeError, ValueError) as error:
            store.complete(
                row["document_id"],
                "graph",
                row["version"],
                "unknown",
                run.id,
                report=str(report_path),
                reason=str(error),
            )
            run.failure(
                document_id=row["document_id"],
                version=row["version"],
                step="graph",
                reason="GRAPHIFY_RESULT_UNKNOWN",
                message=str(error),
                request=str(request_path),
                report=str(report_path),
                exit_code=code,
            )
            return code if interrupted else 1
        result_row = result[item["source_id"]]
        completed += source_completed
        failed += source_failed
        doc_id = row["document_id"]
        status = result_row["status"]
        if status == "completed":
            document = store.document(doc_id)
            document["graph"] = {
                "status": "completed",
                "version": row["version"],
                "source_file": result_row["source_file"],
                "run_id": run.id,
                "report": str(report_path),
            }
            pending = document.get("pending")
            if (
                pending
                and not row.get("pending_steps")
                and version_key(pending["version"]) == version_key(row["version"])
                and pending.get("metadata")
                == row.get("pending_metadata", row.get("metadata", {}))
            ):
                document.pop("pending", None)
        # Persist graph truth and the completed step in the same state write.
        store.complete(
            doc_id,
            "graph",
            row["version"],
            status,
            run.id,
            report=str(report_path),
            reason=result_row.get("error"),
        )
        if status == "failed":
            run.failure(
                document_id=doc_id,
                version=row["version"],
                step="graph",
                reason="GRAPHIFY_SOURCE_FAILED",
                message=result_row.get("error"),
                report=str(report_path),
            )
    run.stage("graphify", total=len(requested), completed=completed, failed=failed)
    return 2 if failures or failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dogpdkb update-graph", description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        type=Path,
        help="complete-source manifest from apply",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    run = None
    try:
        settings = load_config(args.config)
        book = select_book(settings, args.book)
        store = Store(settings, book)
        with store.lock():
            manifest = read_json(args.input)
            if not isinstance(manifest, dict) or not isinstance(
                manifest.get("run_id"), str
            ):
                raise TypeError("invalid complete-source manifest")
            run = Run.load(store, manifest.get("run_id", ""))
            run.data.update(status="running", pid=os.getpid(), ended_at=None)
            run.save()
            code = run_graph(settings, book, store, run, manifest)
            run.finish(
                "interrupted"
                if code in {130, 143}
                else "completed"
                if code == 0
                else "partial"
                if code == 2
                else "failed",
                code,
            )
            import json

            print(
                json.dumps(
                    {
                        "run_id": run.id,
                        "exit_code": code,
                        "status": run.data["status"],
                        "report": run.data["artifacts"].get("graph_report"),
                    }
                )
            )
            return code
    except (OSError, TypeError, ValueError) as error:
        if run:
            run.finish("failed", 1, str(error))
        print(f"dogpdkb update-graph: {error}", file=sys.stderr)
        return 1
