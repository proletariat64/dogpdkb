#!/usr/bin/env python3
"""Apply a Yuque sync-updates delta: export and merge into the corpus.

Reads a sync-updates JSON produced by get_sync_updates.py, selects recent
additions plus all updates, renames and moves of tracked docs, then for each doc:

  1. exports the markdown body via the skylark MCP bridge
  2. rewrites SSO-gated image URLs to the public mirror
  3. merges the result into the corpus dir using the flatten-export
     filename convention `<toc-path>__<title>-<YYYY-MM-DD>-[doc_id].md`
     (stale local copies of updated docs are removed first)

OCR is no longer run here: it is a best-effort stage of the unified
``dogpdkb sync`` runner, and publication proceeds with the available raw export
when no OCR output exists. The ``run_ocr_with_resume`` utility remains for
direct callers.

Remote deletions are applied only from a complete snapshot of the same book;
old and deleted files are backed up in the staging directory.

Usage:
    python scripts/apply_sync_updates.py \
        -i graphify_flatten_ready \
        -s https://yuque.antfin.com/global-fund-platform/gom7xx \
        [--sync-json tmp/sync-updates-gom7xx-20260830.json] \
        [--staging wash/sync-20260830] [--cutoff 2026-08-07] [--no-merge]

Read-only toward Yuque; local writes only inside --staging and -i.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dogpdkb.instance_lock import locked_main, subprocess_lock_options
from dogpdkb.sync_progress import progress_for_config
from dogpdkb.sync_time import UTC8, parse_timestamp, timestamp_record

sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_sync_updates import (
    SkylarkError,
    call_tool,
    corpus_filename,
    resolve_book,
    safe_diagnostic,
    toc_paths,
)
from sync_formats import export_table, rewrite_image_urls

from mdocr.config import cli_path, config_arguments, configure, configured_book

LOCAL_NAME_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})-\[(\d+)\]\.md$")
VERSION_ANOMALIES = {
    "detail_version_missing",
    "listing_version_missing",
    "detail_newer_than_listing",
    "detail_older_than_listing",
    "detail_version_field_changed",
}


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def corpus_max_date(input_dir: Path) -> date:
    best: date | None = None
    for path in input_dir.glob("*.md"):
        match = LOCAL_NAME_RE.search(path.name)
        if match:
            stamp = parse_date(match.group(1))
            if best is None or stamp > best:
                best = stamp
    if best is None:
        raise SystemExit(f"no flatten-export filenames found in {input_dir}")
    return best


def latest_sync_json(output_dir: Path, book_slug: str | None = None) -> Path:
    pattern = f"sync-updates-{book_slug}-*.json" if book_slug else "sync-updates-*.json"
    candidates = sorted(output_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise SystemExit(f"no {pattern} found in {output_dir}")
    return candidates[-1]


def select_delta(sync: dict[str, Any], cutoff: date) -> list[dict[str, Any]]:
    """Select recent additions and all updates to documents already tracked locally."""
    delta: list[dict[str, Any]] = []
    for doc in sync.get("new", []):
        remote = doc.get("content_updated_at") or doc.get("created_at")
        stamp = parse_timestamp(remote).date().isoformat() if remote else ""
        if stamp >= cutoff.isoformat():
            delta.append({**doc, "kind": "new", "stamp": stamp})
    for kind in ("updated", "renamed", "moved"):
        for doc in sync.get(kind, []):
            remote = doc.get("content_updated_at")
            stamp = (
                parse_timestamp(remote).date().isoformat()
                if remote
                else doc["local_date"]
            )
            delta.append({**doc, "kind": kind, "stamp": stamp})
    seen = set()
    unique = []
    for doc in delta:
        if doc["id"] in seen:
            continue
        seen.add(doc["id"])
        unique.append(doc)
    return unique


def validate_sync_book(sync: dict[str, Any], book: dict[str, Any]) -> None:
    sync_book_id = (sync.get("book") or {}).get("book_id")
    if sync_book_id is None:
        raise ValueError("sync JSON is missing book.book_id")
    if int(sync_book_id) != int(book["book_id"]):
        raise ValueError("sync JSON and --skylark-url refer to a different book")


def _assert_deletion_source_unchanged(path: Path, record: dict[str, Any]) -> None:
    stat = path.stat()
    if ("size" in record and stat.st_size != int(record["size"])) or (
        "mtime_ns" in record and stat.st_mtime_ns != int(record["mtime_ns"])
    ):
        raise RuntimeError(f"local file changed since sync plan: {path}")


def plan_deletions(
    sync: dict[str, Any], input_dir: Path
) -> list[tuple[Path, dict[str, Any]]]:
    deleted = sync.get("deleted") or []
    if not deleted:
        return []
    snapshot = sync.get("snapshot") or {}
    raw_ids = snapshot.get("document_ids")
    if snapshot.get("complete") is not True or not isinstance(raw_ids, list):
        raise ValueError("deletions require a complete remote snapshot")
    try:
        remote_id_list = [int(value) for value in raw_ids]
    except (TypeError, ValueError) as exc:
        raise ValueError("remote snapshot contains an invalid document ID") from exc
    remote_ids = set(remote_id_list)
    if len(remote_ids) != len(remote_id_list):
        raise ValueError("remote snapshot contains duplicate remote document IDs")
    if snapshot.get("document_count") != len(remote_id_list):
        raise ValueError("remote snapshot document_count does not match document_ids")

    planned: list[tuple[Path, dict[str, Any]]] = []
    deleted_ids = set()
    for record in deleted:
        doc_id = int(record["id"])
        if doc_id in deleted_ids:
            raise ValueError(f"duplicate deletion for document {doc_id}")
        deleted_ids.add(doc_id)
        if doc_id in remote_ids:
            raise ValueError(
                f"deleted document {doc_id} is still present in remote snapshot"
            )
        local_file = str(record.get("local_file") or "")
        path = input_dir / local_file
        match = LOCAL_NAME_RE.search(path.name)
        if path.parent != input_dir or not match or int(match.group(2)) != doc_id:
            raise ValueError(
                f"invalid deletion filename for document {doc_id}: {local_file}"
            )
        candidates = [
            candidate
            for candidate in input_dir.glob("*.md")
            if (match := LOCAL_NAME_RE.search(candidate.name))
            and int(match.group(2)) == doc_id
        ]
        if len(candidates) > 1:
            raise RuntimeError(f"multiple local files for document {doc_id}")
        if candidates and candidates[0] != path:
            raise RuntimeError(f"local file changed since sync plan: {candidates[0]}")
        if path.exists():
            if not path.is_file():
                raise RuntimeError(f"deletion target is not a file: {path}")
            _assert_deletion_source_unchanged(path, record)
        planned.append((path, record))
    return planned


def remove_documents(
    planned: list[tuple[Path, dict[str, Any]]],
    backup_dir: Path,
    on_complete: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    result = {"deleted": 0, "already_absent": 0}
    for path, record in planned:
        if not path.exists():
            result["already_absent"] += 1
            if on_complete:
                on_complete(record)
            continue
        _assert_deletion_source_unchanged(path, record)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / path.name
        if backup.exists() and backup.read_bytes() != path.read_bytes():
            raise FileExistsError(f"deletion backup collision: {backup}")
        if not backup.exists():
            _atomic_copy(path, backup)
        path.unlink()
        result["deleted"] += 1
        if on_complete:
            on_complete(record)
    return result


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.sync-tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish_documents(
    exported: list[tuple[Path, dict[str, Any]]],
    ocr_dir: Path,
    input_dir: Path,
    backup_dir: Path,
    on_complete: Callable[[Path, dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    planned: list[tuple[Path, Path, list[Path], dict[str, Any]]] = []
    for raw_path, doc in exported:
        final_source = ocr_dir / raw_path.name
        if not final_source.is_file():
            final_source = raw_path
        if not final_source.is_file():
            raise FileNotFoundError(final_source)
        destination = input_dir / raw_path.name
        if destination.exists():
            match = LOCAL_NAME_RE.search(destination.name)
            if not match or int(match.group(2)) != int(doc["id"]):
                raise FileExistsError(f"document filename collision: {destination}")
        old = []
        for candidate in input_dir.glob("*.md"):
            match = LOCAL_NAME_RE.search(candidate.name)
            if match and int(match.group(2)) == int(doc["id"]):
                old.append(candidate)
        planned.append((final_source, destination, old, doc))

    assets = []
    assets_dir = ocr_dir / "assets"
    if assets_dir.is_dir():
        for source in sorted(assets_dir.rglob("*")):
            if not source.is_file():
                continue
            destination = input_dir / source.relative_to(ocr_dir)
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                raise FileExistsError(f"asset content collision: {destination}")
            assets.append((source, destination))

    backup_dir.mkdir(parents=True, exist_ok=True)
    old_documents = {old for _, _, old_paths, _ in planned for old in old_paths}
    for old in sorted(old_documents):
        backup = backup_dir / old.name
        if not backup.exists():
            _atomic_copy(old, backup)

    for source, destination in assets:
        if not destination.exists():
            _atomic_copy(source, destination)

    replaced = 0
    for final_source, destination, old_paths, doc in planned:
        _atomic_copy(final_source, destination)
        for old in old_paths:
            replaced += 1
            if old != destination:
                old.unlink()
        if on_complete:
            on_complete(destination, doc)
    return {"added": len(planned), "replaced": replaced, "assets": len(assets)}


def export_doc(
    doc_id: int, doc_format: str | None = None
) -> tuple[str, dict[str, Any]]:
    if str(doc_format or "").lower() in {"laketable", "table"}:
        return export_table(doc_id)
    data = call_tool("skylark_doc_detail", {"doc_id": doc_id, "format": "md"})
    if not isinstance(data, dict) or not isinstance(data.get("body"), str):
        raise SkylarkError(f"doc {doc_id}: invalid detail payload")
    return rewrite_image_urls(data["body"]), data


def document_version(document: dict[str, Any]) -> dict[str, Any] | None:
    listed = document.get("listing_version")
    if listed and listed.get("field") in {"content_updated_at", "updated_at"}:
        return timestamp_record(listed["value"], listed["field"])
    for field in ("content_updated_at", "updated_at"):
        if document.get(field):
            return timestamp_record(str(document[field]), field)
    return None


def run_mdocr(files: list[Path], out_dir: Path) -> int:
    if not files:
        return 0
    local_executable = Path(sys.executable).with_name("mdocr")
    executable = str(local_executable) if local_executable.is_file() else "mdocr"
    cmd = [
        executable,
        "ocr",
        "-o",
        str(out_dir),
        "--download",
        "--force",
        "-f",
        *[str(f) for f in files],
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, check=False, **subprocess_lock_options()
    )
    sys.stderr.write(proc.stderr)
    return proc.returncode


class OCRFailure(RuntimeError):
    def __init__(self, message: str, file: str) -> None:
        super().__init__(safe_diagnostic(message))
        self.file = file


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    temporary.replace(path)


def _write_failure_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Keep the original failure and retry output if diagnostic storage fails."""
    try:
        _write_json(path, manifest)
    except OSError as error:
        print(
            f"cannot persist failure manifest {path}: {safe_diagnostic(error)}",
            file=sys.stderr,
        )


def run_ocr_with_resume(
    files: list[Path],
    out_dir: Path,
    state_dir: Path,
    on_complete: Callable[[Path], None] | None = None,
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = state_dir / "ocr-complete.json"
    completed: set[str] = set()
    if checkpoint.exists():
        try:
            completed = set(
                json.loads(checkpoint.read_text(encoding="utf-8")).get("files", [])
            )
        except ValueError as error:
            raise RuntimeError(f"invalid OCR checkpoint: {checkpoint}") from error
    for path in files:
        output = out_dir / path.name
        if path.name in completed and output.is_file():
            if on_complete:
                on_complete(path)
            continue
        # Missing output invalidates the old completion before OCR can write a
        # partial replacement and fail or be interrupted.
        if path.name in completed:
            completed.remove(path.name)
            _write_json(checkpoint, {"files": sorted(completed)})
        try:
            rc = run_mdocr([path], out_dir)
        except (OSError, RuntimeError) as error:
            raise OCRFailure(
                f"mdocr failed for {path.name}: {error}", path.name
            ) from error
        if rc != 0:
            raise OCRFailure(f"mdocr exited {rc} for {path.name}", path.name)
        if not output.is_file():
            raise OCRFailure(f"mdocr did not write {output}", path.name)
        completed.add(path.name)
        temporary = checkpoint.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"files": sorted(completed)}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        temporary.replace(checkpoint)
        if on_complete:
            on_complete(path)


@locked_main
def main() -> None:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "apply sync-updates delta").split("\n")[0]
    )
    config_arguments(parser, book=True)
    parser.add_argument("-i", "--input", help="local md corpus dir")
    parser.add_argument("-s", "--skylark-url", help="Yuque book url")
    parser.add_argument(
        "--sync-json",
        type=cli_path,
        help="sync-updates JSON (default: newest in configured state/<book>/)",
    )
    parser.add_argument(
        "--staging",
        type=cli_path,
        default=None,
        help="staging dir (default: configured staging/<book>/sync-<today>)",
    )
    parser.add_argument(
        "--cutoff", help="YYYY-MM-DD delta cutoff (default: newest date in corpus)"
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="stop after staging; do not merge into -i",
    )
    args = parser.parse_args()

    config = configure(parser, args.config)
    source = configured_book(parser, config, args, args.skylark_url)
    args.skylark_url = source.url
    input_dir = cli_path(args.input) if args.input else source.corpus
    if not input_dir.is_dir():
        raise SystemExit(f"input dir not found: {input_dir}")

    book = resolve_book(args.skylark_url)
    book_slug = book["slug"] or str(book["book_id"])
    sync_path = args.sync_json or latest_sync_json(config.state / source.key, book_slug)
    try:
        sync = json.loads(sync_path.read_text(encoding="utf-8"))
        validate_sync_book(sync, book)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"cannot use sync json {sync_path}: {exc}") from exc
    cutoff = parse_date(args.cutoff) if args.cutoff else corpus_max_date(input_dir)
    delta = select_delta(sync, cutoff)
    try:
        deletions = plan_deletions(sync, input_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"cannot plan deletions: {exc}") from exc
    print(f"sync json: {sync_path}")
    print(f"cutoff: {cutoff} · delta docs: {len(delta)} · deletions: {len(deletions)}")
    staging = (
        args.staging
        or config.staging / source.key / f"sync-{datetime.now(UTC8):%Y%m%d}"
    )
    retry_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--config",
        str(config.file),
        "-i",
        str(input_dir),
        "-s",
        source.url,
        "--sync-json",
        str(sync_path),
        "--staging",
        str(staging),
        "--cutoff",
        cutoff.isoformat(),
    ]
    if args.no_merge:
        retry_command.append("--no-merge")

    def print_retry() -> None:
        print(f"Retry: {shlex.join(retry_command)}", file=sys.stderr)

    raw_dir = staging / "raw"
    ocr_dir = staging / "md"
    checkpoint = staging / "export-progress.json"
    source_identity = {
        "path": str(sync_path.resolve()),
        "mtime_ns": sync_path.stat().st_mtime_ns,
    }
    configuration = {
        "input_dir": str(input_dir.resolve()),
        "source_url": source.url,
        "cutoff": cutoff.isoformat(),
        "settings": config.snapshot(),
    }
    completed: dict[str, dict[str, Any]] = {}
    if checkpoint.exists():
        try:
            progress = json.loads(checkpoint.read_text(encoding="utf-8"))
        except ValueError as error:
            raise SystemExit(f"invalid export checkpoint: {checkpoint}") from error
        if progress.get("source") != source_identity:
            raise SystemExit(
                "staging belongs to a different sync plan; use a new staging dir"
            )
        if "configuration" in progress and progress["configuration"] != configuration:
            raise SystemExit("staging configuration changed; use a new staging dir")
        completed = progress.get("documents") or {}

    try:
        tracker = progress_for_config(config, source, input_dir)
        assert tracker is not None
        tracker.claim(staging, phase="corpus")
    except (OSError, ValueError) as error:
        print(f"[state] failed: {safe_diagnostic(error)}", file=sys.stderr)
        print_retry()
        raise SystemExit(1) from error

    raw_dir.mkdir(parents=True, exist_ok=True)
    ocr_dir.mkdir(parents=True, exist_ok=True)

    def save_progress(errors: list[dict[str, Any]]) -> None:
        temporary = checkpoint.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "source": source_identity,
                    "configuration": configuration,
                    "documents": completed,
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        temporary.replace(checkpoint)

    paths = {}
    if delta:
        print(f"Fetching TOC for book {book['book_id']} ...", file=sys.stderr)
        paths = toc_paths(book["book_id"])

    exported: list[tuple[Path, dict[str, Any]]] = []
    skipped: list[tuple[int, str]] = []
    errors: list[dict[str, Any]] = []
    export_cancellation: KeyboardInterrupt | None = None
    for i, doc in enumerate(delta, 1):
        doc_id = doc["id"]
        prior = completed.get(str(doc_id)) or {}
        if (
            prior.get("status") == "exported"
            and prior.get("source_version")
            and not prior.get("anomalies")
            and (raw_dir / prior.get("file", "")).is_file()
        ):
            restored = {
                **doc,
                "title": prior["title"],
                "remote_content_updated_at": prior["remote_content_updated_at"],
                "stamp": prior["stamp"],
                "source_version": prior["source_version"],
                "listing_version": prior.get("listing_version"),
            }
            exported.append((raw_dir / prior["file"], restored))
            tracker.step(
                str(doc_id),
                "download",
                "completed",
                file=prior["file"],
                version=prior["source_version"],
                listing_version=prior.get("listing_version"),
                anomalies=[],
                raw=tracker.reference(raw_dir / prior["file"]),
            )
            existing_corpus = tracker.book["documents"][str(doc_id)].get("corpus", {})
            if (
                existing_corpus.get("status") != "published"
                or existing_corpus.get("file") != prior["file"]
            ):
                tracker.update(
                    str(doc_id),
                    corpus={
                        "status": "captured",
                        "version": prior["source_version"],
                        "raw": tracker.reference(raw_dir / prior["file"]),
                    },
                )
            print(
                f"  [{i}/{len(delta)}] reuse {doc_id} {prior['title']}", file=sys.stderr
            )
            continue
        if prior.get("status") == "skipped_empty" and doc["kind"] == "new":
            skipped.append((doc_id, "empty document body"))
            continue
        if prior:
            completed.pop(str(doc_id), None)
            save_progress(errors)
        try:
            body, detail = export_doc(doc_id, doc.get("format"))
        except KeyboardInterrupt as error:
            export_cancellation = error
            skipped.append((doc_id, "cancelled"))
            errors.append({"id": doc_id, "reason": "cancelled"})
            break
        except SkylarkError as error:
            reason = safe_diagnostic(error)
            skipped.append((doc_id, reason))
            errors.append({"id": doc_id, "reason": reason})
            save_progress(errors)
            tracker.step(str(doc_id), "download", "failed", reason=reason)
            tracker.update(
                str(doc_id),
                pending=sorted(
                    set(tracker.book["documents"][str(doc_id)].get("pending", []))
                    | {"CAPTURE_EXPORT_FAILED"}
                ),
            )
            print(
                f"  [{i}/{len(delta)}] FAIL {doc_id}: {reason}",
                file=sys.stderr,
            )
            continue
        if not body.strip() and doc["kind"] == "new":
            skipped.append((doc_id, "empty document body"))
            completed[str(doc_id)] = {"status": "skipped_empty"}
            save_progress(errors)
            print(
                f"  [{i}/{len(delta)}] SKIP {doc_id} {doc['title']}: empty body",
                file=sys.stderr,
            )
            continue
        listing_version = document_version(doc)
        source_version = document_version(detail)
        anomalies = []
        if source_version is None:
            anomalies.append("detail_version_missing")
        elif listing_version is None:
            anomalies.append("listing_version_missing")
        else:
            detail_instant = parse_timestamp(source_version["utc"])
            listed_instant = parse_timestamp(listing_version["utc"])
            if detail_instant != listed_instant:
                anomalies.append(
                    "detail_newer_than_listing"
                    if detail_instant > listed_instant
                    else "detail_older_than_listing"
                )
            if source_version["field"] != listing_version["field"]:
                anomalies.append("detail_version_field_changed")
        remote_stamp = source_version["value"] if source_version else ""
        stamp = (
            parse_timestamp(remote_stamp).date().isoformat()
            if remote_stamp
            else doc["stamp"]
        )
        title = str(detail.get("title") or doc.get("title") or f"doc-{doc_id}")
        doc = {
            **doc,
            "title": title,
            "remote_content_updated_at": remote_stamp,
            "source_version": source_version,
            "listing_version": listing_version,
            "anomalies": anomalies,
            "stamp": stamp,
        }
        trail = paths.get(doc_id, [])
        naming_doc = {
            **doc,
            "title": title,
            "content_updated_at": parse_timestamp(remote_stamp).isoformat()
            if remote_stamp
            else "",
        }
        target = raw_dir / corpus_filename(naming_doc, trail, parse_date(doc["stamp"]))
        # A regenerated raw file invalidates OCR even when its filename is unchanged.
        # Invalidate first so interruption cannot leave a completed stale OCR result.
        (ocr_dir / target.name).unlink(missing_ok=True)
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(target)
        exported.append((target, doc))
        completed[str(doc_id)] = {
            "status": "exported",
            "file": target.name,
            "title": title,
            "remote_content_updated_at": remote_stamp,
            "source_version": source_version,
            "listing_version": listing_version,
            "anomalies": anomalies,
            "stamp": stamp,
        }
        if anomalies:
            errors.append(
                {"id": doc_id, "stage": "version", "reason": ", ".join(anomalies)}
            )
        save_progress(errors)
        tracker.step(
            str(doc_id),
            "download",
            "completed",
            file=target.name,
            version=source_version,
            listing_version=listing_version,
            anomalies=anomalies,
            raw=tracker.reference(target),
        )
        tracker.update(
            str(doc_id),
            corpus={
                "status": "captured",
                "version": source_version,
                "raw": tracker.reference(target),
            },
            pending=sorted(
                (
                    set(tracker.book["documents"][str(doc_id)].get("pending", []))
                    - {"CAPTURE_EXPORT_FAILED"}
                    - VERSION_ANOMALIES
                )
                | {"GRAPH_UPDATE_REQUIRED"}
                | set(anomalies)
            ),
        )
        print(
            f"  [{i}/{len(delta)}] ok {doc_id} {title} ({len(body)} chars)",
            file=sys.stderr,
        )

    manifest = {
        "status": "failed" if errors else "staged",
        "failures": [{"stage": "export", **error} for error in errors],
        "cutoff": cutoff.isoformat(),
        "staging": str(staging),
        "exported": [
            {
                "id": doc["id"],
                "kind": doc["kind"],
                "file": p.name,
                "title": doc["title"],
                "url": doc.get("url"),
                "content_updated_at": doc["remote_content_updated_at"],
                "source_version": doc.get("source_version"),
                "listing_version": doc.get("listing_version"),
                "anomalies": doc.get("anomalies", []),
            }
            for p, doc in exported
        ],
        "skipped": [{"id": i, "reason": r} for i, r in skipped],
        "deleted": [record for _, record in deletions],
    }
    manifest_path = staging / "manifest.json"
    if errors:
        _write_failure_manifest(manifest_path, manifest)
    else:
        _write_json(manifest_path, manifest)
    if any(error.get("stage") == "version" for error in errors):
        tracker.phase("download", "failed", reason="listing/detail version anomalies")
        print(
            "[version] downloaded sources have unresolved version anomalies; publication blocked",
            file=sys.stderr,
        )
        print_retry()
        raise SystemExit(1)
    if export_cancellation is not None:
        print(
            f"[export] cancelled doc_id={doc_id}; completed={len(exported)} "
            f"failed={len(errors)}; staging kept at {staging}; "
            "valid export/OCR checkpoints will be reused",
            file=sys.stderr,
        )
        print_retry()
        raise export_cancellation
    print(
        f"exported {len(exported)}, skipped {len(skipped)}; "
        "OCR is a best-effort stage of the unified sync runner",
        file=sys.stderr,
    )
    if errors:
        tracker.phase("download", "failed", failures=len(errors))
        print(
            f"[export] completed={len(exported)} failed={len(errors)}; "
            f"staging kept at {staging}; valid export/OCR checkpoints will be reused",
            file=sys.stderr,
        )
        print_retry()
        raise SystemExit(1)

    print(f"wrote {manifest_path}")
    if args.no_merge:
        print(f"--no-merge: staged in {staging}, not merged into {input_dir}")
        return

    try:

        def published(destination, doc):
            version = doc.get("source_version")
            raw = raw_dir / destination.name
            tracker.step(
                str(doc["id"]),
                "corpus",
                "completed",
                file=destination.name,
                version=version,
            )
            tracker.update(
                str(doc["id"]),
                corpus={
                    "status": "published",
                    "version": version,
                    "raw": tracker.reference(raw),
                    "file": destination.name,
                },
            )

        def removed(record):
            tracker.step(str(record["id"]), "corpus", "completed", deleted=True)
            tracker.update(
                str(record["id"]),
                corpus={"status": "deleted", "version": None},
                pending=sorted(
                    set(tracker.book["documents"][str(record["id"])].get("pending", []))
                    | {"GRAPH_DELETION_REQUIRED"}
                ),
            )

        result = publish_documents(
            exported, ocr_dir, input_dir, staging / "backup", on_complete=published
        )
        result.update(
            remove_documents(deletions, staging / "backup", on_complete=removed)
        )
    except (OSError, RuntimeError, KeyboardInterrupt, SystemExit) as error:
        tracker.phase("corpus", "failed", reason=safe_diagnostic(error) or "cancelled")
        manifest["status"] = "failed"
        manifest["failures"].append(
            {"stage": "publish", "reason": safe_diagnostic(error) or "cancelled"}
        )
        _write_failure_manifest(manifest_path, manifest)
        print(
            f"[publish] failed: {safe_diagnostic(error) or 'cancelled'}; "
            f"staging kept at {staging}; publication may be partial",
            file=sys.stderr,
        )
        print_retry()
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise SystemExit(1) from error
    manifest["status"] = "completed"
    manifest["published"] = result
    _write_json(manifest_path, manifest)
    tracker.phase("corpus", "completed", manifest=str(manifest_path))
    if not os.environ.get("DOGPDKB_RUN_STAGING"):
        tracker.finish()
    print(
        f"merged into {input_dir}: {result['added']} added, "
        f"{result['replaced']} stale copies replaced, "
        f"{result['deleted']} deleted, {result['assets']} assets verified"
    )


if __name__ == "__main__":
    main()
