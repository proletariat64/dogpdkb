"""A one-book knowledge view over the canonical corpus state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dogpdkb.sync_state import StateError, load_state, save_state


class BookState(dict):
    def __init__(self, root: dict[str, Any], key: str, book: dict[str, Any]):
        self.root = root
        self.key = key
        entry = root["books"][key]
        entry["book_id"] = book["book_id"]
        documents = entry["documents"]
        for row in documents.values():
            initial = row.get("baseline") or {}
            row.setdefault("observation", None)
            row.setdefault(
                "corpus", {"status": "not_captured", "version": None, "raw": None}
            )
            row.setdefault("graph", {})
            if initial and not row["graph"]:
                row["graph"] = {
                    "status": "covered"
                    if initial.get("graph_coverage") == "existing_source"
                    else "uncovered",
                    "coverage": "registered_historical_baseline"
                    if initial.get("graph_coverage") == "existing_source"
                    else "unregistered_historical_baseline",
                    "version": initial.get("version"),
                    "raw": initial.get("source"),
                    "content_kind": initial.get("content_kind", "historical_enriched"),
                    "metadata": initial.get("metadata", {}),
                }
            row.setdefault("pending", [])
            row.setdefault("steps", {})
        super().__init__(
            schema_version="knowledge-state-1.0", book=book, documents=documents
        )


def canonical_view(root: dict[str, Any], book: dict[str, Any]) -> BookState:
    key = book.get("key")
    if key not in root["books"]:
        matches = [
            k
            for k, v in root["books"].items()
            if v.get("book_id") == book["book_id"]
            or (
                book.get("url")
                and v.get("url", "").rstrip("/") == book["url"].rstrip("/")
            )
        ]
        if len(matches) != 1:
            raise StateError("snapshot cannot be bound to one initialized book")
        key = matches[0]
    entry = root["books"][key]
    if entry.get("book_id") not in (None, book["book_id"]):
        raise StateError("snapshot and initialized state refer to different books")
    if (
        book.get("url")
        and entry.get("url")
        and book["url"].rstrip("/") != entry["url"].rstrip("/")
    ):
        raise StateError("snapshot and initialized state refer to different URLs")
    return BookState(root, key, book)


def root_state(view: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(view, BookState):
        return view
    root = view.root
    root["books"][view.key]["documents"] = view["documents"]
    return root


def save_view(path: Path, view: dict[str, Any]) -> None:
    if isinstance(view, BookState):
        save_state(path.parent, root_state(view))
    else:
        from mdcompare.io import publish_reports

        publish_reports([(path, json.dumps(view, ensure_ascii=False, indent=1) + "\n")])


def load_view(path: Path, book: dict[str, Any]) -> BookState:
    return canonical_view(load_state(path.parent, book.get("key")), book)
