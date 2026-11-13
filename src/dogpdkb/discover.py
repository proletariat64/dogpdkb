"""Complete source observation and bounded daily selection, without body fetches."""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from dogpdkb.help import HelpParser
from dogpdkb.processes import diagnostic_argv, run_command
from dogpdkb.records import UTC8, Run, Store, atomic_json, read_json
from dogpdkb.settings import load_config, select_book


class DiscoverError(ValueError):
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


def normalize_version(value):
    """Normalize source timestamps; keep opaque versions and unknown truth."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("utc") or value.get("value")
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise DiscoverError("source version must be a string, integer or null")
    value = str(value)
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return {"value": value, "precision": "opaque"}
    if len(value) == 10:
        return {"value": value, "precision": "day"}
    instant = instant.replace(tzinfo=instant.tzinfo or UTC8)
    return {
        "value": value,
        "utc": instant.astimezone(timezone.utc).isoformat(),
        "precision": "instant",
    }


def _same(left, right):
    return (left or {}).get("utc", (left or {}).get("value")) == (right or {}).get(
        "utc", (right or {}).get("value")
    )


def _day(version):
    if not version:
        return None
    if version["precision"] == "day":
        return version["value"]
    if version["precision"] == "instant":
        return (
            datetime.fromisoformat(version["utc"]).astimezone(UTC8).date().isoformat()
        )
    return None


def _paths(toc):
    entries = []

    def flatten(rows, parent=None):
        for row in rows:
            if not isinstance(row, dict):
                raise DiscoverError("TOC entries must be objects")
            entries.append((row, parent))
            children = row.get("children", [])
            if not isinstance(children, list):
                raise DiscoverError("TOC children must be an array")
            flatten(children, row)

    flatten(toc)
    by_uuid = {str(row["uuid"]): row for row, _ in entries if row.get("uuid")}
    parents = {id(row): parent for row, parent in entries}
    result = {}
    for row, _ in entries:
        doc_id = row.get("doc_id", row.get("document_id"))
        if doc_id is None:
            continue
        chain, seen, current = [], set(), row
        while current is not None:
            if id(current) in seen:
                raise DiscoverError("TOC contains a parent cycle")
            seen.add(id(current))
            if current.get("title"):
                chain.append(str(current["title"]))
            parent_uuid = current.get("parent_uuid")
            current = (
                by_uuid.get(str(parent_uuid)) if parent_uuid else parents[id(current)]
            )
        result[str(doc_id)] = list(reversed(chain))
    return result


def _rows(payload, book):
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "skylark-listing-1"
        or payload.get("complete") is not True
    ):
        raise DiscoverError("a complete skylark-listing-1 artifact is required")
    remote = payload.get("book", {})
    if not isinstance(remote, dict) or remote.get("url", "").rstrip(
        "/"
    ) != book.url.rstrip("/"):
        raise DiscoverError("listing belongs to a different source book")
    rows = payload.get("documents")
    if (
        not isinstance(rows, list)
        or type(remote.get("items_count")) is not int
        or remote["items_count"] != len(rows)
    ):
        raise DiscoverError("listing document count is incomplete")
    if not isinstance(payload.get("toc"), list):
        raise DiscoverError("listing requires a complete TOC")
    paths = _paths(payload["toc"])
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise DiscoverError("listing document must be an object")
        doc_id = row.get("document_id")
        if not isinstance(doc_id, str) or not doc_id or doc_id in result:
            raise DiscoverError("listing document IDs must be nonempty and unique")
        title = row.get("title", "")
        if not isinstance(title, str):
            raise DiscoverError("listing title must be a string")
        path = row.get("toc_path", row.get("path", paths.get(doc_id, [])))
        source = (
            row.get("url")
            or row.get("source_url")
            or f"{book.url.rstrip('/')}/{row.get('slug') or doc_id}"
        )
        result[doc_id] = {
            "document_id": doc_id,
            "version": normalize_version(row.get("version")),
            "title": title,
            "name": title,
            "path": path,
            "logical_path": path,
            "source_url": source,
            "source_ref": source,
            "format": row.get("format", "md"),
            "type": row.get("type", row.get("format", "md")),
            "metadata": row,
        }
    return result


def discover(settings, book, store, run, listing=None):
    """Observe a complete listing inside the caller's lock and run."""
    if (
        not store.data.get("observation")
        and not any(doc.get("baseline") for doc in store.data["documents"].values())
        and any(
            re.search(r"\d{4}-\d{2}-\d{2}-\[\d+\]\.md$", path.name)
            for path in book.corpus.glob("*.md")
        )
    ):
        raise DiscoverError(
            "existing corpus requires registration: run dogpdkb init-state first"
        )
    run.stage("discover")
    target = run.directory / "listing.json"
    if listing is None:
        argv = [
            *settings.command("skylark"),
            "listing",
            "--url",
            book.url,
            "--output",
            str(target),
        ]
        run.data["listing_command"] = diagnostic_argv(argv)
        run.save()
        code = run_command(
            argv,
            log=run.directory / "listing.log",
            env=settings.skylark_environment(),
        )
        if code:
            raise DiscoverError(
                f"Skylark listing failed with exit {code}",
                code if code in {130, 143} else 1,
            )
        payload = read_json(target)
    else:
        payload = read_json(Path(listing))
    current = _rows(payload, book)
    if book.document_ids is not None:
        current = {key: row for key, row in current.items() if key in book.document_ids}
    atomic_json(target, payload)
    previous = store.data.get("observation")
    before = previous["documents"] if previous else {}
    documents = store.data["documents"]
    cutoff = (
        None
        if previous
        else max(
            (
                doc.get("baseline", {}).get("filename_date", "")
                for doc in documents.values()
            ),
            default="",
        )
        or None
    )
    selected, ignored = [], []
    for doc_id, row in current.items():
        local = documents.get(doc_id, {})
        old = before.get(doc_id) or (local.get("observed") if previous else None)
        reasons = []
        if old:
            if not _same(row["version"], old["version"]):
                reasons.append("version-changed")
            for field in ("title", "path", "source_url", "format"):
                if row[field] != old[field]:
                    reasons.append(f"{field}-changed")
        elif previous:
            reasons.append("new")
        elif local.get("corpus") or local.get("baseline"):
            prior = normalize_version(
                local.get("corpus", {}).get("version")
                or local.get("baseline", {}).get("filename_date")
            )
            if not row["version"] or not prior:
                reasons.append("version-unknown")
            elif prior["precision"] == "day":
                if (
                    _day(row["version"]) is None
                    or _day(row["version"]) >= prior["value"]
                ):
                    reasons.append("date-needs-comparison")
            elif not _same(row["version"], prior):
                reasons.append("version-changed")
        elif cutoff and _day(row["version"]) and _day(row["version"]) < cutoff:
            ignored.append(doc_id)
        else:
            reasons.append("new")
        pending = local.get("pending")
        if pending and _same(pending["version"], row["version"]):
            reasons = list(dict.fromkeys([*reasons, *pending["reasons"]]))
        if reasons:
            selected.append({**row, "reasons": reasons, "local": deepcopy(local)})
    expected_ids = (
        set(book.document_ids)
        if book.document_ids is not None
        else set(before) | set(documents)
    )
    absent = sorted(expected_ids - set(current))
    differences = {
        "schema": "dogpdkb-differences-1",
        "book": store.data["book"],
        "run_id": run.id,
        "captured_at": payload.get("captured_at"),
        "listing": str(target),
        "documents": selected,
        "absent": absent,
        "ignored": sorted(ignored),
        "cutoff": cutoff,
    }
    path = run.directory / "differences.json"
    atomic_json(path, differences)
    for doc_id, row in current.items():
        store.document(doc_id)["observed"] = row
        store.document(doc_id).pop("absence", None)
    for row in selected:
        store.document(row["document_id"])["pending"] = {
            "version": row["version"],
            "reasons": row["reasons"],
            "metadata": row["metadata"],
            "run_id": run.id,
        }
    for doc_id in absent:
        store.document(doc_id)["absence"] = {
            "captured_at": payload.get("captured_at"),
            "run_id": run.id,
        }
    store.data["observation"] = {
        "documents": current,
        "captured_at": payload.get("captured_at"),
        "run_id": run.id,
    }
    store.save()
    run.data["artifacts"].update(listing=str(target), differences=str(path))
    run.stage(
        "discover", observed=len(current), selected=len(selected), absent=len(absent)
    )
    return differences


def main(argv=None):
    parser = HelpParser(prog="dogpdkb discover")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    parser.add_argument("--listing", type=Path)
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
            run = Run.create(store, "discover")
            result = discover(settings, book, store, run, args.listing)
            run.finish("completed")
        print(
            json.dumps(
                {
                    "run": str(run.directory),
                    "differences": run.data["artifacts"]["differences"],
                    "selected": len(result["documents"]),
                    "absent": len(result["absent"]),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError) as error:
        code = getattr(error, "exit_code", 1)
        if run:
            run.failure(step="discover", reason=str(error), exit_code=code)
            run.finish(
                "interrupted" if code in {130, 143} else "failed", code, str(error)
            )
        print(f"dogpdkb discover: {error}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
