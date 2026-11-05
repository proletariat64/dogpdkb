"""Incrementally update graphify artifacts for one published sync manifest."""

import argparse
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graph_sync import doc_id, merge_graph_records

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdocr.config import cli_path, config_arguments, configure, configured_book


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
) -> tuple[dict[str, Any], dict[str, int]]:
    from graphify.cache import check_semantic_cache
    from graphify.llm import (
        _default_model_for_backend,
        _extraction_system,
        detect_backend,
        extract_corpus_parallel,
    )

    paths = [str(corpus / name) for name in files]
    prompt = _extraction_system(deep=False)
    _, _, _, uncached = check_semantic_cache(
        paths, root=corpus, cache_root=graph_dir, prompt=prompt
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
            cache_root=graph_dir,
            token_budget=50_000,
            max_concurrency=2,
            on_chunk_done=on_chunk_done,
        )
        tokens = {
            "input_tokens": fresh.get("input_tokens", 0),
            "output_tokens": fresh.get("output_tokens", 0),
        }
        if (
            fresh.get("failed_chunks")
            or fresh.get("uncovered_files")
            or fresh.get("_partial_files")
        ):
            write_json(staging / "extraction-failed.json", fresh)
            raise RuntimeError("Graphify extraction is incomplete; resume to retry")
    nodes, edges, hyperedges, missing = check_semantic_cache(
        paths, root=corpus, cache_root=graph_dir, prompt=prompt
    )
    if missing:
        raise RuntimeError(f"semantic cache is still missing {len(missing)} files")
    combined = {"nodes": nodes, "edges": edges, "hyperedges": hyperedges}
    write_json(staging / "extraction.json", combined)
    write_json(staging / "tokens.json", tokens)
    return combined, tokens


def analysis_artifacts(
    candidate: dict[str, Any],
    labels: dict[str, str],
    corpus: Path,
    tokens: dict[str, int],
) -> dict[str, Any]:
    import networkx as nx
    from graphify.analyze import god_nodes, suggest_questions, surprising_connections
    from graphify.cluster import community_member_sigs, score_all
    from graphify.report import generate

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


def build_candidate(
    corpus: Path,
    graph_dir: Path,
    staging: Path,
    current_files: list[str],
    old_files: list[str],
    deleted_files: list[str],
    source_urls: dict[str, str],
) -> tuple[Path, dict[str, Any]]:
    old = load_json(graph_dir / "graph.json")
    labels_path = graph_dir / ".graphify_labels.json"
    labels = load_json(labels_path) if labels_path.exists() else {}
    if current_files:
        fresh, tokens = extract_semantic(corpus, graph_dir, staging, current_files)
    else:
        fresh = {"nodes": [], "edges": [], "hyperedges": []}
        tokens = {"input_tokens": 0, "output_tokens": 0}
    candidate, labels, audit = merge_graph_records(
        old,
        fresh,
        current_files=current_files,
        old_files=old_files,
        deleted_files=deleted_files,
        source_urls=source_urls,
        captured_at=datetime.now(timezone.utc).isoformat(),
        labels=labels,
    )
    candidate_dir = staging / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    write_json(candidate_dir / "graph.json", candidate)
    write_json(candidate_dir / ".graphify_labels.json", labels)

    from graphify.detect import save_manifest

    manifest_path = candidate_dir / "manifest.json"
    if (graph_dir / "manifest.json").exists():
        shutil.copy2(graph_dir / "manifest.json", manifest_path)
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
    for name, payload in analysis_artifacts(candidate, labels, corpus, tokens).items():
        if name.endswith((".json", ".sig")):
            write_json(candidate_dir / name, payload)
        else:
            (candidate_dir / name).write_text(str(payload), encoding="utf-8")
    audit["tokens"] = tokens
    audit["status"] = "candidate_validated"
    write_json(staging / "candidate-validation.json", audit)
    return candidate_dir, audit


def publish_candidate(candidate: Path, graph_dir: Path, staging: Path) -> None:
    files = [path for path in candidate.iterdir() if path.is_file()]
    if files and all(
        (graph_dir / source.name).is_file()
        and (graph_dir / source.name).read_bytes() == source.read_bytes()
        for source in files
    ):
        return
    backup = staging / "graph-backup"
    backup.mkdir(parents=True, exist_ok=True)
    for source in files:
        live = graph_dir / source.name
        archived = backup / source.name
        if not archived.exists() and live.exists():
            shutil.copy2(live, archived)
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
        if not live.exists() and not archived.exists():
            continue
        raise RuntimeError(f"graph artifact changed concurrently: {live}")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    config_arguments(parser, book=True)
    parser.add_argument("-i", "--input", type=cli_path)
    parser.add_argument("--manifest", type=cli_path, required=True)
    parser.add_argument("--staging", type=cli_path, required=True)
    parser.add_argument("--graph-dir", type=cli_path)
    args = parser.parse_args()
    config = configure(parser, args.config)
    source = (
        configured_book(parser, config, args)
        if args.input is None or args.book
        else None
    )
    corpus = (args.input or source.corpus).resolve()
    graph_dir = (
        args.graph_dir or config.graph_dir or corpus / "graphify-out"
    ).resolve()
    staging = args.staging.resolve()
    staging.mkdir(parents=True, exist_ok=True)
    manifest = load_json(args.manifest)
    scope = sync_scope(manifest, corpus, args.manifest.parent)
    current_files, old_files, deleted_files, source_urls = scope
    if not current_files and not deleted_files:
        result = {"status": "skipped", "reason": "no published corpus changes"}
        write_json(staging / "result.json", result)
        print(json.dumps(result))
        return
    if not (graph_dir / "graph.json").is_file():
        raise SystemExit(f"existing graph not found: {graph_dir / 'graph.json'}")
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
    ready_path = staging / "candidate-ready.json"
    candidate = staging / "candidate"
    if ready_path.exists():
        ready = load_json(ready_path)
        if ready["inputs"] != inputs:
            raise RuntimeError("candidate inputs changed; use a new staging directory")
        if not ready["files"] or any(
            not (candidate / name).is_file() for name in ready["files"]
        ):
            raise RuntimeError(
                "validated candidate files missing; use a new staging directory"
            )
        audit = ready["audit"]
    else:
        candidate, audit = build_candidate(
            corpus,
            graph_dir,
            staging,
            current_files,
            old_files,
            deleted_files,
            source_urls,
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
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
