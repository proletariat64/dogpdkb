"""ocr: best-effort recognition of this batch's planned images.

Consumes the plan stage's OCR work list (``ocr[].resources``) and never re-plans.
For each planned image it reuses mdocr processing (download/normalize) and the
PaddleOCR client, persists verified success results to a durable cache outside
wash, and writes ``prepared-jpeg-ocr-v1`` evidence to the canonical graph record
so the next batch's mdcompare can verify reuse. OCR is best-effort: a single
image failure is recorded and the others continue; a shared auth/quota cause
stops the batch; a bounded time budget keeps completed results and archives the
unfinished. OCR completion is never a prerequisite for compile.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from dogpdkb.knowledge_plan import _load_state, state_lock
from dogpdkb.knowledge_state import save_view
from dogpdkb.ocr_cache import (
    cache_root,
    document_evidence,
    persist_success,
    verify_reuse,
)
from dogpdkb.stages.plan import PLAN_SCHEMA
from dogpdkb.sync_results import (
    archive_anomalies,
    followup_id,
    load_followups,
    register_compile_followup,
)
from dogpdkb.sync_run import read_json
from dogpdkb.sync_stages import StageContext, StageFailed, StageResult
from dogpdkb.sync_time import now_iso
from dogpdkb.workflows.get_sync_updates import safe_diagnostic
from mdcompare.io import publish_reports
from mdocr.markdown import ImageRef
from mdocr.ocr import build_ocr_block
from mdocr.paddle import OcrAuthError, OcrSharedError, PaddleClient
from mdocr.prepare import DOWNLOAD_TIMEOUT, ImageError, ImageSkip, prepare_one

OCR_SCHEMA = "unified-ocr-1.0"


def _plan(context: StageContext) -> dict[str, Any]:
    path = context.require("plan", "plan")
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != PLAN_SCHEMA:
        raise StageFailed(f"plan has an unsupported schema: {path}")
    return payload


def _source_name(resource: dict[str, Any]) -> str:
    url = resource.get("url") or resource.get("resource_id") or ""
    name = Path(urlsplit(str(url)).path).name
    return name or "image.png"


def _original_bytes(
    resource: dict[str, Any], session: requests.Session
) -> tuple[bytes, str]:
    """Fetch the original image bytes (download is not a billable OCR call)."""
    local = resource.get("path")
    if isinstance(local, str) and local and Path(local).is_file():
        path = Path(local)
        return path.read_bytes(), path.name
    url = str(resource.get("url") or resource.get("resource_id"))
    response = session.get(url, timeout=DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    return response.content, _source_name(resource)


def _prepare(
    context: StageContext, doc_id: Any, data: bytes, name: str
) -> tuple[Path, Path, bytes]:
    """Normalize the original into a content-named prepared JPEG via mdocr.

    The original is stored under a content-addressed subdirectory so two images
    that share a basename but differ in content cannot overwrite each other's
    bytes before deferred anomaly archiving copies them for recovery (M15). The
    basename is preserved so mdocr's prepared-asset name (the OCR cache key) is
    unchanged. Returns ``(original_path, prepared_path, prepared_bytes)``.
    """
    src_dir = context.directory / "src" / str(doc_id)
    original_dir = src_dir / hashlib.sha256(data).hexdigest()[:8]
    original_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    original_path = original_dir / name
    original_path.write_bytes(data)
    prepared_dir = context.directory / "prepared"
    ref = ImageRef(
        alt="",
        target=f"{original_dir.name}/{name}",
        title=None,
        is_remote=False,
        unsupported=False,
    )
    prepared = prepare_one(
        ref,
        src_dir / "doc.md",
        prepared_dir,
        download=False,
    )
    return original_path, prepared.path, prepared.path.read_bytes()


def _merge_graph_ocr(
    state: dict[str, Any],
    doc_id: str,
    images: list[dict[str, Any]],
    tombstones: list[dict[str, Any]] = (),
) -> None:
    record = state["documents"].setdefault(
        doc_id,
        {
            "observation": None,
            "corpus": {"status": "not_captured", "version": None, "raw": None},
            "graph": {},
            "pending": [],
        },
    )
    graph = record.setdefault("graph", {})
    existing = graph.get("ocr")
    merged: dict[str, dict[str, Any]] = {}
    if isinstance(existing, dict) and isinstance(existing.get("images"), list):
        for row in existing["images"]:
            if isinstance(row, dict) and isinstance(row.get("resource_id"), str):
                merged[row["resource_id"]] = row
    for row in images:
        merged[row["resource_id"]] = row
    # A planned image that did not succeed this batch must not keep exposing its
    # previous success: tombstone it so compile keeps the reference and records a
    # missing item instead of publishing stale text for changed content.
    for row in tombstones:
        merged[row["resource_id"]] = row
    graph["ocr"] = document_evidence(list(merged.values()))
    record.setdefault("steps", {})["ocr"] = {
        "status": "partial" if tombstones else "completed",
        "recorded_at": now_iso(),
    }


def handle(context: StageContext) -> StageResult:
    plan = _plan(context)
    items = plan.get("ocr") or []
    config = context.config
    root = cache_root(config, context.book.key)
    deadline = time.monotonic() + float(config.ocr_budget_seconds)

    # Per-document accumulators, in plan order, so the reference keeps a row for
    # every planned document even when none of its images could be recognized.
    accs: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for item in items:
        doc_id = item["document_id"]
        if doc_id not in accs:
            accs[doc_id] = {
                "item": item,
                "images": [],
                "final_inputs": [],
                "missing": [],
            }
            order.append(doc_id)
    work = [
        (item, resource)
        for item in items
        for resource in (item.get("resources") or [])
        if resource.get("kind") == "image"
    ]

    counts = {
        "total": len(work),
        "success": 0,
        "reused": 0,
        "failed": 0,
        "unattempted": 0,
    }
    fail_anomalies: list[dict[str, Any]] = []
    stop_anomalies: list[dict[str, Any]] = []
    batch_cause: dict[str, Any] | None = None
    stop_index = len(work)
    # Recovery bytes for the image that TRIGGERS a shared auth/quota stop. It was
    # already downloaded+prepared, so capture them in the OcrSharedError handler
    # (where the paths are valid for that image). The budget stop breaks before
    # download, so it leaves this empty and never borrows a prior image's paths.
    stop_inputs: list[dict[str, Any]] = []
    token = os.environ.get("PADDLEOCR_TOKEN", "").strip()
    session = requests.Session()
    client = PaddleClient(token, session=session) if token else None

    for index, (item, resource) in enumerate(work):
        acc = accs[item["document_id"]]
        resource_id = resource["resource_id"]
        if time.monotonic() >= deadline:
            batch_cause = {
                "stage": "ocr",
                "code": "OCR_BUDGET_EXHAUSTED",
                "reason": "OCR time budget exhausted",
            }
            stop_index = index
            break
        original_path: Path | None = None
        prepared_path: Path | None = None
        try:
            data, name = _original_bytes(resource, session)
            original_path, prepared_path, prepared_bytes = _prepare(
                context, item["document_id"], data, name
            )
            key = prepared_path.stem
            record = verify_reuse(
                root,
                key,
                resource_id=resource_id,
                original_bytes=data,
                prepared_bytes=prepared_bytes,
            )
            reused = record is not None
            if not reused:
                if client is None:
                    raise OcrAuthError("PADDLEOCR_TOKEN is not available")
                markdown = client.convert(prepared_path)
                if not markdown.strip():
                    raise ImageError("OCR result was blank")
                artifact_text = build_ocr_block("prepared.jpg", markdown)
                record = persist_success(
                    root,
                    key,
                    resource_id=resource_id,
                    original_name="original" + (Path(name).suffix or ".png"),
                    original_bytes=data,
                    prepared_bytes=prepared_bytes,
                    artifact_text=artifact_text,
                )
        except OcrSharedError as error:
            # A shared auth/quota cause stops the batch's remaining OCR.
            batch_cause = {
                "stage": "ocr",
                "code": error.code,
                "reason": safe_diagnostic(error),
            }
            stop_index = index
            stop_inputs = _image_inputs(original_path, prepared_path)
            context.log.write(f"ocr batch stopped: {error.code}")
            break
        except (ImageError, ImageSkip, OSError, RuntimeError, ValueError) as error:
            counts["failed"] += 1
            reason = safe_diagnostic(error)
            acc["missing"].append(
                {
                    "kind": "ocr_artifact",
                    "resource_id": resource_id,
                    "code": "OCR_FAILED",
                    "reason": reason,
                }
            )
            fail_anomalies.append(
                _anomaly(
                    item,
                    resource,
                    "OCR_FAILED",
                    reason,
                    necessary_inputs=_image_inputs(original_path, prepared_path),
                )
            )
            context.log.write(f"ocr failed for {resource_id}: {reason}")
            context.count(**counts)
            continue
        counts["reused" if reused else "success"] += 1
        acc["images"].append(record)
        artifact_ref = {
            "kind": "ocr_artifact",
            "resource_id": resource_id,
            "path": record["artifact"],
        }
        acc["final_inputs"].append(artifact_ref)
        context.count(**counts)

    # Everything from the stop onward was not executed: archive it as unfinished
    # under one shared batch cause, never counted as success.
    if batch_cause is not None:
        for index in range(stop_index, len(work)):
            item, resource = work[index]
            acc = accs[item["document_id"]]
            counts["unattempted"] += 1
            acc["missing"].append(
                {
                    "kind": "ocr_artifact",
                    "resource_id": resource["resource_id"],
                    "code": batch_cause["code"],
                    "reason": batch_cause["reason"],
                }
            )
            stop_anomalies.append(
                _anomaly(
                    item,
                    resource,
                    batch_cause["code"],
                    batch_cause["reason"],
                    # Only the triggering image (index == stop_index) has bytes;
                    # the rest were never downloaded. The budget stop has none.
                    necessary_inputs=stop_inputs if index == stop_index else [],
                )
            )
        context.count(**counts)

    documents = [
        {
            "book_id": accs[doc_id]["item"].get("book_id"),
            "document_id": doc_id,
            "source_version": accs[doc_id]["item"].get("source_version"),
            "ocr": document_evidence(accs[doc_id]["images"]),
            "execution": {
                "final_inputs": accs[doc_id]["final_inputs"],
                "missing_items": accs[doc_id]["missing"],
                "completed_at": now_iso(),
                "status": "completed" if not accs[doc_id]["missing"] else "partial",
            },
        }
        for doc_id in order
    ]

    # Prior published SUCCESS contributions this batch invalidates (tombstones),
    # captured per document before the merge overwrites them. A body-reuse source
    # whose published OCR text is invalidated must republish to drop it (H2).
    invalidated_success: dict[str, list[str]] = {}
    if documents:
        state_path = context.corpus / ".sync-state.json"
        with state_lock(state_path):
            state = _load_state(state_path, plan["book"])
            for row in documents:
                acc = accs[row["document_id"]]
                doc_key = str(row["document_id"])
                tombstones = [
                    {
                        "resource_id": m["resource_id"],
                        "status": "failed" if m["code"] == "OCR_FAILED" else "pending",
                    }
                    for m in acc["missing"]
                ]
                prior_record = state["documents"].get(doc_key) or {}
                prior_ocr = (prior_record.get("graph") or {}).get("ocr") or {}
                prior_success = {
                    img["resource_id"]
                    for img in prior_ocr.get("images") or []
                    if isinstance(img, dict)
                    and img.get("status") == "success"
                    and isinstance(img.get("resource_id"), str)
                }
                invalidated_ids = sorted(
                    prior_success & {m["resource_id"] for m in acc["missing"]}
                )
                if invalidated_ids:
                    invalidated_success[doc_key] = invalidated_ids
                if row["ocr"]["images"] or tombstones:
                    _merge_graph_ocr(
                        state,
                        doc_key,
                        row["ocr"]["images"],
                        tombstones,
                    )
            save_view(state_path, state)

    # A body-reuse source keeps its published body knowledge, but an OCR artifact
    # the published source does not yet contain changes its compile input. Register
    # follow-up compile work (the same machinery the post-hoc backfill path uses)
    # so this batch's compile stage republishes the source with the new image text.
    # The delta is measured against the inputs of the consumed (done) follow-up at
    # this version, NOT against whether this run made a fresh request: a cache hit
    # on resume after an interrupted persist must still register when the published
    # source lacks the artifact. A pending follow-up already covers the work.
    # A prior published SUCCESS this batch tombstoned (invalidated) also forces a
    # whole-source republish even with no new artifact, so the stale OCR text is
    # dropped rather than kept under the advanced body-reuse version (H2).
    body_reuse_ids = {str(item["document_id"]) for item in plan.get("body_reuse") or []}
    registry = load_followups(config, context.book.key)
    for doc_id in order:
        acc = accs[doc_id]
        doc_key = str(doc_id)
        if doc_key not in body_reuse_ids:
            continue
        invalidated = invalidated_success.get(doc_key)
        if not acc["final_inputs"] and not invalidated:
            continue
        source_version = acc["item"].get("source_version")
        existing = registry["followups"].get(
            followup_id(context.book.key, doc_id, source_version)
        )
        if existing is not None and existing.get("status") == "pending":
            continue
        published = (
            {row["path"] for row in existing.get("inputs", [])}
            if existing is not None and existing.get("status") == "done"
            else set()
        )
        changed = [row for row in acc["final_inputs"] if row["path"] not in published]
        if not changed and not invalidated:
            continue
        register_compile_followup(
            config,
            context.book.key,
            document_id=doc_id,
            source_version=source_version,
            reason=(
                "OCR changed a body-reuse source's compile input"
                if changed
                else "OCR contribution invalidated; republish to drop stale text"
            ),
            inputs=changed,
        )

    reference = {
        "schema_version": OCR_SCHEMA,
        "book": plan["book"],
        "documents": documents,
        "counts": dict(counts),
    }
    if batch_cause is not None:
        reference["batch_cause"] = dict(batch_cause)
    reference_path = context.directory / "ocr.json"
    reference_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    publish_reports(
        [(reference_path, json.dumps(reference, ensure_ascii=False, indent=1) + "\n")]
    )

    archived: list[dict[str, Any]] = []
    try:
        if fail_anomalies:
            archived += archive_anomalies(context, fail_anomalies)
        if stop_anomalies:
            archived += archive_anomalies(
                context, stop_anomalies, batch_cause=batch_cause
            )
    except (OSError, ValueError) as error:
        context.log.write(f"anomaly archiving failed: {safe_diagnostic(error)}")
        archived = fail_anomalies + stop_anomalies

    status = "completed"
    if counts["total"] == 0:
        status = "skipped"
    elif counts["failed"] or counts["unattempted"]:
        status = "partial"
    return StageResult(
        status=status,
        counts=counts,
        artifacts={"ocr": context.layout.relative(reference_path)},
        inputs={"plan": context.layout.relative(context.require("plan", "plan"))},
        anomalies=archived,
        reason=None
        if status in {"completed", "skipped"}
        else "OCR incomplete for some images",
    )


def _image_inputs(
    original_path: Path | None, prepared_path: Path | None
) -> list[dict[str, Any]]:
    """Reference whatever original/prepared bytes exist so cleanup preserves them."""
    inputs: list[dict[str, Any]] = []
    if original_path is not None and original_path.is_file():
        inputs.append({"kind": "original_image", "path": str(original_path)})
    if prepared_path is not None and prepared_path.is_file():
        inputs.append({"kind": "prepared_image", "path": str(prepared_path)})
    return inputs


def _anomaly(
    item: dict[str, Any],
    resource: dict[str, Any],
    code: str,
    reason: str,
    necessary_inputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "stage": "ocr",
        "book_id": item.get("book_id"),
        "document_id": item["document_id"],
        "source_version": item.get("source_version"),
        "code": code,
        "reason": reason,
        "resource_id": resource["resource_id"],
        "image": {"resource_id": resource["resource_id"], "url": resource.get("url")},
        "necessary_inputs": list(necessary_inputs or []),
    }


HANDLER = handle
