"""Compile full sources through Graphify's public CLI and result artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from dogpdkb.help import HelpParser
from dogpdkb.phases import check_graph_manifest, graph_handoff_matches
from dogpdkb.planning import reject_rollback
from dogpdkb.processes import diagnostic_argv, run_command
from dogpdkb.records import Run, Store, atomic_json, read_json, version_key
from dogpdkb.settings import load_config, select_book


def _manifest(book, store, run, reference):
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
            or row.get("action")
            not in {"compile", "metadata", "reuse", "retain", "defer"}
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
        expected = row.get("input_copy_sha256")
        if (
            not witness
            or not Path(witness).is_file()
            or Path(witness).read_bytes() != path.read_bytes()
            or (expected is not None and _digest(path) != expected)
        ):
            raise ValueError(
                f"corpus input changed or missing for {doc_id}; generate a new plan"
            )
        reject_rollback(store, [row])
    return data


_NATIVE_ENV = {
    "GRAPHIFY_FORCE": "0",
    "GRAPHIFY_OPENAI_OAUTH_PARALLEL": "1",
}


def _native_extract(command, corpus, token_budget, model, concurrency):
    argv = [
        *command,
        "extract",
        str(corpus),
        "--out",
        str(corpus),
        "--no-cluster",
        "--backend",
        "openai-oauth",
        "--max-concurrency",
        str(concurrency),
    ]
    if model is not None:
        argv.extend(["--model", model])
    if token_budget is not None:
        argv.extend(["--token-budget", str(token_budget)])
    return argv


def _prove_native_result(log, graph_path):
    """Require the pinned CLI's terminal output and complete chunk accounting."""
    done = set()
    totals = set()
    dispatched = 0
    cache = None
    written = unchanged = False
    with log.open(encoding="utf-8") as lines:
        for line in lines:
            chunk = re.fullmatch(
                r"\[graphify extract\] chunk (\d+)/(\d+) done\s*", line
            )
            if chunk:
                index, total = map(int, chunk.groups())
                if not dispatched or written or unchanged or index in done:
                    raise IncompleteExtraction("unexpected native chunk completion")
                done.add(index)
                totals.add(total)
            elif match := re.fullmatch(
                r"\[graphify extract\] semantic cache: ([1-9]\d*) hit / (\d+) miss\s*",
                line,
            ):
                if cache is not None or dispatched or written or unchanged:
                    raise IncompleteExtraction("unexpected native cache accounting")
                cache = tuple(map(int, match.groups()))
            elif match := re.fullmatch(
                r"\[graphify extract\] semantic extraction on ([1-9]\d*) files via .+",
                line.rstrip(),
            ):
                if dispatched or written or unchanged:
                    raise IncompleteExtraction("unexpected native semantic dispatch")
                dispatched = int(match[1])
            elif line.startswith(f"[graphify extract] wrote {graph_path} — "):
                if written or unchanged:
                    raise IncompleteExtraction("multiple native terminal outputs")
                written = True
            elif line.strip() == (
                "[graphify extract] no incremental changes detected "
                "(--no-cluster); outputs left untouched."
            ):
                if written or unchanged:
                    raise IncompleteExtraction("multiple native terminal outputs")
                unchanged = True
            elif line.startswith(
                (
                    "[graphify extract] semantic extraction is incomplete:",
                    "[graphify extract] semantic extraction failed:",
                    "[graphify extract] AST extraction incomplete for ",
                    "[graphify extract] warning: could not write manifest:",
                    "[graphify extract] warning: could not write semantic cache:",
                    "[graphify extract] warning: could not prune semantic cache:",
                )
            ) or re.match(
                r"\[graphify\] WARNING: \d+/\d+ semantic chunk\(s\) failed", line
            ):
                raise IncompleteExtraction("native CLI reported incomplete output")
    if written == unchanged or (dispatched and not written):
        raise IncompleteExtraction("native CLI did not confirm a settled result")
    if dispatched and (
        not done or totals != {len(done)} or min(done) != 1 or max(done) != len(done)
    ):
        raise IncompleteExtraction("native chunk completion is missing or inconsistent")
    if cache is not None and cache[1] != dispatched:
        raise IncompleteExtraction("native cache misses were not all dispatched")


class IncompleteExtraction(ValueError):
    """Native result did not complete the selected graph handoff."""


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_graph(
    settings,
    book,
    store,
    run,
    manifest=None,
    *,
    retry_graph=False,
    replan=False,
) -> int:
    """Run native extraction in place and acknowledge only a settled result."""
    manifest = manifest or run.data["artifacts"].get("manifest")
    if not manifest:
        raise ValueError("a complete-source compile manifest is required")
    if "graph_retry" in run.data:
        frozen = Path(manifest).read_bytes()
        check_graph_manifest(run, manifest, content=frozen)
        manifest = json.loads(frozen)
    data = _manifest(book, store, run, manifest)
    partial = bool(data.get("failures")) or any(
        row["action"] == "defer" for row in data["documents"]
    )
    pending = []
    reconciled = 0
    for row in data["documents"]:
        if row["action"] != "compile":
            continue
        previous = store.step(row["document_id"], "graph") or {}
        if (
            previous.get("status") == "completed"
            and previous.get("run_id") == run.id
            and version_key(previous.get("version")) == version_key(row["version"])
            and previous.get("metadata") == row.get("metadata", {})
            and previous.get("input_sha256") == _digest(Path(row["path"]))
            and Path(previous.get("input_copy", "")).is_file()
            and Path(previous["input_copy"]).read_bytes()
            == Path(row["path"]).read_bytes()
        ):
            reconciled += 1
            continue
        pending.append(row)
    if not pending:
        run.data["graph_publication"] = "published" if reconciled else "no_work"
        run.stage("graphify", total=reconciled, completed=reconciled, failed=0)
        return 2 if partial else 0
    if (
        retry_graph
        and "graph_generation" in run.data
        and not graph_handoff_matches(store, book, run)
    ):
        run.failure(step="graph", reason="STALE_GRAPH_HANDOFF", send_state="not_sent")
        return 2
    attempted = {row["document_id"]: row for row in pending}
    selected = set(attempted)
    input_digests = {
        doc_id: _digest(Path(row["path"])) for doc_id, row in attempted.items()
    }
    retrying = False
    # Record possible sends in the fixed directory, not just selected facts.
    for doc_id, document in store.data["documents"].items():
        entry = document.get("corpus", {})
        version, path = entry.get("version"), entry.get("path")
        if not (
            version
            and path
            and Path(path).is_file()
            and Path(path).resolve().is_relative_to(book.corpus)
        ):
            continue
        digest = input_digests[doc_id] if doc_id in selected else _digest(Path(path))
        if entry.get("input_copy_sha256", digest) != digest:
            run.failure(
                document_id=doc_id,
                version=version,
                step="graph",
                reason="CORPUS_PUBLICATION_UNSETTLED",
                send_state="not_sent",
            )
            return 2
        attempt = store.blocked_attempt(doc_id, "graph", version)
        previous = store.step(doc_id, "graph") or {}
        if attempt:
            own_scan = (
                previous.get("scan_only")
                and previous["run_id"] == run.id
                and version_key(previous["version"]) == version_key(version)
            )
            if not (
                retry_graph
                and attempt["run_id"] == run.id
                and (doc_id in selected or own_scan)
            ):
                run.failure(
                    document_id=doc_id,
                    version=version,
                    step="graph",
                    reason="SAME_VERSION_NOT_RETRIED",
                    send_state="not_sent",
                )
                return 2
            if doc_id not in selected and (
                previous.get("input") != path or previous.get("input_sha256") != digest
            ):
                raise ValueError(
                    "a source exposed by the saved Graphify attempt changed"
                )
            retrying = True
        elif (
            doc_id in selected
            or digest == document.get("graph", {}).get("sha256")
            or (
                previous.get("status") == "completed"
                and previous.get("scan_only")
                and previous.get("input_sha256") == digest
            )
        ):
            continue
        if doc_id not in selected:
            attempted[doc_id] = {
                "document_id": doc_id,
                "version": version,
                "path": path,
            }
            input_digests[doc_id] = digest

    if retry_graph and "graph_retry" in run.data and not (retrying or replan):
        raise ValueError(
            "no prior graph attempt to retry; resume the graph stage instead"
        )

    corpus = book.corpus
    graph_dir = settings.graph_for(book)
    graph_path = graph_dir / "graph.json"
    native_manifest = graph_dir / "manifest.json"
    run.stage("graphify", total=len(pending), completed=0, failed=0, phase="prepare")
    evidence_root = store.path.parent / "graph-evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence_dir = Path(tempfile.mkdtemp(prefix="graphify-", dir=evidence_root))
    report = evidence_dir / "result.json"
    log = evidence_dir / "extract.log"
    run.data["artifacts"]["graph_report"] = str(report)
    store.complete_batch(
        (
            {
                "doc_id": row["document_id"],
                "name": "graph",
                "version": row["version"],
                "status": "running",
                "run_id": run.id,
                "input": row["path"],
                "report": str(report),
                "input_sha256": input_digests[row["document_id"]],
                "scan_only": row["document_id"] not in selected,
            }
            for row in attempted.values()
        ),
        durable=True,
    )
    store.record_graph_writer(run.id)
    run.stage("graphify", total=len(pending), completed=0, failed=0, phase="tools")
    argv = _native_extract(
        settings.command("graphify"),
        corpus,
        settings.graphify_token_budget,
        settings.graphify_model,
        settings.graphify_concurrency,
    )
    code = run_command(
        argv, log=log, env={**_NATIVE_ENV, "GRAPHIFY_OUT": str(graph_dir)}
    )
    status, reason = "failed", None
    if code in {124, 130, 143}:
        status, reason = "unknown", "Graphify interrupted before a known result"
    elif code:
        reason = f"Graphify directory extraction exited {code}"
    else:
        try:
            if not (graph_path.is_file() and native_manifest.is_file()):
                raise ValueError("native graph or manifest is missing")
            _prove_native_result(log, graph_path)
            _manifest(book, store, run, data)
            if any(
                _digest(Path(row["path"])) != input_digests[row["document_id"]]
                for row in attempted.values()
            ):
                raise ValueError("Corpus input changed while Graphify was running")
            status = "completed"
        except IncompleteExtraction as error:
            reason = f"GRAPHIFY_INCOMPLETE_EXTRACTION: {error}"
        except (OSError, TypeError, ValueError) as error:
            status, reason = "unknown", str(error)
    evidence = {
        "schema": "dogpdkb-graph-result-1",
        "status": status,
        "exit_code": code,
        "argv": diagnostic_argv(argv),
        "graph": str(graph_path),
        "native_log": str(log),
        "input_sha256": input_digests,
        "corpus": str(corpus),
        "graphify_model": settings.graphify_model,
        "graphify_token_budget": settings.graphify_token_budget,
        "graphify_concurrency": settings.graphify_concurrency,
        "reason": reason,
    }
    run.stage(
        "graphify", total=len(pending), completed=0, failed=0, phase="record-results"
    )
    outcomes = []
    for row in attempted.values():
        doc_id = row["document_id"]
        scan_only = doc_id not in selected
        acknowledgement = {}
        if status == "completed" and not scan_only:
            document = store.document(doc_id)
            witness = Path(row.get("input_copy") or document["corpus"]["input_copy"])
            # Corpus normally provides a durable witness. A compile handoff can
            # still carry its own wash copy; never pin that path as a baseline.
            if not witness.resolve().is_relative_to(store.input_directory(doc_id)):
                body = witness.read_bytes()
                witness = evidence_dir / f"{doc_id}.md"
                with witness.open("xb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
            source_file = Path(row["path"]).relative_to(book.corpus)
            acknowledgement = {
                "input_copy": str(witness),
                "action": row["action"],
                "metadata": row.get("metadata", {}),
            }
            document["graph"] = {
                "status": "completed",
                "version": row["version"],
                "source_file": str(source_file),
                "sha256": input_digests[doc_id],
                "run_id": run.id,
                "report": str(report),
            }
            document["graph_baseline"] = {
                "path": str(witness),
                "source_file": str(Path(row["path"]).relative_to(book.corpus)),
                "version": row["version"],
                "metadata": row.get("metadata", {}),
                **{key: row[key] for key in ("resources", "ocr") if key in row},
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
        outcomes.append(
            {
                "doc_id": doc_id,
                "name": "graph",
                "version": row["version"],
                "status": status,
                "run_id": run.id,
                "report": str(report),
                "reason": reason,
                "input": row["path"],
                "input_sha256": input_digests[doc_id],
                "scan_only": scan_only,
                **acknowledgement,
            }
        )
        if status != "completed":
            run.failure(
                persist=False,
                document_id=doc_id,
                version=row["version"],
                step="graph",
                reason="GRAPHIFY_BATCH_FAILED"
                if status == "failed"
                else "GRAPHIFY_RESULT_UNKNOWN",
                message=reason,
                report=str(report),
                reports=[str(log)],
                log=str(log),
                exit_code=code,
                argv=diagnostic_argv(argv),
                input_sha256=input_digests[doc_id],
                scan_only=scan_only,
            )
    atomic_json(report, evidence, durable=True)
    store.complete_batch(outcomes, durable=True)
    complete = len(pending) if status == "completed" else 0
    run.data["graph_result"] = status
    run.data["graph_publication"] = "published" if complete else "unknown"
    run.stage(
        "graphify",
        total=len(pending),
        completed=complete,
        failed=len(pending) - complete,
    )
    return (
        code
        if code in {130, 143}
        else (2 if partial else 0)
        if status == "completed"
        else 2
        if status == "failed"
        else 1
    )


def main(argv=None) -> int:
    parser = HelpParser(prog="dogpdkb update-graph", description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    parser.add_argument("--retry-graph", action="store_true")
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
        return 64 if error.code == 2 else int(error.code or 0)
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
            code = run_graph(
                settings,
                book,
                store,
                run,
                args.input,
                retry_graph=args.retry_graph,
            )
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
