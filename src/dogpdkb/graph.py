"""Compile full sources through Graphify's public CLI and result artifacts."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from dogpdkb.planning import reject_rollback
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
            or current.get("metadata", {}) != row.get("metadata", {})
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
        reject_rollback(store, [row])
    return data


def _graph(path):
    data = read_json(path)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("nodes"), list)
        or not isinstance(data.get("links", data.get("edges")), list)
    ):
        raise TypeError("Graphify did not produce a graph artifact")
    return data


def run_graph(settings, book, store, run, manifest=None) -> int:
    """Run native directory extraction; publish only a complete batch."""
    manifest = manifest or run.data["artifacts"].get("manifest")
    if not manifest:
        raise ValueError("a complete-source compile manifest is required")
    data = _manifest(settings, book, store, run, manifest)
    pending = []
    for row in data["documents"]:
        previous = store.step(row["document_id"], "graph") or {}
        if (
            previous.get("status") == "completed"
            and previous.get("run_id") == run.id
            and version_key(previous.get("version")) == version_key(row["version"])
            and previous.get("metadata") == row.get("metadata", {})
            and Path(previous.get("input_copy", "")).is_file()
            and Path(previous["input_copy"]).read_bytes()
            == Path(row["path"]).read_bytes()
        ):
            continue
        pending.append(row)
    if not pending:
        return 2 if data.get("failures") else 0
    # A directory scan could otherwise silently retry a blocked source from an
    # earlier batch, even when the new plan selected different documents.
    for doc_id, document in store.data["documents"].items():
        version = document.get("corpus", {}).get("version")
        if version and store.suppressed(doc_id, "graph", version):
            run.failure(
                document_id=doc_id,
                version=version,
                step="graph",
                reason="SAME_VERSION_NOT_RETRIED",
            )
            return 2
    graph_dir = settings.graph_for(book)
    graph_path = graph_dir / "graph.json"
    if graph_path.exists() and not (graph_dir / "manifest.json").is_file():
        run.failure(step="graph", reason="GRAPHIFY_BASELINE_MANIFEST_REQUIRED")
        return 1
    if not graph_path.exists():
        selected = {Path(row["path"]).resolve() for row in pending}
        if any(path.resolve() not in selected for path in book.corpus.glob("*.md")):
            run.failure(step="graph", reason="GRAPHIFY_BASELINE_REQUIRED")
            return 1
    import tempfile

    workspace = Path(tempfile.mkdtemp(prefix="graphify-", dir=run.directory))
    candidate = workspace / "graphify-out"
    if graph_dir.exists():
        shutil.copytree(graph_dir, candidate)
    report = workspace / "result.json"
    run.data["artifacts"].update(
        graph_report=str(report), graph_candidate=str(candidate)
    )
    for row in pending:
        store.attempt(
            row["document_id"],
            "graph",
            row["version"],
            run.id,
            input=row["path"],
            report=str(report),
        )
    run.stage("graphify", total=len(pending), completed=0, failed=0)
    code = run_command(
        [
            *settings.command("graphify"),
            "extract",
            str(book.corpus),
            "--out",
            str(workspace),
            "--no-cluster",
        ],
        log=run.directory / "graphify.log",
        env={"GRAPHIFY_OUT": "graphify-out", "GRAPHIFY_FORCE": "0"},
    )
    status, reason = "failed", None
    if code in {124, 130, 143}:
        status, reason = "unknown", "Graphify interrupted before a known result"
    elif code:
        reason = f"Graphify directory extraction exited {code}"
    else:
        try:
            graph = _graph(candidate / "graph.json")
            tool_manifest = read_json(candidate / "manifest.json")
            if not isinstance(tool_manifest, dict):
                raise TypeError("Graphify did not produce its manifest")
            # Extraction may take hours. Do not publish if an input changed.
            _manifest(settings, book, store, run, data)
            graph_dir.mkdir(parents=True, exist_ok=True)
            for artifact in candidate.iterdir():
                if artifact.name in {"graph.json", "manifest.json", ".dogpdkb.lock"}:
                    continue
                target = graph_dir / artifact.name
                if artifact.is_dir():
                    shutil.copytree(artifact, target, dirs_exist_ok=True)
                else:
                    shutil.copyfile(artifact, target)
            # Native graph contents pass through without node/edge rewriting.
            # Publish graph before manifest: an interrupted publication must
            # never make a later scan skip work absent from the public graph.
            atomic_json(graph_path, graph)
            atomic_json(graph_dir / "manifest.json", tool_manifest)
            status = "completed"
        except (OSError, TypeError, ValueError) as error:
            status, reason = "unknown", str(error)
    atomic_json(
        report,
        {
            "schema": "dogpdkb-graph-result-1",
            "status": status,
            "exit_code": code,
            "graph": str(graph_path),
            "candidate": str(candidate),
            "reason": reason,
        },
    )
    for row in pending:
        doc_id = row["document_id"]
        acknowledgement = {}
        if status == "completed":
            witness = run.directory / "graph-success" / f"{doc_id}.md"
            witness.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(row["input_copy"], witness)
            acknowledgement = {
                "input_copy": str(witness),
                "action": row["action"],
                "metadata": row.get("metadata", {}),
            }
            document = store.document(doc_id)
            document["graph"] = {
                "status": "completed",
                "version": row["version"],
                "source_file": str(Path(row["path"]).relative_to(book.corpus)),
                "run_id": run.id,
                "report": str(report),
            }
            observed = document.get("pending")
            if (
                observed
                and observed.get("version") is not None
                and not row.get("pending_steps")
                and version_key(observed["version"]) == version_key(row["version"])
                and observed.get("metadata")
                == row.get("pending_metadata", row.get("metadata", {}))
            ):
                document.pop("pending", None)
        store.complete(
            doc_id,
            "graph",
            row["version"],
            status,
            run.id,
            report=str(report),
            reason=reason,
            **acknowledgement,
        )
        if status != "completed":
            run.failure(
                document_id=doc_id,
                version=row["version"],
                step="graph",
                reason="GRAPHIFY_BATCH_FAILED"
                if status == "failed"
                else "GRAPHIFY_RESULT_UNKNOWN",
                message=reason,
                report=str(report),
                exit_code=code,
            )
    complete = len(pending) if status == "completed" else 0
    run.stage(
        "graphify",
        total=len(pending),
        completed=complete,
        failed=len(pending) - complete,
    )
    return (
        code
        if code in {130, 143}
        else (2 if data.get("failures") else 0)
        if status == "completed"
        else 2
        if status == "failed"
        else 1
    )


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
