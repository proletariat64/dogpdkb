"""Persist concrete sync business steps in the canonical corpus state."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any

from dogpdkb.sync_state import StateError, document_record, load_state, save_state
from dogpdkb.sync_time import now_iso, timestamp_record


class Progress:
    def __init__(
        self,
        corpus: Path,
        book_key: str,
        *,
        url: str = "",
        name: str = "",
        book_id: int | None = None,
    ):
        self.corpus = Path(corpus).resolve()
        known = load_state(self.corpus, book_key)
        if book_key == "adhoc" and book_key not in known["books"] and known["books"]:
            matches = [
                key
                for key, entry in known["books"].items()
                if url and entry.get("url", "").rstrip("/") == url.rstrip("/")
            ]
            if len(matches) == 1:
                book_key = matches[0]
            elif len(known["books"]) == 1:
                book_key = next(iter(known["books"]))
            else:
                raise StateError("select an explicit book for this registered corpus")
        self.key = book_key
        self.run: str | None = None
        self._page_lock = Lock()
        self._reload()
        self.metadata = {"url": url, "name": name, "book_id": book_id}
        existing = self.root["books"].setdefault(book_key, {"documents": {}})
        if (
            existing.get("url")
            and url
            and existing["url"].rstrip("/") != url.rstrip("/")
        ):
            raise StateError("book key belongs to a different source URL")
        if existing.get("book_id") not in (None, book_id) and book_id is not None:
            raise StateError("book state belongs to a different remote book")
        if url:
            existing["url"] = url
        if name:
            existing["name"] = name
        if book_id is not None:
            existing["book_id"] = book_id
        existing.setdefault("key", book_key)
        self.book = existing

    def _reload(self):
        self.root = load_state(self.corpus, self.key)
        self.book = self.root["books"].setdefault(self.key, {"documents": {}})
        for field, value in getattr(self, "metadata", {}).items():
            if value is not None and value != "":
                self.book[field] = value
        self.book.setdefault("key", self.key)

    def claim(
        self,
        staging: Path,
        *,
        phase: str,
        parent: Path | None = None,
        persist: bool = True,
    ):
        self._reload()
        inherited = parent or os.environ.get("DOGPDKB_RUN_STAGING")
        identity = str(Path(inherited or staging).expanduser().resolve())
        active = self.root.get("active_run")
        if active and (
            active.get("staging") != identity or active.get("book_key") != self.key
        ):
            raise StateError(
                "unfinished sync plan exists; resume it before starting another plan"
            )
        if (
            phase in {"compare", "execute"}
            and active
            and active.get("plan_staging")
            not in (None, str(Path(staging).expanduser().resolve()))
        ):
            raise StateError(
                "unfinished knowledge plan exists; resume its original staging directory"
            )
        self.run = identity
        self.root["active_run"] = {
            **(active or {}),
            "staging": identity,
            "book_key": self.key,
            "phase": phase,
            "updated_at": now_iso(),
        }
        if phase in {"compare", "execute"}:
            self.root["active_run"]["plan_staging"] = str(
                Path(staging).expanduser().resolve()
            )
        if persist:
            save_state(self.corpus, self.root)

    def step(self, doc_id, step: str, status: str, **details: Any):
        self._reload()
        row = document_record(self.root, self.key, str(doc_id))
        row.setdefault("steps", {})[step] = {
            **details,
            "status": status,
            "recorded_at": now_iso(),
        }
        save_state(self.corpus, self.root)
        return row

    def update(self, doc_id, **sections: Any):
        self._reload()
        row = document_record(self.root, self.key, str(doc_id))
        row.update(sections)
        save_state(self.corpus, self.root)
        return row

    def observe_page(self, offset: int, documents: list[dict[str, Any]]):
        # Remote page workers share this instance; serialize their atomic state writes.
        with self._page_lock:
            self._reload()
            observed_at = now_iso()
            for document in documents:
                row = document_record(self.root, self.key, str(document["id"]))
                row["observed"] = {
                    "version": document["version"],
                    "observed_at": observed_at,
                }
            pages = self.book.setdefault("listing_pages", {})
            pages[str(offset)] = {"items": len(documents), "recorded_at": observed_at}
            save_state(self.corpus, self.root)

    def phase(self, step: str, status: str, **details: Any):
        self._reload()
        self.book.setdefault("steps", {})[step] = {
            **details,
            "status": status,
            "recorded_at": now_iso(),
        }
        save_state(self.corpus, self.root)

    def finish(self):
        self._reload()
        active = self.root.get("active_run")
        if (
            active
            and self.run
            and active.get("staging") == self.run
            and active.get("book_key") == self.key
        ):
            self.root["active_run"] = None
            save_state(self.corpus, self.root)

    def reference(self, path: Path, stage: str = "raw_export"):
        path = Path(path).resolve()
        return {
            "path": str(path.relative_to(self.corpus))
            if path.is_relative_to(self.corpus)
            else str(path),
            "stage": stage,
        }

    @staticmethod
    def version(value, field: str = "content_updated_at"):
        return timestamp_record(value, field=field) if value else None


def progress_for_config(config, source, corpus: Path | None = None) -> Progress | None:
    inherited_corpus = os.environ.get("DOGPDKB_RUN_CORPUS")
    inherited_book = os.environ.get("DOGPDKB_RUN_BOOK")
    if inherited_corpus and inherited_book:
        return Progress(Path(inherited_corpus), inherited_book)
    if source is None:
        return None
    target = Path(corpus or source.corpus)
    root = load_state(target, source.key)
    matches = [
        key
        for key, book in root["books"].items()
        if book.get("url", "").rstrip("/") == source.url.rstrip("/")
    ]
    if len(matches) > 1:
        raise StateError("source URL matches multiple book state entries")
    key = matches[0] if matches else source.key
    return Progress(target, key, url=source.url, name=source.name)
