"""Auditable, read-only Yuque observation snapshots and selected raw exports."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from dogpdkb.sync_progress import Progress
from dogpdkb.sync_state import state_path
from dogpdkb.sync_time import now_iso, parse_timestamp, timestamp_record
from mdcompare.io import InputError, validate_outputs

SCHEMA_VERSION = "knowledge-capture-1.0"


class CaptureError(ValueError):
    """An observation cannot be represented as a complete, trustworthy snapshot."""


def _utc_now() -> str:
    return now_iso()


def _version(document: dict[str, Any]) -> dict[str, Any]:
    field = "content_updated_at" if document.get("content_updated_at") else "updated_at"
    value = document.get(field)
    if not value:
        raise CaptureError(f"document {document['id']} has no usable update timestamp")
    try:
        return timestamp_record(str(value), field)
    except ValueError as error:
        raise CaptureError(
            f"document {document['id']} has an invalid update timestamp"
        ) from error


def _instant(version: dict[str, Any]) -> datetime:
    try:
        return parse_timestamp(str(version["utc"]))
    except (KeyError, TypeError, ValueError) as error:
        raise CaptureError(
            "snapshot contains an invalid normalized timestamp"
        ) from error


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaptureError(f"cannot read JSON input {path.name}: {error}") from error


def _validate_pages(pages: Any, document_count: int) -> list[dict[str, int]]:
    if not isinstance(pages, list) or not pages:
        raise CaptureError("complete listing requires page coverage")
    result = []
    expected_offset = 0
    for page in pages:
        if not isinstance(page, dict):
            raise CaptureError("invalid listing page coverage")
        offset = page.get("offset")
        limit = page.get("limit")
        item_count = page.get("item_count")
        if (
            type(offset) is not int
            or type(limit) is not int
            or type(item_count) is not int
            or limit <= 0
            or item_count < 0
            or item_count > limit
            or offset != expected_offset
        ):
            raise CaptureError("listing page coverage has a gap or invalid bounds")
        result.append({"offset": offset, "limit": limit, "item_count": item_count})
        expected_offset += limit
    if sum(page["item_count"] for page in result) != document_count:
        raise CaptureError("listing page coverage does not match document count")
    if any(page["item_count"] != page["limit"] for page in result[:-1]):
        raise CaptureError("listing page coverage has a short non-final page")
    if result[-1]["item_count"] == result[-1]["limit"]:
        raise CaptureError("listing page coverage has no terminating page")
    return result


def _load_local_listing(
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise CaptureError("local listing must be a JSON object")
    book = payload.get("book")
    documents = payload.get("documents")
    listing = payload.get("listing")
    if (
        not isinstance(book, dict)
        or not isinstance(documents, list)
        or not isinstance(listing, dict)
    ):
        raise CaptureError("local listing requires book, documents and listing objects")
    if type(book.get("book_id")) is not int or type(book.get("items_count")) is not int:
        raise CaptureError("local listing has an invalid book identity or count")
    ids: list[int] = []
    for document in documents:
        if not isinstance(document, dict) or type(document.get("id")) is not int:
            raise CaptureError("local listing contains an invalid document identity")
        ids.append(document["id"])
    if len(set(ids)) != len(ids):
        raise CaptureError("local listing contains duplicate document IDs")
    if listing.get("complete") is not True:
        raise CaptureError("local listing is not complete")
    if listing.get("document_count") != len(documents):
        raise CaptureError("local listing document_count does not match documents")
    if book.get("items_count") != len(documents):
        raise CaptureError("local listing book.items_count does not match documents")
    pages = _validate_pages(listing.get("pages"), len(documents))
    return (
        book,
        documents,
        {
            "complete": True,
            "document_count": len(documents),
            "document_ids": sorted(ids),
            "pages": pages,
        },
    )


def _raw_inputs(snapshot_path: Path) -> set[Path]:
    """Resolve every raw input referenced by a baseline before any output write."""
    if not snapshot_path.is_file():
        return set()
    payload = _read_json(snapshot_path)
    result: set[Path] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("stage") == "raw_export" and isinstance(
                value.get("path"), str
            ):
                raw = Path(value["path"])
                result.add(
                    raw.resolve()
                    if raw.is_absolute()
                    else (snapshot_path.parent / raw).resolve()
                )
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return result


def _validate_capture_paths(args: Any, snapshot_path: Path, staging: Path) -> set[Path]:
    inputs = {
        Path(value).expanduser().resolve()
        for value in (
            args.baseline_snapshot,
            args.pending_state,
            args.listing,
            args.details_dir,
        )
        if value
    }
    if args.baseline_snapshot:
        inputs |= _raw_inputs(Path(args.baseline_snapshot).expanduser().resolve())
    outputs = [
        snapshot_path,
        staging / "pages",
        staging / "capture-checkpoint.json",
        staging / "raw",
    ]
    try:
        for index, output in enumerate(outputs):
            validate_outputs(
                [output], inputs | set(outputs[:index] + outputs[index + 1 :])
            )
        for path in (staging / "pages").glob("*"):
            if path.is_file() or path.is_symlink():
                validate_outputs([path], inputs)
    except InputError as error:
        raise CaptureError(error.reason_code) from error
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(staging, 0o700)
    return inputs


def _load_remote_listing(
    source_url: str, staging: Path, progress: Progress | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    from dogpdkb.workflows.get_sync_updates import (
        PAGE_SIZE,
        SkylarkError,
        fetch_doc_list,
        resolve_book,
        toc_paths,
    )

    try:
        book = resolve_book(source_url)
        pages_dir = staging / "pages"
        if progress:
            progress.metadata["book_id"] = book["book_id"]
        documents = fetch_doc_list(
            book["book_id"],
            book["items_count"],
            pages_dir,
            on_page=(
                lambda offset, rows: progress.observe_page(
                    offset,
                    [{"id": row["id"], "version": _version(row)} for row in rows],
                )
            )
            if progress
            else None,
        )
        paths = toc_paths(book["book_id"])
    except SkylarkError as error:
        raise CaptureError(f"remote observation failed: {error}") from error
    if book["items_count"] != len(documents):
        raise CaptureError("remote book count and complete listing do not match")
    pages = []
    # Every offset through the proven count was fetched on this attempt; a full
    # final page also requires the freshly fetched empty terminating page.
    for offset in range(0, len(documents) + 1, PAGE_SIZE):
        path = pages_dir / f"page-{book['book_id']}-{offset:07}.json"
        checkpoint = _read_json(path)
        if not isinstance(checkpoint.get("items"), list):
            raise CaptureError("remote page checkpoint is invalid")
        pages.append(
            {
                "offset": checkpoint["offset"],
                "limit": PAGE_SIZE,
                "item_count": len(checkpoint["items"]),
            }
        )
    for document in documents:
        if document["id"] not in paths:
            raise CaptureError(
                f"document {document['id']} is missing from the book TOC"
            )
        document["toc_path"] = paths[document["id"]]
    ids = [document["id"] for document in documents]
    return (
        book,
        documents,
        {
            "complete": True,
            "document_count": len(documents),
            "document_ids": sorted(ids),
            "pages": _validate_pages(pages, len(documents)),
        },
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_baseline(path: Path, book: dict[str, Any]) -> dict[str, Any]:
    baseline = _read_json(path)
    if (
        not isinstance(baseline, dict)
        or baseline.get("schema_version") != SCHEMA_VERSION
    ):
        raise CaptureError("baseline has an unsupported snapshot schema")
    if baseline.get("status") not in {"initialized", "captured"}:
        raise CaptureError("baseline is not a complete observation")
    if (baseline.get("listing") or {}).get("complete") is not True:
        raise CaptureError("baseline listing is incomplete")
    if (baseline.get("book") or {}).get("book_id") != book.get("book_id"):
        raise CaptureError("baseline and current listing refer to different books")
    documents = baseline.get("documents")
    if not isinstance(documents, dict):
        raise CaptureError("baseline has no complete document records")
    try:
        document_ids = [int(value) for value in documents]
        listed_ids = [int(value) for value in baseline["listing"]["document_ids"]]
    except (KeyError, TypeError, ValueError) as error:
        raise CaptureError("baseline document IDs are invalid") from error
    if (
        len(set(document_ids)) != len(document_ids)
        or sorted(document_ids) != sorted(listed_ids)
        or baseline["listing"].get("document_count") != len(document_ids)
        or any(
            not isinstance(record, dict) or record.get("id") != int(doc_id)
            for doc_id, record in documents.items()
        )
    ):
        raise CaptureError("baseline document IDs do not match its complete listing")
    for record in documents.values():
        _instant(record.get("version") or {})
    return baseline


def _load_pending(path: Path, book: dict[str, Any]) -> dict[int, list[str]]:
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise CaptureError("pending state has an unsupported snapshot schema")
    if (payload.get("book") or {}).get("book_id") != book.get("book_id"):
        raise CaptureError("pending state and current listing refer to different books")
    rows = payload.get("pending")
    if not isinstance(rows, list):
        raise CaptureError("pending state requires a pending list")
    result: dict[int, list[str]] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or type(row.get("id")) is not int
            or not isinstance(row.get("reasons"), list)
            or not all(isinstance(reason, str) for reason in row["reasons"])
        ):
            raise CaptureError("pending state contains an invalid document record")
        if row["id"] in result:
            raise CaptureError("pending state contains duplicate document IDs")
        result[row["id"]] = list(row["reasons"])
    return result


def _load_checkpoint(staging: Path, book: dict[str, Any]) -> dict[str, Any]:
    path = staging / "capture-checkpoint.json"
    if not path.exists():
        return {}
    payload = _read_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "knowledge-capture-checkpoint-1.0"
        or payload.get("book_id") != book.get("book_id")
        or not isinstance(payload.get("documents"), dict)
    ):
        raise CaptureError("staging checkpoint belongs to a different capture")
    return payload["documents"]


def _save_checkpoint(
    staging: Path, book: dict[str, Any], documents: dict[str, Any]
) -> None:
    _write_json(
        staging / "capture-checkpoint.json",
        {
            "schema_version": "knowledge-capture-checkpoint-1.0",
            "book_id": book["book_id"],
            "documents": documents,
        },
    )


def _raw_reference(path: Path, snapshot_path: Path, captured_at: str) -> dict[str, Any]:
    return {
        "path": os.path.relpath(path, snapshot_path.parent),
        "stage": "raw_export",
        "captured_at": captured_at,
    }


def _rebase_raw(
    raw: dict[str, Any], baseline_path: Path, snapshot_path: Path
) -> dict[str, Any] | None:
    if not isinstance(raw.get("path"), str):
        return None
    source = (baseline_path.parent / raw["path"]).resolve()
    if not source.is_file():
        return None
    return {
        **raw,
        "path": os.path.relpath(source, snapshot_path.parent),
    }


def _raw_name(document: dict[str, Any], version: dict[str, Any]) -> str:
    stamp = version["utc"].replace("-", "").replace(":", "").replace(".", "_")
    return f"{document['id']}-{stamp}.md"


def _save_raw(
    document: dict[str, Any],
    detail: dict[str, Any],
    staging: Path,
    snapshot_path: Path,
    protected: set[Path],
) -> tuple[dict[str, Any], dict[str, Any]]:
    version = _version(detail)
    raw_dir = staging / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_path = raw_dir / _raw_name(document, version)
    try:
        validate_outputs(
            [raw_path],
            protected
            | {
                snapshot_path,
                staging / "pages",
                staging / "capture-checkpoint.json",
            },
        )
    except InputError as error:
        raise CaptureError(error.reason_code) from error
    if raw_path.exists() and raw_path.read_text(encoding="utf-8") != detail["body"]:
        raise CaptureError(
            f"raw export already exists with different content: {raw_path.name}"
        )
    if not raw_path.exists():
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{raw_path.name}.", suffix=".tmp", dir=raw_dir
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(detail["body"])
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, raw_path)
        finally:
            temporary.unlink(missing_ok=True)
    os.chmod(raw_path, 0o600)
    return version, _raw_reference(raw_path, snapshot_path, _utc_now())


def _export_local(
    document: dict[str, Any], details_dir: Path
) -> tuple[str, dict[str, Any]]:
    detail = _read_json(details_dir / f"{document['id']}.json")
    if not isinstance(detail, dict) or not isinstance(detail.get("body"), str):
        raise CaptureError(f"document {document['id']} detail has no Markdown body")
    if detail.get("id", document["id"]) != document["id"]:
        raise CaptureError(f"document {document['id']} detail identity does not match")
    return detail["body"], detail


def _export_remote(document: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from dogpdkb.workflows.apply_sync_updates import export_doc
    from dogpdkb.workflows.get_sync_updates import SkylarkError

    try:
        return export_doc(
            document["id"], document.get("format") or document.get("type")
        )
    except SkylarkError as error:
        raise CaptureError(
            f"document {document['id']} export failed: {error}"
        ) from error


def run_capture(args: Any) -> int:
    """Run the explicitly selected snapshot path for the discover CLI."""
    snapshot_path = Path(args.snapshot).expanduser().resolve()
    if snapshot_path.exists():
        existing = _read_json(snapshot_path)
        if (
            not isinstance(existing, dict)
            or existing.get("schema_version") != SCHEMA_VERSION
        ):
            raise CaptureError("snapshot output already exists with another format")
        if existing.get("status") in {"initialized", "captured"}:
            raise CaptureError("snapshot output is already complete")
        if existing.get("status") != "incomplete":
            raise CaptureError("snapshot output has an unknown status")
    staging = (
        Path(args.staging).expanduser().resolve()
        if args.staging
        else snapshot_path.parent / "staging"
    )
    protected_inputs = _validate_capture_paths(args, snapshot_path, staging)
    progress = None
    corpus = getattr(args, "sync_corpus", None)
    source = getattr(args, "sync_source", None)
    if corpus and source and state_path(Path(corpus)).is_file():
        progress = Progress(Path(corpus), source.key, url=source.url, name=source.name)
        progress.claim(staging, phase="capture", parent=snapshot_path)
    started_at = _utc_now()
    if args.listing:
        book, documents, listing = _load_local_listing(
            Path(args.listing).expanduser().resolve()
        )
    else:
        if args.details_dir:
            raise CaptureError("--details-dir requires the local --listing adapter")
        book, documents, listing = _load_remote_listing(
            args.skylark_url, staging, progress
        )
    if progress:
        progress = Progress(
            progress.corpus,
            progress.key,
            url=source.url,
            name=source.name,
            book_id=book["book_id"],
        )
        progress.run = str(snapshot_path)
        book = {**book, "key": source.key, "url": source.url}
    records = {
        str(document["id"]): {
            "id": document["id"],
            "title": document.get("title"),
            "slug": document.get("slug"),
            "format": document.get("format") or document.get("type"),
            "toc_path": document.get("toc_path") or [],
            "version": _version(document),
        }
        for document in documents
    }
    baseline_path = (
        Path(args.baseline_snapshot).expanduser().resolve()
        if args.baseline_snapshot
        else None
    )
    if baseline_path == snapshot_path:
        raise CaptureError("baseline snapshot cannot be overwritten")
    baseline = _load_baseline(baseline_path, book) if baseline_path else None
    baseline_documents = baseline["documents"] if baseline else {}
    canonical_documents = progress.book["documents"] if progress else {}
    if progress:
        for page in listing["pages"]:
            page_rows = documents[page["offset"] : page["offset"] + page["item_count"]]
            progress.observe_page(
                page["offset"],
                [
                    {"id": row["id"], "version": records[str(row["id"])]["version"]}
                    for row in page_rows
                ],
            )
        progress.phase(
            "listing", "completed", pages=listing["pages"], document_count=len(records)
        )
    if baseline and baseline_path is not None:
        for doc_id, previous in baseline_documents.items():
            current = records.get(doc_id)
            if current is None or _instant(current["version"]) != _instant(
                previous["version"]
            ):
                continue
            previous_export = (baseline.get("exports") or {}).get(doc_id, {})
            previous_raw = previous.get("raw") or previous_export.get("raw")
            if previous_raw:
                current_raw = _rebase_raw(previous_raw, baseline_path, snapshot_path)
                if current_raw is not None:
                    current["raw"] = current_raw
    changes = {"new": [], "updated": [], "renamed": [], "moved": [], "absent": []}
    if baseline:
        current_ids = {document["id"] for document in documents}
        changes["absent"] = sorted(
            int(value)
            for value in set(baseline_documents) - {str(item) for item in current_ids}
        )
        for document in documents:
            doc_id = str(document["id"])
            previous = baseline_documents.get(doc_id)
            if previous is None:
                changes["new"].append(document["id"])
                continue
            current_version = records[doc_id]["version"]
            previous_version = previous["version"]
            current_instant = _instant(current_version)
            previous_instant = _instant(previous_version)
            if current_instant < previous_instant:
                raise CaptureError(
                    f"document {document['id']} has a timestamp regression"
                )
            if current_instant > previous_instant:
                changes["updated"].append(document["id"])
            if document.get("title") != previous.get("title"):
                changes["renamed"].append(document["id"])
            if (document.get("toc_path") or []) != (previous.get("toc_path") or []):
                changes["moved"].append(document["id"])
    if progress and baseline is None:
        cutoff = max(
            (
                row.get("baseline", {}).get("filename_date", "")
                for row in canonical_documents.values()
                if row.get("baseline")
            ),
            default="",
        )
        for document in documents:
            doc_id = str(document["id"])
            previous = canonical_documents.get(doc_id)
            current = records[doc_id]["version"]
            existing_source = previous and (
                previous.get("baseline")
                or (previous.get("corpus") or {}).get("raw")
                or (previous.get("graph") or {}).get("raw")
                or (
                    previous.get("steps", {}).get("download", {}).get("status")
                    == "completed"
                    and previous["steps"]["download"].get("raw")
                )
            )
            if not existing_source:
                if _instant(current).date().isoformat() >= cutoff:
                    changes["new"].append(document["id"])
                continue
            initial = previous.get("baseline") or {}
            downloaded = previous.get("steps", {}).get("download", {})
            prior = (
                downloaded.get("version")
                or (previous.get("corpus") or {}).get("version")
                or initial.get("version")
            )
            if prior:
                if _instant(current) < _instant(prior):
                    raise CaptureError(f"document {doc_id} has a timestamp regression")
                if _instant(current) > _instant(prior):
                    changes["updated"].append(document["id"])
            elif _instant(current).date().isoformat() >= initial.get(
                "filename_date", ""
            ):
                changes["updated"].append(document["id"])
    current_id_set = {document["id"] for document in documents}
    prior_pending = (
        _load_pending(Path(args.pending_state).expanduser().resolve(), book)
        if args.pending_state
        else {}
    )
    if progress:
        prior_pending.update(
            {
                int(key): row.get("pending", [])
                for key, row in canonical_documents.items()
                if row.get("pending")
            }
        )
    known_ids = (
        current_id_set
        | {int(value) for value in baseline_documents}
        | {int(value) for value in canonical_documents}
    )
    unknown_pending = set(prior_pending) - known_ids
    if unknown_pending:
        raise CaptureError(
            f"pending state contains unknown document IDs: {sorted(unknown_pending)}"
        )
    selected = sorted(
        {
            doc_id
            for values in changes.values()
            for doc_id in values
            if doc_id in current_id_set
        }
        | (set(prior_pending) & current_id_set)
    )
    exports: dict[str, Any] = {}
    pending = []
    anomalies = []
    details_dir = (
        Path(args.details_dir).expanduser().resolve() if args.details_dir else None
    )
    checkpoint = _load_checkpoint(staging, book)
    by_id = {document["id"]: document for document in documents}
    for doc_id in selected:
        document = by_id[doc_id]
        previous_export = (
            (baseline.get("exports") or {}).get(str(doc_id), {}) if baseline else {}
        )
        reasons = []
        if doc_id in changes["new"]:
            original = {"status": "not_applicable"}
        elif (
            baseline_documents.get(str(doc_id), {}).get("raw")
            or previous_export.get("raw")
        ) and baseline_path is not None:
            original_raw = _rebase_raw(
                baseline_documents[str(doc_id)].get("raw") or previous_export["raw"],
                baseline_path,
                snapshot_path,
            )
            if original_raw is not None:
                original = {"status": "available", "raw": original_raw}
            else:
                original = {"status": "missing", "reason": "baseline_raw_missing"}
                reasons.append("baseline_raw_missing")
        else:
            original = {"status": "missing", "reason": "baseline_raw_missing"}
            reasons.append("baseline_raw_missing")
        labels = sorted(label for label, values in changes.items() if doc_id in values)
        checkpoint_row = checkpoint.get(str(doc_id))
        detail_matches_listing = False
        if isinstance(checkpoint_row, dict):
            try:
                detail_matches_listing = _instant(
                    checkpoint_row.get("detail_version") or {}
                ) == _instant(records[str(doc_id)]["version"])
            except CaptureError:
                detail_matches_listing = False
        reused = False
        saved_download = (
            canonical_documents.get(str(doc_id), {})
            .get("steps", {})
            .get("download", {})
        )
        saved_ref = saved_download.get("raw") or {}
        saved_path = Path(saved_ref.get("path", ""))
        if not saved_path.is_absolute() and progress:
            saved_path = progress.corpus / saved_path
        saved_version = saved_download.get("version")
        if (
            progress
            and saved_version
            and saved_path.is_file()
            and _instant(saved_version) == _instant(records[str(doc_id)]["version"])
        ):
            detail_version = saved_version
            raw = _raw_reference(
                saved_path, snapshot_path, saved_download["recorded_at"]
            )
            raw["adapter"] = "canonical_download"
            reused = True
        elif (
            isinstance(checkpoint_row, dict)
            and checkpoint_row.get("listing_version") == records[str(doc_id)]["version"]
            and detail_matches_listing
            and isinstance(checkpoint_row.get("raw_file"), str)
            and Path(checkpoint_row["raw_file"]).name == checkpoint_row["raw_file"]
            and (staging / "raw" / checkpoint_row["raw_file"]).is_file()
        ):
            detail_version = checkpoint_row["detail_version"]
            raw = _raw_reference(
                staging / "raw" / checkpoint_row["raw_file"],
                snapshot_path,
                checkpoint_row["captured_at"],
            )
            raw["adapter"] = checkpoint_row["adapter"]
            reused = True
        else:
            try:
                if args.listing:
                    if details_dir is None:
                        raise CaptureError(
                            f"document {doc_id} requires --details-dir in local adapter mode"
                        )
                    body, detail = _export_local(document, details_dir)
                else:
                    body, detail = _export_remote(document)
                detail_version, raw = _save_raw(
                    document,
                    {**detail, "body": body},
                    staging,
                    snapshot_path,
                    protected_inputs,
                )
                raw["adapter"] = (
                    "local_detail" if args.listing else "skylark_doc_detail"
                )
            except (CaptureError, OSError) as error:
                anomalies.append({"id": doc_id, "code": "export_failed"})
                reasons.extend(["export_failed", *prior_pending.get(doc_id, [])])
                pending.append({"id": doc_id, "reasons": reasons})
                exports[str(doc_id)] = {
                    "status": "failed",
                    "labels": labels,
                    "listing_version": records[str(doc_id)]["version"],
                    "original": original,
                    "error": str(error),
                }
                if progress:
                    progress.step(doc_id, "download", "failed", reason=str(error))
                    progress.update(doc_id, pending=sorted(set(reasons)))
                continue
            checkpoint[str(doc_id)] = {
                "raw_file": Path(raw["path"]).name,
                "captured_at": raw["captured_at"],
                "listing_version": records[str(doc_id)]["version"],
                "detail_version": detail_version,
                "adapter": raw["adapter"],
            }
            _save_checkpoint(staging, book, checkpoint)
        if progress:
            raw_path = Path(raw["path"])
            if not raw_path.is_absolute():
                raw_path = snapshot_path.parent / raw_path
            progress.step(
                doc_id,
                "download",
                "completed",
                version=detail_version,
                file=raw_path.name,
                raw=progress.reference(raw_path),
            )
            progress.update(
                doc_id,
                corpus={
                    "status": "captured",
                    "version": detail_version,
                    "raw": progress.reference(raw_path),
                },
                pending=sorted(
                    set(canonical_documents.get(str(doc_id), {}).get("pending", []))
                    | {"COMPARISON_PENDING"}
                ),
            )
        detail_instant = _instant(detail_version)
        listing_instant = _instant(records[str(doc_id)]["version"])
        if detail_instant != listing_instant:
            code = (
                "detail_newer_than_listing"
                if detail_instant > listing_instant
                else "detail_older_than_listing"
            )
            anomalies.append({"id": doc_id, "code": code})
            reasons.append(code)
        else:
            records[str(doc_id)]["raw"] = raw
        reasons.extend(prior_pending.get(doc_id, []))
        if reasons:
            pending.append({"id": doc_id, "reasons": reasons})
        exports[str(doc_id)] = {
            "status": "captured",
            "reused": reused,
            "labels": labels,
            "raw": raw,
            "listing_version": records[str(doc_id)]["version"],
            "detail_version": detail_version,
            "original": original,
        }
    for doc_id in sorted(set(prior_pending) - current_id_set):
        pending.append(
            {
                "id": doc_id,
                "reasons": [
                    "absent_from_current_listing",
                    *prior_pending[doc_id],
                ],
            }
        )
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "incomplete"
            if anomalies
            else "initialized"
            if baseline is None and progress is None
            else "captured"
        ),
        "book": book,
        "observation": {
            "started_at": started_at,
            "ended_at": _utc_now(),
            "timezone": "UTC+8",
        },
        "listing": listing,
        "baseline": (
            {
                "observation": baseline["observation"],
                "document_count": len(baseline_documents),
            }
            if baseline
            else None
        ),
        "changes": changes,
        "selected": selected,
        "counts": {
            "documents": len(records),
            **{label: len(values) for label, values in changes.items()},
            "selected": len(selected),
            "captured": sum(row["status"] == "captured" for row in exports.values()),
            "failed": sum(row["status"] == "failed" for row in exports.values()),
            "pending": len(pending),
            "anomalies": len(anomalies),
        },
        "documents": records,
        "exports": exports,
        "pending": pending,
        "anomalies": anomalies,
    }
    _write_json(snapshot_path, snapshot)
    print(
        f"status={snapshot['status']} documents={len(records)} "
        f"selected={len(selected)} captured={snapshot['counts']['captured']} "
        f"failed={snapshot['counts']['failed']} pending={len(pending)}"
    )
    return 1 if anomalies else 0
