"""Incrementally update graphify artifacts for one published sync manifest."""

import argparse
import fcntl
import json
import os
import shutil
import sys
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any

if __package__:
    from .graph_sync import doc_id, merge_graph_records, validate_extracted_records
else:
    # Also support an isolated (-I) external graph interpreter.
    sys.path.append(str(Path(__file__).resolve().parent))
    from graph_sync import doc_id, merge_graph_records, validate_extracted_records

sys.path.append(str(Path(__file__).resolve().parents[2]))

from dogpdkb.instance_lock import locked_main
from dogpdkb.sync_progress import Progress, progress_for_config
from dogpdkb.sync_time import UTC8
from mdocr.config import cli_path, config_arguments, configure, configured_book


def _usage_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"invalid graph JSON file: {path}") from error


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def sync_scope(
    manifest: dict[str, Any], corpus: Path, staging: Path
) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    exported = manifest.get("exported") or []
    current_files = [row["file"] for row in exported]
    if len(set(current_files)) != len(current_files):
        raise ValueError("sync manifest contains duplicate output files")
    for name in current_files:
        if not (corpus / name).is_file():
            raise FileNotFoundError(corpus / name)
    exported_ids = {str(row["id"]) for row in exported}
    old_files = set()
    backup = staging / "backup"
    if backup.is_dir():
        for path in backup.glob("*.md"):
            if doc_id(path.name) in exported_ids:
                old_files.add(path.name)
    deleted_files = [row["local_file"] for row in manifest.get("deleted") or []]
    source_urls = {
        row["file"]: row["url"]
        for row in exported
        if row.get("url") and row.get("file")
    }
    return current_files, sorted(old_files), deleted_files, source_urls


def extract_semantic(
    corpus: Path, graph_dir: Path, staging: Path, files: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    # Graphify belongs to the configured graph interpreter, not the platform wheel.
    check_semantic_cache = import_module("graphify.cache").check_semantic_cache
    llm = import_module("graphify.llm")
    _default_model_for_backend = llm._default_model_for_backend
    _extraction_system = llm._extraction_system
    detect_backend = llm.detect_backend
    extract_corpus_parallel = llm.extract_corpus_parallel

    paths = [str(corpus / name) for name in files]
    prompt = _extraction_system(deep=False)
    cache_root = graph_dir.parent
    _, _, _, uncached = check_semantic_cache(
        paths, root=corpus, cache_root=cache_root, prompt=prompt
    )
    fresh = {"nodes": [], "edges": [], "hyperedges": []}
    tokens = {"input_tokens": 0, "output_tokens": 0}
    if uncached:
        backend = detect_backend()
        if not backend:
            raise RuntimeError(
                "no Graphify LLM backend configured; source ~/.bashrc first"
            )
        model = _default_model_for_backend(backend)
        print(
            f"Graphify semantic extraction: {len(uncached)} files, "
            f"backend={backend}, model={model}",
            flush=True,
        )

        def on_chunk_done(index: int, total: int, result: dict[str, Any]) -> None:
            write_json(staging / f"chunk-{index:03}.json", result)
            print(f"  graph chunk {index + 1}/{total} complete", flush=True)

        fresh = extract_corpus_parallel(
            [Path(path) for path in uncached],
            backend=backend,
            root=corpus,
            cache_root=cache_root,
            token_budget=50_000,
            max_concurrency=2,
            on_chunk_done=on_chunk_done,
        )
        tokens = {
            "input_tokens": _usage_count(fresh.get("input_tokens")),
            "output_tokens": _usage_count(fresh.get("output_tokens")),
        }
        if (
            fresh.get("failed_chunks")
            or fresh.get("uncovered_files")
            or fresh.get("_partial_files")
        ):
            write_json(staging / "extraction-failed.json", fresh)
            raise RuntimeError("Graphify extraction is incomplete; resume to retry")
    nodes, edges, hyperedges, missing = check_semantic_cache(
        paths, root=corpus, cache_root=cache_root, prompt=prompt
    )
    if missing:
        raise RuntimeError(f"semantic cache is still missing {len(missing)} files")
    combined = {"nodes": nodes, "edges": edges, "hyperedges": hyperedges}
    external_calls = _usage_count(fresh.get("external_calls")) if uncached else 0
    token_values = list(tokens.values())
    usage = {
        "requested_documents": len(paths),
        "cache_hits": len(paths) - len(uncached),
        "external_extractions": len(uncached),
        "external_calls": external_calls,
        "external_calls_evidence": (
            "graphify_result"
            if uncached and external_calls is not None
            else "not_applicable"
            if not uncached
            else "unknown"
        ),
        "tokens": tokens,
        "token_evidence": (
            "not_applicable"
            if not uncached
            else "graphify_result"
            if all(value is not None for value in token_values)
            else "partial_graphify_result"
            if any(value is not None for value in token_values)
            else "unknown"
        ),
    }
    write_json(staging / "extraction.json", combined)
    write_json(staging / "tokens.json", tokens)
    write_json(staging / "extraction-usage.json", usage)
    return combined, usage


def analysis_artifacts(
    candidate: dict[str, Any],
    labels: dict[str, str],
    corpus: Path,
    tokens: dict[str, Any],
) -> dict[str, Any]:
    import networkx as nx

    analyze = import_module("graphify.analyze")
    cluster = import_module("graphify.cluster")
    god_nodes = analyze.god_nodes
    suggest_questions = analyze.suggest_questions
    surprising_connections = analyze.surprising_connections
    community_member_sigs = cluster.community_member_sigs
    score_all = cluster.score_all
    generate = import_module("graphify.report").generate

    graph = nx.DiGraph() if candidate.get("directed") else nx.Graph()
    for node in candidate["nodes"]:
        graph.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    for edge in candidate["links"]:
        graph.add_edge(
            edge["source"],
            edge["target"],
            **{k: v for k, v in edge.items() if k not in {"source", "target"}},
        )
    communities = defaultdict(list)
    for node in candidate["nodes"]:
        if node.get("community") is not None:
            communities[int(node["community"])].append(node["id"])
    cohesion = score_all(graph, communities)
    gods = god_nodes(graph)
    surprises = surprising_connections(graph, communities)
    questions = suggest_questions(graph, communities, labels)
    analysis = {
        "communities": communities,
        "cohesion": cohesion,
        "gods": gods,
        "surprises": surprises,
        "questions": questions,
    }
    documents = list(corpus.glob("*.md"))
    total_words = sum(
        len(path.read_text(encoding="utf-8").split()) for path in documents
    )
    stats = {
        "total_files": len(documents),
        "total_words": total_words,
        "files": {"document": [str(path) for path in documents]},
    }
    report = generate(
        graph,
        communities,
        cohesion,
        labels,
        gods,
        surprises,
        stats,
        tokens,
        str(corpus),
        suggested_questions=questions,
        built_at_commit=candidate.get("built_at_commit"),
    )
    return {
        ".graphify_analysis.json": analysis,
        ".graphify_labels.json.sig": community_member_sigs(communities),
        "GRAPH_REPORT.md": report,
    }


def assert_deletions_absent(corpus: Path, deleted_files: list[str]) -> None:
    deleted_ids = {doc_id(name) for name in deleted_files} - {None}
    if not deleted_ids:
        return
    restored = sorted(
        path.name
        for path in corpus.glob("*.md")
        if path.is_file() and doc_id(path.name) in deleted_ids
    )
    if restored:
        raise RuntimeError(
            f"deleted documents restored in corpus: {restored}; "
            "refresh the sync plan before applying these deletions"
        )


def build_candidate(
    corpus: Path,
    graph_dir: Path,
    staging: Path,
    current_files: list[str],
    old_files: list[str],
    deleted_files: list[str],
    source_urls: dict[str, str],
    *,
    base_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    assert_deletions_absent(corpus, deleted_files)
    old = load_json(base_dir / "graph.json")
    # A rebase can introduce another filename for a document in this delta.
    # Derive ownership from the pinned baseline, not only the corpus backup.
    affected_ids = {
        identity
        for name in [*current_files, *old_files, *deleted_files]
        if (identity := doc_id(name)) is not None
    }
    baseline_sources = {
        Path(str(record.get("source_file") or "")).name
        for kind in ("nodes", "links", "hyperedges")
        for record in old.get(kind, [])
    }
    baseline_manifest = base_dir / "manifest.json"
    if baseline_manifest.exists():
        baseline_sources.update(
            Path(name).name for name in load_json(baseline_manifest)
        )
    old_files = sorted(
        set(old_files)
        | {
            name
            for name in baseline_sources
            if doc_id(name) in affected_ids and name not in current_files
        }
    )
    labels_path = base_dir / ".graphify_labels.json"
    labels = load_json(labels_path) if labels_path.exists() else {}
    # Empty tracked documents have no concepts to extract. Treat their former
    # graph records as removed, while retaining the empty file in the manifest.
    empty_files = {
        name
        for name in current_files
        if not (corpus / name).read_text(encoding="utf-8").strip()
    }
    semantic_files = [name for name in current_files if name not in empty_files]
    if semantic_files:
        fresh, tokens = extract_semantic(corpus, graph_dir, staging, semantic_files)
    else:
        fresh = {"nodes": [], "edges": [], "hyperedges": []}
        tokens = {
            "requested_documents": 0,
            "cache_hits": 0,
            "external_extractions": 0,
            "external_calls": 0,
            "external_calls_evidence": "not_applicable",
            "tokens": {"input_tokens": 0, "output_tokens": 0},
            "token_evidence": "not_applicable",
        }
    if "tokens" not in tokens:
        # Preserve the existing extraction boundary for older Graphify adapters.
        tokens = {
            "requested_documents": len(semantic_files),
            "cache_hits": None,
            "external_extractions": None,
            "external_calls": None,
            "external_calls_evidence": "unknown",
            "tokens": tokens,
            "token_evidence": "legacy_adapter",
        }
    validate_extracted_records(fresh, old)
    candidate, labels, audit = merge_graph_records(
        old,
        fresh,
        current_files=semantic_files,
        old_files=old_files,
        deleted_files=[*deleted_files, *sorted(empty_files)],
        source_urls=source_urls,
        captured_at=datetime.now(UTC8).isoformat(),
        labels=labels,
    )
    candidate_dir = staging / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    write_json(candidate_dir / "graph.json", candidate)
    write_json(candidate_dir / ".graphify_labels.json", labels)

    save_manifest = import_module("graphify.detect").save_manifest

    manifest_path = candidate_dir / "manifest.json"
    if (base_dir / "manifest.json").exists():
        shutil.copy2(base_dir / "manifest.json", manifest_path)
    else:
        write_json(manifest_path, {})
    save_manifest(
        {"document": [str(corpus / name) for name in current_files]},
        manifest_path=str(manifest_path),
        kind="semantic",
        root=corpus,
    )
    manifest = load_json(manifest_path)
    for name in (set(old_files) | set(deleted_files)) - set(current_files):
        manifest.pop(name, None)
        manifest.pop(str((corpus / name).resolve()), None)
    write_json(manifest_path, manifest)
    for name, payload in analysis_artifacts(
        candidate, labels, corpus, tokens["tokens"]
    ).items():
        if name.endswith((".json", ".sig")):
            write_json(candidate_dir / name, payload)
        else:
            (candidate_dir / name).write_text(str(payload), encoding="utf-8")
    audit["tokens"] = tokens["tokens"]
    audit["usage"] = tokens
    audit["status"] = "candidate_validated"
    write_json(staging / "candidate-validation.json", audit)
    return candidate_dir, audit


@contextmanager
def publication_lock(graph_dir: Path):
    """Serialize publishers and baseline capture without creating graph artifacts."""
    descriptor = os.open(graph_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def prepare_base(graph_dir: Path, staging: Path) -> Path:
    with publication_lock(graph_dir):
        return _prepare_base(graph_dir, staging)


def _prepare_base(graph_dir: Path, staging: Path) -> Path:
    """Capture the build input once; compare bytes instead of adding hashes."""
    backup = staging / "graph-backup"
    marker = staging / "base-state.json"
    if marker.exists():
        state = load_json(marker)
        if state["graph_dir"] != str(graph_dir) or any(
            not (backup / name).is_file() for name in state["files"]
        ):
            raise RuntimeError("invalid graph baseline; use a new staging directory")
        return backup
    if backup.exists() or (staging / "candidate-ready.json").exists():
        raise RuntimeError(
            "staging has no pinned graph baseline; use a new staging directory"
        )
    backup.mkdir()
    files = sorted(path.name for path in graph_dir.iterdir() if path.is_file())
    for name in files:
        atomic_copy(graph_dir / name, backup / name)
    if files != sorted(
        path.name for path in graph_dir.iterdir() if path.is_file()
    ) or any(
        not (graph_dir / name).is_file()
        or (graph_dir / name).read_bytes() != (backup / name).read_bytes()
        for name in files
    ):
        raise RuntimeError(
            "graph artifact changed concurrently while capturing baseline"
        )
    write_json(marker, {"graph_dir": str(graph_dir), "files": files})
    return backup


def publish_candidate(candidate: Path, graph_dir: Path, staging: Path) -> None:
    with publication_lock(graph_dir):
        _publish_candidate(candidate, graph_dir, staging)


def _publish_candidate(candidate: Path, graph_dir: Path, staging: Path) -> None:
    files = [path for path in candidate.iterdir() if path.is_file()]
    if files and all(
        (graph_dir / source.name).is_file()
        and (graph_dir / source.name).read_bytes() == source.read_bytes()
        for source in files
    ):
        return
    backup = staging / "graph-backup"
    base_files = set(load_json(staging / "base-state.json")["files"])
    for source in files:
        live = graph_dir / source.name
        archived = backup / source.name
        if live.exists() and live.read_bytes() == source.read_bytes():
            continue
        if (
            live.exists()
            and archived.exists()
            and live.read_bytes() == archived.read_bytes()
        ):
            continue
        if not live.exists() and source.name not in base_files:
            continue
        raise RuntimeError(
            f"graph artifact changed concurrently: {live}; "
            "retry the retained staging directory with --rebase "
            "(pipeline: --resume --rebase-graph)"
        )
    written = []
    try:
        for source in files:
            atomic_copy(source, graph_dir / source.name)
            written.append(source.name)
        live_graph = load_json(graph_dir / "graph.json")
        if live_graph != load_json(candidate / "graph.json"):
            raise RuntimeError("published graph does not match candidate")
    except BaseException:
        for name in reversed(written):
            archived = backup / name
            if archived.exists():
                atomic_copy(archived, graph_dir / name)
            else:
                (graph_dir / name).unlink(missing_ok=True)
        raise


@locked_main
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    config_arguments(parser, book=True)
    parser.add_argument("-i", "--input", type=cli_path)
    parser.add_argument("--manifest", type=cli_path, required=True)
    parser.add_argument("--staging", type=cli_path, required=True)
    parser.add_argument("--graph-dir", type=cli_path)
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument(
        "--candidate-only",
        action="store_true",
        help="build and retain an isolated candidate without publishing it",
    )
    phase.add_argument(
        "--publish-validated",
        action="store_true",
        help="publish only a previously retained candidate",
    )
    parser.add_argument(
        "--rebase",
        action="store_true",
        help="rebuild the staged candidate against the current graph baseline",
    )
    args = parser.parse_args()
    config = configure(parser, args.config)
    source = (
        configured_book(parser, config, args)
        if args.input is None or args.book
        else None
    )
    if source is None:
        assert args.input is not None
        corpus = args.input.resolve()
    else:
        corpus = (args.input or source.corpus).resolve()
    graph_dir = (
        args.graph_dir or config.graph_dir or corpus / "graphify-out"
    ).resolve()
    staging = args.staging.resolve()
    staging.mkdir(parents=True, exist_ok=True)
    tracker = progress_for_config(config, source, corpus)
    if tracker is None and not (args.candidate_only or args.publish_validated):
        tracker = Progress(corpus, "adhoc")
    if tracker:
        tracker.claim(staging, phase="graph")
        tracker.phase("graph", "running")
    try:
        manifest = load_json(args.manifest)
        scope = sync_scope(manifest, corpus, args.manifest.parent)
        current_files, old_files, deleted_files, source_urls = scope
        if not current_files and not deleted_files:
            result = {"status": "skipped", "reason": "no published corpus changes"}
            write_json(staging / "result.json", result)
            if tracker:
                tracker.phase("graph", "completed", result=result)
                if not os.environ.get("DOGPDKB_RUN_STAGING"):
                    tracker.finish()
            print(json.dumps(result))
            return
        if not (graph_dir / "graph.json").is_file():
            raise SystemExit(f"existing graph not found: {graph_dir / 'graph.json'}")
        if args.publish_validated and not (staging / "candidate-ready.json").is_file():
            raise SystemExit(
                "validated candidate is missing; build it before publishing"
            )
        if args.rebase:
            with publication_lock(graph_dir):
                for name in ("graph-backup", "candidate"):
                    if (staging / name).exists():
                        try:
                            shutil.rmtree(staging / name)
                        except OSError as error:
                            raise RuntimeError(
                                f"cannot reset staged graph directory: {name}"
                            ) from error
                for name in (
                    "base-state.json",
                    "candidate-ready.json",
                    "candidate-validation.json",
                    "result.json",
                ):
                    (staging / name).unlink(missing_ok=True)
                base_dir = _prepare_base(graph_dir, staging)
        else:
            base_dir = prepare_base(graph_dir, staging)
        # Freeze validated artifacts before publication, so a crash after publishing
        # does not rebuild them with different timestamps or extraction token counts.
        inputs = {
            "corpus": str(corpus),
            "graph_dir": str(graph_dir),
            "manifest": manifest,
            "scope": [current_files, old_files, deleted_files, source_urls],
            "config": config.snapshot(),
            "sources": {
                name: {
                    "size": (corpus / name).stat().st_size,
                    "mtime_ns": (corpus / name).stat().st_mtime_ns,
                }
                for name in current_files
            },
        }
        if tracker:
            for name in current_files:
                snapshot = staging / "source-snapshots" / name
                source_path = corpus / name
                if snapshot.exists():
                    if snapshot.read_bytes() != source_path.read_bytes():
                        raise RuntimeError(
                            "candidate inputs changed; use a new staging directory"
                        )
                else:
                    snapshot.parent.mkdir(parents=True, exist_ok=True)
                    atomic_copy(source_path, snapshot)
        ready_path = staging / "candidate-ready.json"
        candidate = staging / "candidate"
        if ready_path.exists():
            ready = load_json(ready_path)
            if ready["inputs"] != inputs:
                raise RuntimeError(
                    "candidate inputs changed; use a new staging directory"
                )
            if not ready["files"] or any(
                not (candidate / name).is_file() for name in ready["files"]
            ):
                raise RuntimeError(
                    "validated candidate files missing; use a new staging directory"
                )
            audit = ready["audit"]
        else:
            if args.publish_validated:
                raise RuntimeError("validated candidate is missing")
            candidate, audit = build_candidate(
                corpus,
                graph_dir,
                staging,
                current_files,
                old_files,
                deleted_files,
                source_urls,
                base_dir=base_dir,
            )
            write_json(
                ready_path,
                {
                    "inputs": inputs,
                    "audit": audit,
                    "files": sorted(
                        path.name for path in candidate.iterdir() if path.is_file()
                    ),
                },
            )
        if tracker:
            tracker.phase("graph_candidate", "completed", candidate=str(candidate))
            for row in manifest.get("exported", []):
                tracker.step(
                    str(row["id"]),
                    "graph_candidate",
                    "completed",
                    candidate=str(candidate),
                    file=row["file"],
                )
        if args.candidate_only:
            result = {
                **audit,
                "status": "candidate_ready",
                "candidate": str(candidate),
                "backup": str(staging / "graph-backup"),
                "full_rebuild": False,
            }
            write_json(staging / "candidate-result.json", result)
            print(json.dumps(result, ensure_ascii=False))
            return
        assert_deletions_absent(corpus, deleted_files)
        publish_candidate(candidate, graph_dir, staging)
        result = {
            **audit,
            "status": "completed",
            "graph": str(graph_dir / "graph.json"),
            "candidate": str(candidate),
            "backup": str(staging / "graph-backup"),
            "full_rebuild": False,
        }
        write_json(staging / "result.json", result)
        if tracker:
            for row in manifest.get("exported", []):
                identity = str(row["id"])
                version = tracker.version(row.get("content_updated_at"))
                tracker.step(
                    identity, "graph", "completed", version=version, file=row["file"]
                )
                pending = tracker.book["documents"][identity].get("pending", [])
                tracker.update(
                    identity,
                    graph={
                        "status": "covered",
                        "coverage": "published_source",
                        "version": version,
                        "raw": tracker.reference(
                            staging / "source-snapshots" / row["file"],
                            "historical_cleaned",
                        ),
                        "content_kind": "historical_enriched",
                        "metadata": {
                            "name": row.get("title", row["file"]),
                            "logical_path": "",
                            "source_ref": f"{tracker.key}:{identity}",
                        },
                    },
                    pending=[
                        reason
                        for reason in pending
                        if reason != "GRAPH_UPDATE_REQUIRED"
                    ],
                )
            for row in manifest.get("deleted", []):
                identity = str(row["id"])
                tracker.step(identity, "graph", "completed", deleted=True)
                tracker.update(identity, graph={"status": "deleted", "version": None})
            tracker.phase("graph", "completed", result=str(staging / "result.json"))
            if not os.environ.get("DOGPDKB_RUN_STAGING"):
                tracker.finish()
        print(json.dumps(result, ensure_ascii=False))

    except BaseException as error:
        if tracker:
            tracker.phase("graph", "failed", reason=type(error).__name__)
        raise


if __name__ == "__main__":
    main()
