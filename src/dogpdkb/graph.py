"""Compile full sources through Graphify's public CLI and result artifacts."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path, PureWindowsPath

from dogpdkb.help import HelpParser
from dogpdkb.planning import reject_rollback
from dogpdkb.processes import diagnostic_argv, run_command
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


_NATIVE_VERSION = "graphify 0.9.111"
_NATIVE_ENV = {
    "GRAPHIFY_OUT": "graphify-out",
    "GRAPHIFY_FORCE": "0",
    "GRAPHIFY_OPENAI_OAUTH_PARALLEL": "1",
}


def _native_extract(command, corpus, workspace):
    return [
        *command,
        "extract",
        str(corpus),
        "--out",
        str(workspace),
        "--no-cluster",
        "--backend",
        "openai-oauth",
        "--max-concurrency",
        "3",
    ]


def _native_preflight(command, workspace):
    """Check the attested CLI protocol without reading live credentials or documents."""
    log = workspace / "version.log"
    code = run_command([*command, "--version"], log=log, cwd=workspace, timeout=15)
    if (
        code in {130, 143}
        or code != 0
        or _NATIVE_VERSION not in log.read_text().splitlines()
    ):
        return code if code in {130, 143} else 2, log
    probe = workspace / "capability"
    probe.mkdir()
    (probe / "probe.md").write_text(
        "# Graphify capability probe\n\nSynthetic test input.\n"
    )
    log = workspace / "capability.log"
    code = run_command(
        _native_extract(command, probe, workspace / "capability-output"),
        log=log,
        cwd=workspace,
        timeout=15,
        env={
            **_NATIVE_ENV,
            "GRAPHIFY_OPENAI_OAUTH_AUTH_FILE": str(workspace / "no-oauth.json"),
            "GRAPHIFY_CODEX_BASE_URL": "http://127.0.0.1:1",
        },
    )
    if code in {130, 143}:
        return code, log
    expected = (
        "error: backend 'openai-oauth' requires a ChatGPT subscription OAuth login."
    )
    return (0 if code == 1 and expected in log.read_text() else 2), log


def _native_incomplete(log):
    """Recognize the pinned CLI's public partial-extraction diagnostics."""
    done = set()
    totals = set()
    with log.open(encoding="utf-8") as lines:
        for line in lines:
            chunk = re.fullmatch(
                r"\[graphify extract\] chunk (\d+)/(\d+) done\s*", line
            )
            if chunk:
                index, total = map(int, chunk.groups())
                done.add(index)
                totals.add(total)
            elif line.startswith(
                (
                    "[graphify extract] semantic extraction is incomplete:",
                    "[graphify extract] semantic extraction failed:",
                    "[graphify extract] AST extraction incomplete for ",
                    "[graphify extract] warning: could not write manifest:",
                )
            ) or re.match(
                r"\[graphify\] WARNING: \d+/\d+ semantic chunk\(s\) failed", line
            ):
                return True
    return bool(
        totals
        and (
            len(totals) != 1
            or min(done) != 1
            or max(done) != len(done)
            or len(done) != max(totals)
        )
    )


class IncompleteExtraction(ValueError):
    """The candidate cannot prove complete extraction of the frozen inputs."""


def _portable_manifest(path):
    data = read_json(path)
    if not isinstance(data, dict):
        raise TypeError("Graphify manifest must be an object")
    for name in data:
        relative = Path(name)
        if (
            relative == Path(".")
            or relative.is_absolute()
            or ".." in relative.parts
            or PureWindowsPath(name).drive
            or "\\" in name
        ):
            raise ValueError(
                "GRAPHIFY_PORTABLE_BASELINE_REQUIRED: nonportable manifest key"
            )
    return data


def _snapshot(root):
    """Bind the whole actual directory input, not just compile rows."""
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Graphify requires an ordinary file tree")
        if path.is_file():
            name = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
            if name in files:
                raise ValueError(
                    "Graphify input names collide after Unicode normalization"
                )
            with path.open("rb") as stream:
                files[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return files


def _prove_coverage(corpus, snapshot, witnesses, tool_manifest, graph):
    required = {
        unicodedata.normalize("NFC", target.relative_to(corpus).as_posix())
        for target, _ in witnesses
    }
    represented = set()
    for kind in ("nodes", "hyperedges"):
        for item in graph.get(kind, []):
            owner = item.get("source_file")
            if isinstance(owner, str):
                represented.add(unicodedata.normalize("NFC", owner))
    if not required <= represented or not required <= tool_manifest.keys():
        raise IncompleteExtraction(
            "Graphify manifest or graph omitted a selected/retained source"
        )
    for name, entry in tool_manifest.items():
        if name not in snapshot or not isinstance(entry, dict):
            raise IncompleteExtraction(
                f"Graphify manifest has an unbound source: {name}"
            )
        path = corpus / name
        if not path.is_file():
            # Graphify normalizes keys to NFC; the actual filesystem entry need not be NFC.
            path = next(
                p
                for p in corpus.rglob("*")
                if unicodedata.normalize("NFC", p.relative_to(corpus).as_posix())
                == name
            )
        with path.open("rb") as stream:
            # Native manifest uses MD5 as a change stamp, not a trust digest.
            expected = hashlib.file_digest(
                stream, lambda: hashlib.new("md5", usedforsecurity=False)
            ).hexdigest()
        field = "semantic_hash" if name in required else "ast_hash"
        if entry.get(field) != expected:
            raise IncompleteExtraction(
                f"Graphify manifest lacks current byte coverage: {name}"
            )


def _publish_generation(candidate, public, exchange):
    """Switch graph, manifest and native cache in one filesystem operation."""
    with tempfile.TemporaryDirectory(
        prefix=".dogpdkb-graph-", dir=public.parent
    ) as temporary:
        staged = Path(temporary) / "generation"
        shutil.copytree(candidate, staged)
        # Store.lock guards this inode. Keep it stable across the switch.
        os.link(public / ".dogpdkb.lock", staged / ".dogpdkb.lock")
        if exchange(-100, os.fsencode(staged), -100, os.fsencode(public), 2):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), public)


def _previous_graph_input(store, doc_id, document):
    descriptor = dict(document.get("graph_baseline") or {})
    graph = document.get("graph") or {}
    step = store.step(doc_id, "graph") or {}
    if (
        graph.get("status") == "completed"
        and step.get("status") == "completed"
        and version_key(graph.get("version")) == version_key(step.get("version"))
    ):
        descriptor.update(
            path=step.get("input_copy"),
            source_file=graph.get("source_file"),
            version=graph.get("version"),
        )
    return descriptor


def _unowned_deferred_sources(settings, book, store, data):
    """Omit only new deferred sources absent from both native ownership artifacts."""
    candidates = {}
    for row in data["documents"]:
        document = store.data["documents"].get(row["document_id"], {})
        if row["action"] == "defer" and not any(
            document.get(key) for key in ("baseline", "graph_baseline", "graph")
        ):
            candidates[row["document_id"]] = Path(row["path"]).resolve()
    if not candidates:
        return set()
    native = settings.graph_for(book)
    if (
        not (native / "graph.json").is_file()
        or not (native / "manifest.json").is_file()
    ):
        return set()
    manifest = read_json(native / "manifest.json")
    if not isinstance(manifest, dict):
        raise TypeError("Graphify baseline manifest must be an object")
    graph = _graph(native / "graph.json")
    sources = list(manifest)
    for kind in ("nodes", "links", "edges", "hyperedges"):
        records = graph.get(kind, [])
        if not isinstance(records, list):
            raise TypeError("Graphify ownership records must be arrays")
        for record in records:
            if not isinstance(record, dict):
                raise TypeError("Graphify ownership record must be an object")
            if record.get("source_file"):
                sources.append(record["source_file"])

    def identity(value):
        if not isinstance(value, str):
            raise TypeError("Graphify source_file must be a string")
        path = Path(value.replace("\\", "/"))
        if not path.is_absolute():
            path = book.corpus / path
        return unicodedata.normalize("NFC", str(path.resolve()))

    owned = {identity(source) for source in sources}
    return {
        doc_id
        for doc_id, path in candidates.items()
        if identity(str(path)) not in owned
    }


def _retained_corpus(
    settings, book, store, run, data, pending, workspace, omitted, graphify_inputs
):
    """Freeze previous full graph bodies without excluding their native sources."""
    selected = {row["document_id"] for row in pending}
    rows = {row["document_id"]: row for row in data["documents"]}
    retained = []
    omitted_paths = []
    for doc_id, document in store.data["documents"].items():
        if doc_id in selected:
            continue
        current = document.get("corpus", {}).get("path")
        if not current:
            continue
        current = Path(current).resolve()
        if not current.is_absolute() or not current.resolve().is_relative_to(
            book.corpus
        ):
            continue
        if doc_id in omitted:
            omitted_paths.append(current.relative_to(book.corpus))
            continue
        descriptor = _previous_graph_input(store, doc_id, document)
        witness, source_file = descriptor.get("path"), descriptor.get("source_file")
        required = rows.get(doc_id, {}).get("action") in {
            "retain",
            "defer",
            "reuse",
            "metadata",
        }
        if not witness or not source_file or not Path(witness).is_file():
            if required or descriptor:
                run.failure(
                    document_id=doc_id,
                    step="graph",
                    reason="GRAPHIFY_RETAINED_INPUT_REQUIRED",
                )
                return None, []
            continue
        relative = Path(source_file)
        if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
            raise ValueError("retained graph source must have a relative corpus path")
        if graphify_inputs == "text" and relative.suffix.lower() != ".md":
            raise ValueError("text Graphify inputs require Markdown retained sources")
        retained.append((current.relative_to(book.corpus), relative, Path(witness)))
    native_manifest_path = settings.graph_for(book) / "manifest.json"
    native_manifest = (
        read_json(native_manifest_path) if native_manifest_path.is_file() else {}
    )
    if any(Path(key).is_absolute() for key in native_manifest):
        run.failure(step="graph", reason="GRAPHIFY_PORTABLE_BASELINE_REQUIRED")
        return None, []

    # Newer Graphify anchors manifests to --out's parent when the scan root
    # lives beneath it. A sibling keeps the original corpus-relative keys.
    mirror = workspace.with_name(workspace.name + "-corpus")
    excluded = [settings.graph_for(book).resolve(), settings.staging.resolve()]

    def ignore(folder, names):
        ignored = {
            name
            for name in names
            if name == ".dogpdkb.lock"
            or name.startswith(".dogpdkb-graph-")
            or (
                graphify_inputs == "text"
                and not (Path(folder) / name).is_dir()
                and Path(name).suffix.lower() != ".md"
                and name not in {".gitignore", ".graphifyignore"}
            )
            or any(
                (Path(folder) / name).resolve().is_relative_to(path)
                for path in excluded
            )
        }
        if any((Path(folder) / name).is_symlink() for name in set(names) - ignored):
            raise ValueError(
                "graph corpus mirror requires ordinary files and directories"
            )
        return ignored

    shutil.copytree(book.corpus, mirror, ignore=ignore, symlinks=True)
    for relative in omitted_paths:
        (mirror / relative).unlink()
    selected_paths = {
        Path(row["path"]).resolve().relative_to(book.corpus) for row in pending
    }
    destinations = set()
    witnesses = []
    for current, relative, _witness in retained:
        if current != relative:
            (mirror / current).unlink(missing_ok=True)
    for current, relative, witness in retained:
        if relative in selected_paths or relative in destinations:
            raise ValueError("retained graph source collides with another source")
        destinations.add(relative)
        target = mirror / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(witness, target)
        witnesses.append((target, witness))
    for row in pending:
        witnesses.append(
            (
                mirror / Path(row["path"]).resolve().relative_to(book.corpus),
                Path(row["input_copy"]),
            )
        )
    run.data["artifacts"]["graph_corpus"] = str(mirror)
    return mirror, witnesses


def run_graph(
    settings,
    book,
    store,
    run,
    manifest=None,
    *,
    retry_graph=False,
    graphify_inputs="text",
) -> int:
    """Run native directory extraction; publish only a complete batch."""
    if graphify_inputs not in {"all", "text"}:
        raise ValueError("graphify inputs must be 'all' or 'text'")
    manifest = manifest or run.data["artifacts"].get("manifest")
    if not manifest:
        raise ValueError("a complete-source compile manifest is required")
    data = _manifest(settings, book, store, run, manifest)
    if graphify_inputs == "text" and any(
        Path(row["path"]).suffix.lower() != ".md" for row in data["documents"]
    ):
        raise ValueError("text Graphify inputs require Markdown (.md) sources")
    partial = bool(data.get("failures")) or any(
        row["action"] == "defer" for row in data["documents"]
    )
    pending = []
    for row in data["documents"]:
        if row["action"] != "compile":
            continue
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
        return 2 if partial else 0
    selected = {row["document_id"] for row in pending}
    manifest_ids = {row["document_id"] for row in data["documents"]}
    omitted = _unowned_deferred_sources(settings, book, store, data)
    # A directory scan could otherwise silently retry a blocked source from an
    # earlier batch, even when the new plan selected different documents.
    for doc_id, document in store.data["documents"].items():
        version = document.get("corpus", {}).get("version")
        retained_input = False
        if doc_id not in selected:
            previous_input = _previous_graph_input(store, doc_id, document)
            witness = previous_input.get("path")
            if (
                witness
                and previous_input.get("source_file")
                and Path(witness).is_file()
            ):
                version = previous_input.get("version")
                retained_input = True
        if version and store.suppressed(doc_id, "graph", version):
            previous = store.step(doc_id, "graph") or {}
            # Replanning can retain or omit an own-run source that used to be
            # selected. Authorize only its validated mirror representation;
            # leave its failed record intact because it is not acknowledged.
            if (
                retry_graph
                and (
                    doc_id in selected
                    or (
                        doc_id in manifest_ids and (retained_input or doc_id in omitted)
                    )
                )
                and previous.get("run_id") == run.id
                and previous.get("status") in {"running", "failed", "unknown"}
                and version_key(previous["version"]) == version_key(version)
            ):
                continue
            run.failure(
                document_id=doc_id,
                version=version,
                step="graph",
                reason="SAME_VERSION_NOT_RETRIED",
            )
            return 2
    graph_dir = settings.graph_for(book)
    graph_path = graph_dir / "graph.json"
    native_manifest = graph_dir / "manifest.json"
    if graph_path.exists() != native_manifest.is_file():
        run.failure(step="graph", reason="GRAPHIFY_BASELINE_MANIFEST_REQUIRED")
        return 1
    if graph_path.exists():
        _graph(graph_path)
        _portable_manifest(native_manifest)
    if not graph_path.exists():
        selected = {Path(row["path"]).resolve() for row in pending}
        if any(
            path.resolve() not in selected
            for path in book.corpus.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() == ".md"
            and not path.is_relative_to(graph_dir)
            and not path.is_relative_to(settings.staging)
            and not any(
                part.startswith(".dogpdkb-graph-")
                for part in path.relative_to(book.corpus).parts
            )
        ):
            run.failure(step="graph", reason="GRAPHIFY_BASELINE_REQUIRED")
            return 1
    exchange = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if exchange is None:
        run.failure(step="graph", reason="GRAPHIFY_ATOMIC_PUBLICATION_UNAVAILABLE")
        return 2

    run.stage("graphify", total=len(pending), completed=0, failed=0, phase="prepare")
    workspace = Path(tempfile.mkdtemp(prefix="graphify-", dir=run.directory))
    command = settings.command("graphify")
    preflight, preflight_log = _native_preflight(command, workspace)
    if preflight:
        run.failure(
            step="graph",
            reason=(
                "GRAPHIFY_PREFLIGHT_INTERRUPTED"
                if preflight in {130, 143}
                else "GRAPHIFY_NATIVE_PROOF_UNSUPPORTED"
            ),
            report=str(preflight_log),
        )
        return preflight
    candidate = workspace / "graphify-out"
    _snapshot(graph_dir)
    shutil.copytree(
        graph_dir, candidate, ignore=shutil.ignore_patterns(".dogpdkb.lock")
    )
    corpus, witnesses = _retained_corpus(
        settings, book, store, run, data, pending, workspace, omitted, graphify_inputs
    )
    if corpus is None:
        return 2
    snapshot = _snapshot(corpus)
    report = workspace / "result.json"
    run.data["artifacts"].update(
        graph_report=str(report), graph_candidate=str(candidate)
    )
    store.complete_batch(
        {
            "doc_id": row["document_id"],
            "name": "graph",
            "version": row["version"],
            "status": "running",
            "run_id": run.id,
            "input": row["path"],
            "report": str(report),
        }
        for row in pending
    )
    run.stage("graphify", total=len(pending), completed=0, failed=0, phase="tools")
    argv = _native_extract(command, corpus, workspace)
    log = workspace / "extract.log"
    code = run_command(
        argv,
        log=log,
        env=_NATIVE_ENV,
    )
    status, reason = "failed", None
    if code in {124, 130, 143}:
        status, reason = "unknown", "Graphify interrupted before a known result"
    elif code:
        reason = f"Graphify directory extraction exited {code}"
    elif _native_incomplete(log):
        reason = "GRAPHIFY_INCOMPLETE_EXTRACTION: see native extract log"
    else:
        try:
            graph = _graph(candidate / "graph.json")
            tool_manifest = _portable_manifest(candidate / "manifest.json")
            # Extraction may take hours. Do not publish if an input changed.
            _manifest(settings, book, store, run, data)
            if _snapshot(corpus) != snapshot or any(
                target.read_bytes() != witness.read_bytes()
                for target, witness in witnesses
            ):
                raise ValueError("graph corpus mirror changed during extraction")
            _prove_coverage(corpus, snapshot, witnesses, tool_manifest, graph)
            _snapshot(candidate)
            _publish_generation(candidate, graph_dir, exchange)
            status = "completed"
        except IncompleteExtraction as error:
            reason = f"GRAPHIFY_INCOMPLETE_EXTRACTION: {error}"
        except (OSError, TypeError, ValueError) as error:
            status, reason = "unknown", str(error)
    atomic_json(
        report,
        {
            "schema": "dogpdkb-graph-result-1",
            "status": status,
            "exit_code": code,
            "argv": diagnostic_argv(argv),
            "graph": str(graph_path),
            "candidate": str(candidate),
            "native_version": _NATIVE_VERSION,
            "native_log": str(log),
            "input_sha256": snapshot,
            "corpus": str(corpus),
            "graphify_inputs": graphify_inputs,
            "omitted_deferred_sources": sorted(omitted),
            "reason": reason,
        },
    )
    run.stage(
        "graphify", total=len(pending), completed=0, failed=0, phase="record-results"
    )
    outcomes = []
    for row in pending:
        doc_id = row["document_id"]
        acknowledgement = {}
        retained_report = report
        if status == "completed":
            retained_report = store.input_directory(doc_id) / "graph-report.json"
            atomic_json(retained_report, {**read_json(report), "candidate": None})
            witness = store.input_directory(doc_id) / "graph.md"
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
                "report": str(retained_report),
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
                "report": str(retained_report),
                "reason": reason,
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
                log=str(log),
                exit_code=code,
                argv=diagnostic_argv(argv),
            )
    store.complete_batch(outcomes)
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
    parser.add_argument("--graphify-inputs", choices=["all", "text"], default="text")
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
                manifest,
                retry_graph=args.retry_graph,
                graphify_inputs=args.graphify_inputs,
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
