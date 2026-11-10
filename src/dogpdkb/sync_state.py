"""Canonical, incremental sync state and offline existing-corpus registration."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from dogpdkb.sync_time import now_iso, timestamp_record

STATE_NAME = ".sync-state.json"
LOCAL_NAME = re.compile(r"-(\d{4}-\d{2}-\d{2})-\[(\d+)\]\.md$")


class StateError(ValueError):
    """State or baseline cannot be safely interpreted."""


def state_path(corpus: Path) -> Path:
    return corpus / STATE_NAME


def save_state(corpus: Path, state: dict[str, Any]) -> None:
    """Publish one state snapshot only after its corresponding artifacts exist."""
    path = state_path(corpus)
    if path.is_symlink():
        raise StateError("sync state must not be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".sync-state-", suffix=".tmp", dir=corpus)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_state(corpus: Path, book_key: str | None = None) -> dict[str, Any]:
    path = state_path(corpus)
    if path.is_symlink():
        raise StateError("sync state must not be a symlink")
    if not path.exists():
        return {"version": 2, "active_run": None, "books": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StateError("cannot read sync state") from error
    if not isinstance(state, dict):
        raise StateError("unsupported sync state format")
    if state.get("version") == 1:
        if book_key is None or not isinstance(state.get("documents"), dict):
            raise StateError("v1 state migration requires a selected book")
        if any(not isinstance(record, dict) for record in state["documents"].values()):
            raise StateError("invalid legacy document record")
        return {
            "version": 2,
            "active_run": None,
            "books": {
                book_key: {"documents": {}, "legacy_documents": state["documents"]}
            },
        }
    if state.get("version") != 2 or not isinstance(state.get("books"), dict):
        raise StateError("unsupported sync state format")
    for book in state["books"].values():
        if not isinstance(book, dict) or not isinstance(book.get("documents"), dict):
            raise StateError("invalid book state")
    return state


def document_record(
    state: dict[str, Any], book_key: str, doc_id: str
) -> dict[str, Any]:
    book = state["books"].setdefault(book_key, {"documents": {}})
    return book["documents"].setdefault(
        str(doc_id), {"baseline": None, "observed": None, "steps": {}, "pending": []}
    )


def record_step(
    corpus: Path,
    state: dict[str, Any],
    book_key: str,
    doc_id: str,
    step: str,
    status: str,
    **details: Any,
) -> dict[str, Any]:
    record = document_record(state, book_key, doc_id)
    record["steps"][step] = {**details, "status": status, "recorded_at": now_iso()}
    save_state(corpus, state)
    return record


def _source_names(graph_dir: Path) -> set[str]:
    try:
        graph = json.loads((graph_dir / "graph.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StateError("cannot read existing graph baseline") from error
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise StateError("invalid existing graph baseline")
    return {
        str(row["source_file"])
        for key in ("nodes", "links", "hyperedges")
        for row in graph.get(key, [])
        if isinstance(row, dict) and row.get("source_file")
    }


def initialize_state(
    corpus: Path, graph_dir: Path, book_key: str, *, url: str = "", name: str = ""
) -> dict[str, Any]:
    """Register existing cleaned files without changing corpus or graph content."""
    if not corpus.is_dir():
        raise StateError("corpus directory does not exist")
    state = load_state(corpus, book_key)
    if state.get("active_run"):
        raise StateError("unfinished sync plan exists; resume it before initialization")
    book = state["books"].setdefault(book_key, {"documents": {}})
    if book.get("url") and book["url"] != url:
        raise StateError("book key belongs to a different source URL")
    book.update(key=book_key, url=url, name=name)
    book.setdefault("book_id", None)
    sources = _source_names(graph_dir)
    source_ids = {m.group(2) for source in sources if (m := LOCAL_NAME.search(source))}
    paths: dict[str, list[Path]] = defaultdict(list)
    files = sorted(corpus.glob("*.md"))
    unmatched = []
    for path in files:
        match = LOCAL_NAME.search(path.name)
        if match:
            try:
                date.fromisoformat(match.group(1))
            except ValueError as error:
                raise StateError(f"invalid filename date: {path.name}") from error
            paths[match.group(2)].append(path)
        else:
            unmatched.append(path.name)
    book["unmatched_files"] = unmatched
    counts: Counter[str] = Counter(
        documents=len(paths), files=len(files), unmatched_files=len(unmatched)
    )
    legacy = book.get("legacy_documents", {})
    for doc_id, candidates in paths.items():
        record = document_record(state, book_key, doc_id)
        if len(candidates) != 1:
            counts["duplicate_ids"] += 1
            record["conflicting_files"] = [p.name for p in candidates]
            record["pending"] = sorted(
                set(record["pending"]) | {"DUPLICATE_DOCUMENT_ID"}
            )
            continue
        path = candidates[0]
        record.pop("conflicting_files", None)
        record["pending"] = [
            reason for reason in record["pending"] if reason != "DUPLICATE_DOCUMENT_ID"
        ]
        match = LOCAL_NAME.search(path.name)
        assert match
        coverage = (
            "existing_source"
            if path.name in sources or doc_id in source_ids
            else "unknown"
        )
        counts[
            "graph_sources_matched"
            if coverage == "existing_source"
            else "graph_sources_unknown"
        ] += 1
        if record.get("baseline") is None:
            previous = legacy.get(doc_id) or {}
            exact = (
                previous.get("content_updated_at")
                if previous.get("file") == path.name
                else None
            )
            version = timestamp_record(exact) if exact else None
            segments = path.name[: match.start()].split("__")
            record["baseline"] = {
                "file": path.name,
                "content_kind": "historical_enriched",
                "filename_date": match.group(1),
                "content_updated_at": version["utc"] if version else None,
                "version": version,
                "time_precision": "instant" if version else "day",
                "filename_timezone": "unknown",
                "graph_coverage": coverage,
                "metadata": {
                    "name": segments[-1],
                    "logical_path": "/".join(segments[:-1]),
                    "source_ref": f"{book_key}:{doc_id}",
                },
                "source_limitations": ["HISTORICAL_FILENAME_METADATA"],
                "source": {"path": path.name, "stage": "historical_cleaned"},
            }
        counts[
            "exact_times"
            if record["baseline"].get("content_updated_at")
            else "date_only"
        ] += 1
    book["initialized_at"] = now_iso()
    book["initialization_counts"] = dict(counts)
    save_state(corpus, state)
    return {
        "status": "initialized",
        "state": str(state_path(corpus)),
        "book": book_key,
        "counts": dict(counts),
    }
