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
)
from dogpdkb.sync_results import archive_anomalies
from dogpdkb.sync_run import read_json
from dogpdkb.sync_stages import StageContext, StageFailed, StageResult
from dogpdkb.sync_time import now_iso
from dogpdkb.workflows.get_sync_updates import safe_diagnostic
from mdcompare.io import publish_reports
from mdocr.markdown import ImageRef
from mdocr.ocr import build_ocr_block
from mdocr.paddle import PaddleClient
from mdocr.prepare import DOWNLOAD_TIMEOUT, ImageError, ImageSkip, prepare_one

OCR_SCHEMA = "unified-ocr-1.0"
PLAN_SCHEMA = "unified-plan-1.0"


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
) -> tuple[Path, bytes]:
    """Normalize the original into a content-named prepared JPEG via mdocr."""
    src_dir = context.directory / "src" / str(doc_id)
    src_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (src_dir / name).write_bytes(data)
    prepared_dir = context.directory / "prepared"
    ref = ImageRef(alt="", target=name, title=None, is_remote=False, unsupported=False)
    prepared = prepare_one(
        ref,
        src_dir / "doc.md",
        prepared_dir,
        download=False,
    )
    return prepared.path, prepared.path.read_bytes()


def _merge_graph_ocr(
    state: dict[str, Any], doc_id: str, images: list[dict[str, Any]]
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
    graph["ocr"] = document_evidence(list(merged.values()))
    record.setdefault("steps", {})["ocr"] = {
        "status": "completed",
        "recorded_at": now_iso(),
    }


def handle(context: StageContext) -> StageResult:
    plan = _plan(context)
    items = plan.get("ocr") or []
    config = context.config
    root = cache_root(config, context.book.key)
    deadline = time.monotonic() + float(config.ocr_budget_seconds)

    counts = {"total": 0, "success": 0, "reused": 0, "failed": 0, "unattempted": 0}
    documents: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    token = os.environ.get("PADDLEOCR_TOKEN", "").strip()
    session = requests.Session()
    client = PaddleClient(token, session=session) if token else None

    for item in items:
        doc_id = item["document_id"]
        version = item.get("source_version")
        images: list[dict[str, Any]] = []
        final_inputs: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for resource in item.get("resources") or []:
            if resource.get("kind") != "image":
                continue
            counts["total"] += 1
            resource_id = resource["resource_id"]
            if time.monotonic() >= deadline or client is None:
                counts["unattempted"] += 1
                missing.append(
                    {"kind": "ocr_artifact", "resource_id": resource_id,
                     "code": "OCR_BUDGET_EXHAUSTED" if client else "OCR_AUTH_UNAVAILABLE",
                     "reason": "OCR not attempted"}
                )
                anomalies.append(
                    _anomaly(item, resource, "OCR_UNATTEMPTED", "OCR not attempted")
                )
                continue
            try:
                data, name = _original_bytes(resource, session)
                prepared_path, prepared_bytes = _prepare(context, doc_id, data, name)
                markdown = client.convert(prepared_path)
                if not markdown.strip():
                    raise ImageError("OCR result was blank")
                key = prepared_path.stem
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
            except (ImageError, ImageSkip, OSError, RuntimeError, ValueError) as error:
                counts["failed"] += 1
                reason = safe_diagnostic(error)
                missing.append(
                    {"kind": "ocr_artifact", "resource_id": resource_id,
                     "code": "OCR_FAILED", "reason": reason}
                )
                anomalies.append(_anomaly(item, resource, "OCR_FAILED", reason))
                context.log.write(f"ocr failed for {resource_id}: {reason}")
                continue
            counts["success"] += 1
            images.append(record)
            final_inputs.append(
                {"kind": "ocr_artifact", "resource_id": resource_id,
                 "path": record["artifact"]}
            )
            context.count(**counts)
        documents.append(
            {
                "book_id": item.get("book_id"),
                "document_id": doc_id,
                "source_version": version,
                "ocr": document_evidence(images),
                "execution": {
                    "final_inputs": final_inputs,
                    "missing_items": missing,
                    "completed_at": now_iso(),
                    "status": "completed" if not missing else "partial",
                },
            }
        )

    if documents:
        state_path = context.corpus / ".sync-state.json"
        with state_lock(state_path):
            state = _load_state(state_path, plan["book"])
            for row in documents:
                if row["ocr"]["images"]:
                    _merge_graph_ocr(state, str(row["document_id"]), row["ocr"]["images"])
            save_view(state_path, state)

    reference = {
        "schema_version": OCR_SCHEMA,
        "book": plan["book"],
        "documents": documents,
        "counts": dict(counts),
    }
    reference_path = context.directory / "ocr.json"
    reference_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    publish_reports(
        [(reference_path, json.dumps(reference, ensure_ascii=False, indent=1) + "\n")]
    )

    archived: list[dict[str, Any]] = []
    if anomalies:
        try:
            archived = archive_anomalies(context, anomalies)
        except (OSError, ValueError) as error:
            context.log.write(f"anomaly archiving failed: {safe_diagnostic(error)}")
            archived = anomalies

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
        reason=None if status in {"completed", "skipped"} else "OCR incomplete for some images",
    )


def _anomaly(
    item: dict[str, Any], resource: dict[str, Any], code: str, reason: str
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
        "necessary_inputs": [],
    }


HANDLER = handle