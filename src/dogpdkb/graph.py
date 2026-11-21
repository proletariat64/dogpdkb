"""Compile full sources through Graphify's public CLI and result artifacts."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path, PureWindowsPath

from dogpdkb.help import HelpParser
from dogpdkb.phases import check_graph_manifest, graph_generation
from dogpdkb.planning import reject_rollback
from dogpdkb.processes import diagnostic_argv, run_command
from dogpdkb.records import Run, Store, atomic_json, read_json, version_key
from dogpdkb.settings import document_identity, load_config, select_book


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


def _graph(path):
    data = read_json(path)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("nodes"), list)
        or not isinstance(data.get("links", data.get("edges")), list)
        or not all(
            isinstance(data.get(kind, []), list)
            and all(isinstance(record, dict) for record in data.get(kind, []))
            for kind in ("nodes", "edges", "links", "hyperedges")
        )
    ):
        raise TypeError("Graphify did not produce a graph artifact")
    return data


_NATIVE_VERSION = "graphify 0.9.111"
_NATIVE_ENV = {
    "GRAPHIFY_OUT": "graphify-out",
    "GRAPHIFY_FORCE": "0",
    "GRAPHIFY_OPENAI_OAUTH_PARALLEL": "1",
}


def _native_extract(command, corpus, workspace, token_budget, model, concurrency):
    argv = [
        *command,
        "extract",
        str(corpus),
        "--out",
        str(workspace),
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


def _native_preflight(command, workspace, token_budget, model, concurrency):
    """Check the attested CLI protocol without reading live credentials or documents."""
    isolated = {
        **_NATIVE_ENV,
        "HOME": str(workspace),
        "GRAPHIFY_OPENAI_OAUTH_AUTH_FILE": str(workspace / "no-oauth.json"),
        "GRAPHIFY_CODEX_BASE_URL": "http://127.0.0.1:1",
    }
    log = workspace / "version.log"
    code = run_command(
        [*command, "--version"], log=log, cwd=workspace, timeout=15, env=isolated
    )
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
        _native_extract(
            command,
            probe,
            workspace / "capability-output",
            token_budget,
            model,
            concurrency,
        ),
        log=log,
        cwd=workspace,
        timeout=15,
        env=isolated,
    )
    if code in {130, 143}:
        return code, log
    expected = (
        "error: backend 'openai-oauth' requires a ChatGPT subscription OAuth login."
    )
    return (0 if code == 1 and expected in log.read_text() else 2), log


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
    return dispatched + (cache[0] if cache else 0)


class IncompleteExtraction(ValueError):
    """The candidate cannot prove complete extraction of the frozen inputs."""


class NonportableManifest(ValueError):
    """A native manifest cannot be relocated without changing identities."""


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
            raise NonportableManifest(f"nonportable Graphify manifest key: {name}")
    return data


def _input_name(relative):
    return unicodedata.normalize("NFC", relative.as_posix())


def _tree_files(root):
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Graphify requires an ordinary file tree")
        if path.is_file():
            name = _input_name(path.relative_to(root))
            if name in files:
                raise ValueError(
                    "Graphify input names collide after Unicode normalization"
                )
            files[name] = path
    return files


def _digest(path, algorithm="sha256"):
    digest = hashlib.new(algorithm, usedforsecurity=algorithm != "md5")
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(root):
    """Bind the whole actual directory input, not just compile rows."""
    return {name: _digest(path) for name, path in _tree_files(root).items()}


def _semantic_owners(graph):
    nodes = [
        n for n in graph.get("nodes", []) if isinstance(n.get("id"), str) and n["id"]
    ]
    ids = {node["id"] for node in nodes}
    hyperedges = [
        edge
        for edge in graph.get("hyperedges", [])
        if isinstance(edge.get("nodes"), list)
        and edge["nodes"]
        and all(isinstance(member, str) and member in ids for member in edge["nodes"])
    ]
    return {
        unicodedata.normalize("NFC", record["source_file"])
        for record in (*nodes, *hyperedges)
        if isinstance(record.get("source_file"), str)
    }


def _prove_coverage(
    corpus,
    witnesses,
    tool_manifest,
    graph,
    baseline_manifest,
    baseline_graph,
    native_work,
):
    required = {
        unicodedata.normalize("NFC", target.relative_to(corpus).as_posix())
        for target, _ in witnesses
    }
    files = _tree_files(corpus)
    required.update(name for name in files if Path(name).suffix.lower() == ".md")
    if not required <= _semantic_owners(graph) or not required <= tool_manifest.keys():
        raise IncompleteExtraction(
            "Graphify manifest or graph omitted frozen Markdown or a selected source"
        )
    baseline_owners = _semantic_owners(baseline_graph)
    changed = 0
    for name, entry in tool_manifest.items():
        if name not in files or not isinstance(entry, dict):
            raise IncompleteExtraction(
                f"Graphify manifest has an unbound source: {name}"
            )
        # Native manifest uses MD5 as a change stamp, not a trust digest.
        expected = _digest(files[name], "md5")
        field = "semantic_hash" if name in required else "ast_hash"
        if entry.get(field) != expected:
            raise IncompleteExtraction(
                f"Graphify manifest lacks current byte coverage: {name}"
            )
        if name in required:
            prior = baseline_manifest.get(name)
            if (
                name not in baseline_owners
                or not isinstance(prior, dict)
                or prior.get("semantic_hash") != expected
            ):
                changed += 1
    for kind in ("nodes", "edges", "links", "hyperedges"):
        for index, record in enumerate(graph.get(kind, [])):
            if "source_file" not in record:
                continue  # Native records may have no declared source.
            source = record["source_file"]
            if (
                not isinstance(source, str)
                or unicodedata.normalize("NFC", source) not in tool_manifest
            ):
                raise IncompleteExtraction(
                    f"Graphify has unbound provenance in {kind}[{index}]"
                )
    if changed > native_work:
        raise IncompleteExtraction(
            "native extraction or cache evidence omitted changed sources"
        )


def _check_historical_inputs(corpus, book, pending, manifest, graph):
    selected = {
        unicodedata.normalize(
            "NFC", Path(row["path"]).resolve().relative_to(book.corpus).as_posix()
        )
        for row in pending
    }
    owners = _semantic_owners(graph)
    for name, path in _tree_files(corpus).items():
        if Path(name).suffix.lower() != ".md" or name in selected:
            continue
        entry = manifest.get(name)
        if (
            not isinstance(entry, dict)
            or name not in owners
            or entry.get("semantic_hash") != _digest(path, "md5")
        ):
            raise IncompleteExtraction(f"unstamped historical graph input: {name}")


def _sync_path(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exchange_dirs(exchange, staged, public):
    # renameat2(AT_FDCWD, staged, AT_FDCWD, public, RENAME_EXCHANGE).
    if exchange(-100, os.fsencode(staged), -100, os.fsencode(public), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), public)


def _publication_capability(public, exchange):
    if exchange is None or public.is_symlink() or os.path.ismount(public):
        raise OSError("graph target cannot be exchanged atomically")
    with tempfile.TemporaryDirectory(
        prefix=".dogpdkb-graph-", dir=public.parent
    ) as temporary:
        first, second = Path(temporary) / "a", Path(temporary) / "b"
        first.mkdir()
        second.mkdir()
        _exchange_dirs(exchange, first, second)


def _publish_generation(candidate, public, exchange):
    """Switch graph, manifest and native cache in one filesystem operation."""
    with tempfile.TemporaryDirectory(
        prefix=".dogpdkb-graph-", dir=public.parent
    ) as temporary:
        staged = Path(temporary) / "generation"
        shutil.copytree(candidate, staged)
        # Store.lock guards this inode. Keep it stable across the switch.
        os.link(public / ".dogpdkb.lock", staged / ".dogpdkb.lock")
        for folder, _, files in os.walk(staged, topdown=False):
            for name in files:
                _sync_path(Path(folder) / name)
            _sync_path(Path(folder))
        _exchange_dirs(exchange, staged, public)
        _sync_path(public.parent)


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


def _unowned_deferred_sources(settings, book, store, data, *, initial_recovery=False):
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
    files = tuple((native / name).is_file() for name in ("graph.json", "manifest.json"))
    if not any(files) and initial_recovery:
        return set(candidates)  # No published generation can own these deferred paths.
    if not all(files):
        return set()
    owned = _native_source_paths(book, native)
    return {
        doc_id
        for doc_id, path in candidates.items()
        if unicodedata.normalize("NFC", str(path)) not in owned
    }


def _native_source_paths(book, native):
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

    return {identity(source) for source in sources}


def _load_graph_scope(path, approved_sha, manifest_bytes, book, run):
    if "graph_retry" in run.data:
        raise ValueError("frozen graph scope requires an unsealed compile handoff")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != approved_sha:
        raise ValueError("frozen graph scope SHA-256 mismatch")
    scope = json.loads(content)
    if (
        not isinstance(scope, dict)
        or scope.get("schema") != "dogpdkb-graph-scope-1"
        or scope.get("book") != book.key
        or scope.get("run_id") != run.id
        or scope.get("compile_sha256") != hashlib.sha256(manifest_bytes).hexdigest()
        or not all(
            isinstance(scope.get(name), dict)
            for name in ("holds", "baseline", "inputs", "semantic_cache", "misses")
        )
        or not scope["holds"]
    ):
        raise ValueError("invalid frozen graph scope or compile binding")
    return {**scope, "approval_sha256": approved_sha}


def _historical_holds(book, store, run, data, native, scope):
    """Prove that omission preserves existing suppression; never authorize a retry."""
    baseline = {
        name: _digest(native / name) for name in ("graph.json", "manifest.json")
    }
    if scope["baseline"] != baseline:
        raise ValueError("frozen graph baseline changed")
    owned = _native_source_paths(book, native)
    selected = {row["document_id"] for row in data["documents"]}
    documents = store.data["documents"]
    corpus_paths = [doc.get("corpus", {}).get("path") for doc in documents.values()]
    for doc_id, hold in scope["holds"].items():
        doc = documents.get(doc_id, {})
        corpus = doc.get("corpus", {})
        source_key, remote_id = doc.get("source_key"), doc.get("source_document_id")
        path = book.corpus / f"source-[{doc_id}].md"
        step = store.step(doc_id, "graph") or {}
        if (
            not isinstance(hold, dict)
            or doc_id in selected
            or not isinstance(source_key, str)
            or not source_key
            or not isinstance(remote_id, str)
            or not remote_id
            or doc_id != document_identity(source_key, remote_id)
            or corpus.get("path") != str(path)
            or corpus_paths.count(str(path)) != 1
            or not path.is_file()
            or path.resolve() != path
            or unicodedata.normalize("NFC", str(path)) in owned
            or any(doc.get(key) for key in ("baseline", "graph_baseline", "graph"))
            or not isinstance(hold.get("run_id"), str)
            or not hold["run_id"]
            or hold["run_id"] == run.id
            or hold["run_id"] != step.get("run_id")
            or step.get("status") not in {"failed", "unknown"}
            or version_key(hold.get("version")) != version_key(corpus.get("version"))
            or version_key(hold["version"]) != version_key(step.get("version"))
        ):
            raise ValueError(f"unproven historical graph hold: {doc_id}")
        block = store.blocked_attempt(doc_id, "graph", corpus["version"])
        if not block or (block.get("run_id"), block.get("status")) != (
            hold["run_id"],
            step["status"],
        ):
            raise ValueError(f"unproven historical graph suppression: {doc_id}")
    return set(scope["holds"])


def _check_frozen_graph_scope(scope, book, pending, snapshot, baseline, candidate):
    selected = {
        _input_name(Path(row["path"]).relative_to(book.corpus)) for row in pending
    }
    markdown = {name for name in snapshot if Path(name).suffix.lower() == ".md"}
    if (
        snapshot != scope["inputs"]
        or not selected
        or selected & baseline.keys()
        or markdown != selected | baseline.keys()
        or {name: snapshot[name] for name in selected} != scope["misses"]
    ):
        raise ValueError("frozen graph mirror or approved misses changed")
    if scope["baseline"] != {
        name: _digest(candidate / name) for name in scope["baseline"]
    }:
        raise ValueError("frozen Graphify candidate baseline changed")
    cache = candidate / "cache/semantic"
    if (
        not cache.is_dir()
        or not scope["semantic_cache"]
        or _snapshot(cache) != scope["semantic_cache"]
    ):
        raise ValueError("frozen Graphify semantic cache changed")


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
    graphify_inputs=None,
    replan=False,
    graph_scope=None,
) -> int:
    """Run native directory extraction; publish only a complete batch."""
    manifest = manifest or run.data["artifacts"].get("manifest")
    if not manifest:
        raise ValueError("a complete-source compile manifest is required")
    if "graph_retry" in run.data:
        frozen = Path(manifest).read_bytes()
        graphify_inputs = check_graph_manifest(
            run, manifest, graphify_inputs, content=frozen
        )
        manifest = json.loads(frozen)
    graphify_inputs = graphify_inputs or "text"
    if graphify_inputs not in {"all", "text"}:
        raise ValueError("graphify inputs must be 'all' or 'text'")
    data = _manifest(settings, book, store, run, manifest)
    if graphify_inputs == "text" and any(
        Path(row["path"]).suffix.lower() != ".md" for row in data["documents"]
    ):
        raise ValueError("text Graphify inputs require Markdown (.md) sources")
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
    binding = run.data.get("graph_retry")
    if (
        retry_graph
        and binding is not None
        and not (replan or binding.get("input_tree_sha256"))
    ):
        raise ValueError(
            "no frozen graph input tree to retry; resume the phase or generate a new plan"
        )
    if (
        retry_graph
        and "graph_generation" in run.data
        and (graph_generation(store, book) != run.data["graph_generation"])
    ):
        run.failure(step="graph", reason="STALE_GRAPH_HANDOFF", send_state="not_sent")
        return 2
    selected = {row["document_id"] for row in pending}
    manifest_ids = {row["document_id"] for row in data["documents"]}
    recovery = run.data.get("command") == "recover-ocr"
    recovered_scope = set()
    if recovery:
        from dogpdkb.ocr_recovery import changed_graph_scope

        recovered_scope = changed_graph_scope(store, run, data)
    omitted = _unowned_deferred_sources(
        settings, book, store, data, initial_recovery=recovery
    )
    graph_dir = settings.graph_for(book)
    historical_holds = (
        _historical_holds(book, store, run, data, graph_dir, graph_scope)
        if graph_scope is not None
        else set()
    )
    # A directory scan could otherwise silently retry a blocked source from an
    # earlier batch, even when the new plan selected different documents.
    for doc_id, document in store.data["documents"].items():
        if doc_id in historical_holds:
            continue
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
            if doc_id in recovered_scope and (
                doc_id in selected or retained_input or doc_id in omitted
            ):
                # Interrupted old bytes were not published. Only changed bodies
                # enter this new request; retained/omitted peers are not replayed.
                continue
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
    graph_path = graph_dir / "graph.json"
    native_manifest = graph_dir / "manifest.json"
    if graph_path.exists() != native_manifest.is_file():
        run.failure(step="graph", reason="GRAPHIFY_BASELINE_MANIFEST_REQUIRED")
        return 1
    baseline_graph, baseline_manifest = {}, {}
    if graph_path.exists():
        baseline_graph = _graph(graph_path)
        try:
            baseline_manifest = _portable_manifest(native_manifest)
        except NonportableManifest as error:
            run.failure(
                step="graph",
                reason="GRAPHIFY_PORTABLE_BASELINE_REQUIRED",
                message=str(error),
            )
            return 2
    else:
        selected = {Path(row["path"]).resolve() for row in pending}
        selected.update(
            Path(row["path"]).resolve()
            for row in data["documents"]
            if row["document_id"] in omitted
        )
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
    try:
        _publication_capability(graph_dir, exchange)
    except OSError as error:
        run.failure(
            step="graph",
            reason="GRAPHIFY_ATOMIC_PUBLICATION_UNAVAILABLE",
            message=str(error),
        )
        return 2

    run.stage("graphify", total=len(pending), completed=0, failed=0, phase="prepare")
    workspace = Path(tempfile.mkdtemp(prefix="graphify-", dir=run.directory))
    public_snapshot = _snapshot(graph_dir)
    corpus, witnesses = _retained_corpus(
        settings,
        book,
        store,
        run,
        data,
        pending,
        workspace,
        omitted | historical_holds,
        graphify_inputs,
    )
    if corpus is None:
        return 2
    try:
        _check_historical_inputs(
            corpus, book, pending, baseline_manifest, baseline_graph
        )
    except IncompleteExtraction as error:
        run.failure(
            step="graph",
            reason="GRAPHIFY_BASELINE_COVERAGE_REQUIRED",
            message=str(error),
        )
        return 2
    snapshot = _snapshot(corpus)
    input_digests = {
        row["document_id"]: snapshot[
            _input_name(Path(row["path"]).relative_to(book.corpus))
        ]
        for row in pending
    }
    if binding is not None:
        scope = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True).encode()
        ).hexdigest()
        if binding.setdefault("input_tree_sha256", scope) != scope:
            raise ValueError("saved graph input tree changed; generate a new plan")
        run.save(durable=True)
    command = settings.command("graphify")
    token_budget = settings.graphify_token_budget
    model = settings.graphify_model
    concurrency = settings.graphify_concurrency
    preflight, preflight_log = _native_preflight(
        command, workspace, token_budget, model, concurrency
    )
    if preflight:
        run.failure(
            step="graph",
            reason=(
                "GRAPHIFY_PREFLIGHT_INTERRUPTED"
                if preflight in {130, 143}
                else "GRAPHIFY_NATIVE_PROOF_UNSUPPORTED"
            ),
            report=str(preflight_log),
            send_state="not_sent",
        )
        return preflight
    candidate = workspace / "graphify-out"
    shutil.copytree(
        graph_dir, candidate, ignore=shutil.ignore_patterns(".dogpdkb.lock")
    )
    if _snapshot(candidate) != {
        name: digest
        for name, digest in public_snapshot.items()
        if name != ".dogpdkb.lock"
    }:
        raise ValueError("public Graphify generation changed while freezing baseline")
    if graph_scope is not None:
        _check_frozen_graph_scope(
            graph_scope, book, pending, _snapshot(corpus), baseline_manifest, candidate
        )
    report = workspace / "result.json"
    run.data["artifacts"].update(
        graph_report=str(report), graph_candidate=str(candidate)
    )
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
            }
            for row in pending
        ),
        durable=True,
    )
    run.stage("graphify", total=len(pending), completed=0, failed=0, phase="tools")
    argv = _native_extract(command, corpus, workspace, token_budget, model, concurrency)
    log = workspace / "extract.log"
    code = run_command(
        argv,
        log=log,
        env=_NATIVE_ENV,
    )
    status, reason = "failed", None
    publication_attempted = False
    if code in {124, 130, 143}:
        status, reason = "unknown", "Graphify interrupted before a known result"
    elif code:
        reason = f"Graphify directory extraction exited {code}"
    else:
        try:
            graph = _graph(candidate / "graph.json")
            native_work = _prove_native_result(log, candidate / "graph.json")
            tool_manifest = _portable_manifest(candidate / "manifest.json")
            # Extraction may take hours. Do not publish if an input changed.
            _manifest(settings, book, store, run, data)
            if _snapshot(corpus) != snapshot or any(
                target.read_bytes() != witness.read_bytes()
                for target, witness in witnesses
            ):
                raise ValueError("graph corpus mirror changed during extraction")
            _prove_coverage(
                corpus,
                witnesses,
                tool_manifest,
                graph,
                baseline_manifest,
                baseline_graph,
                native_work,
            )
            _tree_files(candidate)
            if _snapshot(graph_dir) != public_snapshot:
                raise ValueError("public Graphify generation changed during extraction")
            publication_attempted = True
            _publish_generation(candidate, graph_dir, exchange)
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
        "candidate": str(candidate),
        "native_version": _NATIVE_VERSION,
        "native_log": str(log),
        "input_sha256": snapshot,
        "corpus": str(corpus),
        "graphify_inputs": graphify_inputs,
        "graphify_model": model,
        "graphify_token_budget": token_budget,
        "graphify_concurrency": concurrency,
        "omitted_deferred_sources": sorted(omitted),
        "reason": reason,
    }
    if graph_scope is not None:
        evidence.update(
            graph_scope_sha256=graph_scope["approval_sha256"],
            omitted_historical_sources=sorted(historical_holds),
        )
    atomic_json(report, evidence, durable=True)
    retained_report = report
    if status == "completed":
        evidence_dir = store.path.parent / "graph-evidence" / workspace.name
        evidence_dir.mkdir(parents=True)
        retained_log = evidence_dir / log.name
        shutil.copyfile(log, retained_log)
        _sync_path(retained_log)
        retained_report = evidence_dir / report.name
        atomic_json(
            retained_report,
            {**evidence, "candidate": None, "native_log": str(retained_log)},
            durable=True,
        )
    run.stage(
        "graphify", total=len(pending), completed=0, failed=0, phase="record-results"
    )
    outcomes = []
    for row in pending:
        doc_id = row["document_id"]
        acknowledgement = {}
        if status == "completed":
            witness = (
                retained_report.parent / f"{store.input_directory(doc_id).name}.md"
            )
            source_file = Path(row["path"]).relative_to(book.corpus)
            shutil.copyfile(corpus / source_file, witness)
            _sync_path(witness)
            acknowledgement = {
                "input_copy": str(witness),
                "action": row["action"],
                "metadata": row.get("metadata", {}),
            }
            document = store.document(doc_id)
            document["graph"] = {
                "status": "completed",
                "version": row["version"],
                "source_file": str(source_file),
                "sha256": input_digests[doc_id],
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
                reports=[str(log)],
                log=str(log),
                exit_code=code,
                argv=diagnostic_argv(argv),
                input_sha256=input_digests[doc_id],
            )
    if status == "completed":
        _sync_path(retained_report.parent)
    store.complete_batch(outcomes, durable=True)
    complete = len(pending) if status == "completed" else 0
    run.data["graph_result"] = status
    run.data["graph_publication"] = (
        "published" if complete else "unknown" if publication_attempted else "retained"
    )
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
        "--graph-scope", type=Path, help="approved frozen historical-hold scope"
    )
    parser.add_argument("--scope-sha256", help="approved SHA-256 of --graph-scope")
    parser.add_argument("--graphify-inputs", choices=["all", "text"])
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
        if bool(args.graph_scope) != bool(args.scope_sha256):
            raise ValueError("--graph-scope and --scope-sha256 are required together")
        if args.graph_scope and (args.retry_graph or args.graphify_inputs == "all"):
            raise ValueError("frozen graph scope requires text inputs without retry")
        settings = load_config(args.config)
        book = select_book(settings, args.book)
        store = Store(settings, book)
        with store.lock():
            manifest_bytes = args.input.read_bytes()
            manifest = json.loads(manifest_bytes)
            if not isinstance(manifest, dict) or not isinstance(
                manifest.get("run_id"), str
            ):
                raise TypeError("invalid complete-source manifest")
            run = Run.load(store, manifest.get("run_id", ""))
            scope = (
                _load_graph_scope(
                    args.graph_scope, args.scope_sha256, manifest_bytes, book, run
                )
                if args.graph_scope
                else None
            )
            run.data.update(status="running", pid=os.getpid(), ended_at=None)
            run.save()
            code = run_graph(
                settings,
                book,
                store,
                run,
                manifest if scope is not None else args.input,
                retry_graph=args.retry_graph,
                graph_scope=scope,
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
