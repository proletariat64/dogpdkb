"""Complete source observation and bounded daily selection, without body fetches."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from dogpdkb.directory_snapshot import (
    copy_snapshot,
    observe_resources,
    snapshot_version,
)
from dogpdkb.help import HelpParser
from dogpdkb.planning import descriptor
from dogpdkb.processes import diagnostic_argv, run_command
from dogpdkb.records import UTC8, Run, Store, atomic_json, now_iso, read_json
from dogpdkb.settings import YuqueSource, document_identity, load_config, select_book


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


def _yuque_listing(settings, run, source, listing):
    target = run.directory / "listings" / f"{source.key}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if listing is None:
        argv = [
            *settings.command("skylark"),
            "listing",
            "--url",
            source.url,
            "--output",
            str(target),
        ]
        run.data.setdefault("listing_commands", {})[source.key] = diagnostic_argv(argv)
        run.save()
        code = run_command(
            argv, log=target.with_suffix(".log"), env=settings.skylark_environment()
        )
        if code:
            raise DiscoverError(
                f"Skylark listing failed with exit {code}",
                code if code in {130, 143} else 1,
            )
        payload = read_json(target)
    else:
        payload = read_json(Path(listing))
    current = _rows(payload, source)
    if source.document_ids is not None:
        current = {
            key: row for key, row in current.items() if key in source.document_ids
        }
    if listing is not None:
        atomic_json(target, payload)
    return _with_source(current, source.key), payload.get("captured_at"), str(target)


def _with_source(rows, source_key):
    current = {}
    for source_id, row in rows.items():
        identity = document_identity(source_key, source_id)
        current[identity] = {
            **row,
            "document_id": identity,
            "source_key": source_key,
            "source_document_id": source_id,
        }
    return current


def _directory_listing(settings, book, run, source):
    root = source.path
    if not root.is_dir():
        raise DiscoverError(
            f"directory source does not exist or is not a directory: {root}"
        )
    for target in (
        book.corpus,
        settings.graph_for(book),
        settings.staging,
        settings.state,
        settings.ocr_cache,
        settings.web,
        settings.results,
    ):
        if root.is_relative_to(target) or target.is_relative_to(root):
            raise DiscoverError(f"directory source overlaps a managed path: {target}")

    def raise_scan_error(error):
        raise error

    rows = {}
    for parent, dirs, files in os.walk(root, onerror=raise_scan_error):
        folder = Path(parent)
        dirs[:] = sorted(
            name
            for name in dirs
            if not name.startswith(".")
            and name != "_inventory"
            and not (folder / name).is_symlink()
        )
        for name in sorted(files):
            path = folder / name
            if (
                name.startswith(".")
                or path.suffix.lower() != ".md"
                or path.is_symlink()
                or not path.is_file()
            ):
                continue
            relative = path.relative_to(root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows[relative] = {
                "directory_body_sha256": digest,
                "title": path.stem,
                "name": path.stem,
                "path": relative,
                "logical_path": relative,
                "source_url": path.as_uri(),
                "source_ref": path.as_uri(),
                "format": "md",
                "type": "md",
                "metadata": {"relative_path": relative},
            }
    current = _with_source(rows, source.key)
    if current:
        _catalog_directory(settings, book, run, source, current)
    return current, now_iso(), None


def _catalog_directory(settings, book, run, source, documents):
    request = run.directory / f"directory-{source.key}-descriptors.json"
    report = run.directory / f"directory-{source.key}-catalog.json"
    atomic_json(
        request,
        {
            "schema_version": "1.0",
            "documents": [
                descriptor(book, row, source.path / row["source_document_id"])
                for row in documents.values()
            ],
        },
    )
    argv = [
        *settings.command("mdcompare"),
        "catalog",
        "--input",
        str(request),
        "--output",
        str(report),
    ]
    code = run_command(argv, log=report.with_suffix(".log"))
    if code:
        raise DiscoverError(
            f"directory resource catalog failed with exit {code}",
            code if code in {130, 143} else 1,
        )
    result = read_json(report)
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "1.0"
        or not isinstance(result.get("documents"), list)
    ):
        raise DiscoverError("invalid directory resource catalog")
    seen = set()
    for item in result["documents"]:
        identity = item.get("document_id") if isinstance(item, dict) else None
        row = documents.get(identity)
        if row is None or identity in seen or item.get("knowledge_base") != book.key:
            raise DiscoverError("directory resource catalog document mismatch")
        seen.add(identity)
        path = source.path / row["source_document_id"]
        resources = item.get("resources")
        if item.get("path") != str(path) or not isinstance(resources, list):
            raise DiscoverError("invalid directory resource catalog entry")
        row["directory_resources"] = observe_resources(source.path, path, resources)
        row["version"] = snapshot_version(
            row["directory_body_sha256"], row["directory_resources"]
        )
    if seen != set(documents):
        raise DiscoverError("incomplete directory resource catalog")


def _select(current, prior, documents, *, yuque):
    before = prior["documents"] if prior else {}
    cutoff = (
        max(
            (
                doc.get("baseline", {}).get("filename_date", "")
                for doc in documents.values()
            ),
            default="",
        )
        or None
        if yuque and not prior
        else None
    )
    selected, ignored = [], []
    for doc_id, row in current.items():
        local = documents.get(doc_id, {})
        old = before.get(doc_id) or (local.get("observed") if prior else None)
        listed_day = _day(row["version"])
        reasons = []
        if old:
            if not _same(row["version"], old["version"]):
                reasons.append("version-changed")
            for field in ("title", "path", "source_url", "format"):
                if row[field] != old[field]:
                    reasons.append(f"{field}-changed")
        elif prior or not yuque:
            reasons.append("new")
        elif local.get("corpus") or local.get("baseline"):
            previous = normalize_version(
                local.get("corpus", {}).get("version")
                or local.get("baseline", {}).get("filename_date")
            )
            if not row["version"] or not previous:
                reasons.append("version-unknown")
            elif previous["precision"] == "day":
                if listed_day is None or listed_day >= previous["value"]:
                    reasons.append("date-needs-comparison")
            elif not _same(row["version"], previous):
                reasons.append("version-changed")
        elif cutoff and listed_day and listed_day < cutoff:
            ignored.append(doc_id)
        else:
            reasons.append("new")
        pending = local.get("pending")
        if pending and _same(pending["version"], row["version"]):
            reasons = list(dict.fromkeys([*reasons, *pending["reasons"]]))
        if reasons:
            selected.append({**row, "reasons": reasons, "local": deepcopy(local)})
    return selected, sorted(ignored), cutoff


def discover(settings, book, store, run, listing=None, *, sources=None):
    """Validate selected scans before recording any observations."""
    sources = book.select_sources() if sources is None else sources
    if (
        listing is not None
        and sum(isinstance(source, YuqueSource) for source in sources) != 1
    ):
        raise DiscoverError("--listing requires exactly one selected Yuque source")
    previous = store.data.get("observation") or {}
    documents = store.data["documents"]
    if any(
        isinstance(source, YuqueSource) and source.key not in previous
        for source in sources
    ):
        owned = {doc.get("corpus", {}).get("path") for doc in documents.values()}
        for path in book.corpus.glob("*.md"):
            match = re.search(r"\d{4}-\d{2}-\d{2}-\[(\d+)\]\.md$", path.name)
            if (
                str(path) not in owned
                and match
                and any(
                    isinstance(source, YuqueSource)
                    and source.key not in previous
                    and (source.document_ids is None or match[1] in source.document_ids)
                    for source in sources
                )
            ):
                raise DiscoverError(
                    "existing corpus requires registration: run dogpdkb init-state first"
                )
    run.stage("discover")
    seen, selected, absent, ignored, listings, cutoffs = {}, [], [], [], {}, {}
    for source in sources:
        yuque = isinstance(source, YuqueSource)
        try:
            if yuque:
                current, captured, artifact = _yuque_listing(
                    settings, run, source, listing
                )
            else:
                current, captured, artifact = _directory_listing(
                    settings, book, run, source
                )
            local = {
                key: doc
                for key, doc in documents.items()
                if doc.get("source_key") == source.key
            }
            prior = previous.get(source.key)
            rows, skipped, cutoff = _select(current, prior, local, yuque=yuque)
            if not yuque:
                for row in rows:
                    target = run.directory / "directory-inputs" / row["document_id"]
                    row["frozen_input"] = str(copy_snapshot(source.path, target, row))
        except (OSError, ValueError, TypeError) as error:
            raise DiscoverError(
                f"source {source.key}: {error}", getattr(error, "exit_code", 1)
            ) from error
        expected = (
            {document_identity(source.key, item) for item in source.document_ids}
            if yuque and source.document_ids is not None
            else set(prior["documents"] if prior else ()) | set(local)
        )
        missing = sorted(expected - set(current))
        seen[source.key] = {
            "documents": current,
            "captured_at": captured,
            "run_id": run.id,
        }
        selected.extend(rows)
        absent.extend(missing)
        ignored.extend(skipped)
        if artifact is not None:
            listings[source.key] = artifact
        cutoffs[source.key] = cutoff
    captured_at = now_iso()
    differences = {
        "schema": "dogpdkb-differences-2",
        "book": store.data["book"],
        "run_id": run.id,
        "captured_at": captured_at,
        "sources": list(seen),
        "listings": listings,
        "documents": selected,
        "absent": sorted(absent),
        "ignored": sorted(ignored),
        "cutoffs": cutoffs,
    }
    path = run.directory / "differences.json"
    for scan in seen.values():
        for doc_id, row in scan["documents"].items():
            document = store.document(doc_id)
            document.update(
                source_key=row["source_key"],
                source_document_id=row["source_document_id"],
                observed=row,
            )
            document.pop("absence", None)
    for row in selected:
        store.document(row["document_id"])["pending"] = {
            "version": row["version"],
            "reasons": row["reasons"],
            "metadata": row["metadata"],
            "run_id": run.id,
        }
    for doc_id in absent:
        store.document(doc_id)["absence"] = {
            "captured_at": captured_at,
            "run_id": run.id,
        }
    store.data["observation"] = {**previous, **seen}
    store.save()
    atomic_json(path, differences)
    run.data["artifacts"]["differences"] = str(path)
    run.stage(
        "discover",
        observed=sum(len(scan["documents"]) for scan in seen.values()),
        selected=len(selected),
        absent=len(absent),
    )
    return differences


def main(argv=None):
    parser = HelpParser(prog="dogpdkb discover")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    parser.add_argument("--listing", type=Path)
    parser.add_argument("--source", action="append", metavar="KEY")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    run = None
    try:
        settings = load_config(args.config)
        book = select_book(settings, args.book)
        sources = book.select_sources(args.source)
        store = Store(settings, book)
        with store.lock():
            run = Run.create(store, "discover")
            result = discover(settings, book, store, run, args.listing, sources=sources)
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
