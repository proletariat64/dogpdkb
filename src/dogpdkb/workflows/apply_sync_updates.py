#!/usr/bin/env python3
"""Apply a Yuque sync-updates delta: export, OCR (mdocr), merge into corpus.

Reads a sync-updates JSON produced by get_sync_updates.py, selects recent
additions plus all updates, renames and moves of tracked docs, then for each doc:

  1. exports the markdown body via the skylark MCP bridge
  2. rewrites SSO-gated image URLs to the public mirror
  3. runs `mdocr ocr --download` so image text becomes queryable
  4. merges the result into the corpus dir using the flatten-export
     filename convention `<toc-path>__<title>-<YYYY-MM-DD>-[doc_id].md`
     (stale local copies of updated docs are removed first)

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
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_sync_updates import (
    SkylarkError,
    call_tool,
    corpus_filename,
    load_sync_state,
    resolve_book,
    safe_diagnostic,
    save_sync_state,
    toc_paths,
)
from sync_formats import export_table, rewrite_image_urls

from mdocr.config import cli_path, config_arguments, configure, configured_book

LOCAL_NAME_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})-\[(\d+)\]\.md$")


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def corpus_max_date(input_dir: Path) -> date:
    best: date | None = None
    for path in input_dir.rglob("*.md"):
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
        stamp = (doc.get("content_updated_at") or doc.get("created_at") or "")[:10]
        if stamp >= cutoff.isoformat():
            delta.append({**doc, "kind": "new", "stamp": stamp})
    for kind in ("updated", "renamed", "moved"):
        for doc in sync.get(kind, []):
            stamp = (doc.get("content_updated_at") or doc["local_date"])[:10]
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
    planned: list[tuple[Path, dict[str, Any]]], backup_dir: Path
) -> dict[str, int]:
    result = {"deleted": 0, "already_absent": 0}
    for path, record in planned:
        if not path.exists():
            result["already_absent"] += 1
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
) -> dict[str, int]:
    planned: list[tuple[Path, Path, list[Path]]] = []
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
        planned.append((final_source, destination, old))

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
    old_documents = {old for _, _, old_paths in planned for old in old_paths}
    for old in sorted(old_documents):
        backup = backup_dir / old.name
        if not backup.exists():
            _atomic_copy(old, backup)

    for source, destination in assets:
        if not destination.exists():
            _atomic_copy(source, destination)

    replaced = 0
    for final_source, destination, old_paths in planned:
        _atomic_copy(final_source, destination)
        for old in old_paths:
            replaced += 1
            if old != destination:
                old.unlink()
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
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
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


def run_ocr_with_resume(files: list[Path], out_dir: Path, state_dir: Path) -> None:
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
        args.staging or config.staging / source.key / f"sync-{date.today():%Y%m%d}"
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
    raw_dir.mkdir(parents=True, exist_ok=True)
    ocr_dir.mkdir(parents=True, exist_ok=True)
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
    for i, doc in enumerate(delta, 1):
        doc_id = doc["id"]
        prior = completed.get(str(doc_id)) or {}
        if (
            prior.get("status") == "exported"
            and (raw_dir / prior.get("file", "")).is_file()
        ):
            restored = {
                **doc,
                "title": prior["title"],
                "remote_content_updated_at": prior["remote_content_updated_at"],
                "stamp": prior["stamp"],
            }
            exported.append((raw_dir / prior["file"], restored))
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
        except SkylarkError as error:
            reason = safe_diagnostic(error)
            skipped.append((doc_id, reason))
            errors.append({"id": doc_id, "reason": reason})
            save_progress(errors)
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
        remote_stamp = str(
            detail.get("content_updated_at") or doc.get("content_updated_at") or ""
        )
        stamp = parse_date((remote_stamp or doc["stamp"])[:10]).isoformat()
        title = str(detail.get("title") or doc.get("title") or f"doc-{doc_id}")
        doc = {
            **doc,
            "title": title,
            "remote_content_updated_at": remote_stamp,
            "stamp": stamp,
        }
        trail = paths.get(doc_id, [])
        naming_doc = {**doc, "title": title, "content_updated_at": remote_stamp}
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
            "stamp": stamp,
        }
        save_progress(errors)
        print(
            f"  [{i}/{len(delta)}] ok {doc_id} {title} ({len(body)} chars)",
            file=sys.stderr,
        )

    print(
        f"exported {len(exported)}, skipped {len(skipped)}; running mdocr ocr ...",
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
            }
            for p, doc in exported
        ],
        "skipped": [{"id": i, "reason": r} for i, r in skipped],
        "deleted": [record for _, record in deletions],
    }
    manifest_path = staging / "manifest.json"
    _write_json(manifest_path, manifest)
    try:
        run_ocr_with_resume([p for p, _ in exported], ocr_dir, staging / "checkpoints")
    except (OSError, RuntimeError, KeyboardInterrupt) as error:
        failure = {"stage": "ocr", "reason": safe_diagnostic(error) or "cancelled"}
        if isinstance(error, OCRFailure):
            failure["file"] = error.file
        manifest["status"] = "failed"
        manifest["failures"].append(failure)
        _write_json(manifest_path, manifest)
        print(
            f"[ocr] {failure['reason']}; staging kept at {staging}; valid checkpoints will be reused",
            file=sys.stderr,
        )
        print_retry()
        if isinstance(error, KeyboardInterrupt):
            raise
        raise SystemExit(1) from error
    print(f"wrote {manifest_path}")

    if errors:
        print(
            f"[export] completed={len(exported)} failed={len(errors)}; "
            f"staging kept at {staging}; valid export/OCR checkpoints will be reused",
            file=sys.stderr,
        )
        print_retry()
        raise SystemExit(1)

    if args.no_merge:
        print(f"--no-merge: staged in {staging}, not merged into {input_dir}")
        return

    try:
        result = publish_documents(exported, ocr_dir, input_dir, staging / "backup")
        result.update(remove_documents(deletions, staging / "backup"))
        state = load_sync_state(input_dir)
        for path, doc in exported:
            if doc["remote_content_updated_at"]:
                state[str(doc["id"])] = {
                    "file": path.name,
                    "content_updated_at": doc["remote_content_updated_at"],
                }
        for _, record in deletions:
            state.pop(str(record["id"]), None)
        save_sync_state(input_dir, state)
    except (OSError, RuntimeError, KeyboardInterrupt) as error:
        manifest["status"] = "failed"
        manifest["failures"].append(
            {"stage": "publish", "reason": safe_diagnostic(error) or "cancelled"}
        )
        _write_json(manifest_path, manifest)
        print(
            f"[publish] failed; staging kept at {staging}; publication may be partial",
            file=sys.stderr,
        )
        print_retry()
        if isinstance(error, KeyboardInterrupt):
            raise
        raise SystemExit(1) from error
    manifest["status"] = "completed"
    manifest["published"] = result
    _write_json(manifest_path, manifest)
    print(
        f"merged into {input_dir}: {result['added']} added, "
        f"{result['replaced']} stale copies replaced, "
        f"{result['deleted']} deleted, {result['assets']} assets verified"
    )


if __name__ == "__main__":
    main()
