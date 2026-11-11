"""plan: automatic knowledge plan consuming the mdcompare batch report.

Replaces the superseded human-approval / semantic-quality contract. The plan
never re-compares and never calls OCR or Graphify; it only reads the mdcompare
report and decides, per document:

* body reuse — keep the existing body knowledge when the body is verified equal;
* compile — new / body-delta / missing-old / uncertain (REVIEW/UNKNOWN/HOLD)
  sources auto-enter the full-document compile list when current raw is available;
* OCR — image resources the comparator marked processing_required, independent
  of body reuse (identical body with changed images still goes to OCR);
* metadata maintenance — rename / move / source-ref changes, in this same plan.

A current input that is unavailable is archived as an anomaly through ticket 03's
durable API (never fabricated). Same-version archived failures are suppressed so
the daily batch does not retry them; a new version is processed normally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dogpdkb.knowledge_plan import (
    _load_state,
    _resolve_raw,
    _write_plan,
    state_lock,
)
from dogpdkb.sync_results import (
    archive_anomalies,
    suppressing_anomalies,
    version_stamp,
)
from dogpdkb.sync_run import read_json
from dogpdkb.sync_stages import StageContext, StageFailed, StageResult
from dogpdkb.sync_time import now_iso
from dogpdkb.workflows.get_sync_updates import safe_diagnostic

PLAN_SCHEMA = "unified-plan-1.0"

#: Body statuses/actions that keep the existing knowledge without recompiling.
REUSE_ACTION = "KEEP_EXISTING"
REUSE_STATUS = "REUSE"


def _report(context: StageContext) -> dict[str, Any]:
    path = context.require("mdcompare", "report")
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "unified-mdcompare-1.0"
    ):
        raise StageFailed(f"mdcompare report has an unsupported schema: {path}")
    return payload


def _suppressed_keys(context: StageContext) -> dict[tuple[str, str], dict[str, Any]]:
    """(document_id, version_stamp) -> archived failure the batch must not retry."""
    keys: dict[tuple[str, str], dict[str, Any]] = {}
    for record in suppressing_anomalies(context.config, context.book.key):
        keys[(str(record["document_id"]), record["version_stamp"])] = record
    return keys


def _ocr_resources(row: dict[str, Any], required: list[str]) -> list[dict[str, Any]]:
    wanted = set(required)
    return [
        {
            key: entry[key]
            for key in ("resource_id", "url", "kind", "path")
            if key in entry
        }
        for entry in row.get("resources") or []
        if entry.get("resource_id") in wanted
    ]


def _metadata_tasks(row: dict[str, Any]) -> list[dict[str, Any]]:
    comparison = row.get("comparison") or {}
    changed = (comparison.get("metadata") or {}).get("changed_fields") or []
    labels = set(row.get("labels") or [])
    tasks = []
    for field in changed:
        tasks.append({"field": field, "reason": "METADATA_FIELD_CHANGED"})
    for label, field in (("renamed", "name"), ("moved", "logical_path")):
        if label in labels and not any(t["field"] == field for t in tasks):
            tasks.append({"field": field, "reason": f"DOCUMENT_{label.upper()}"})
    return tasks


def _necessary_inputs(
    context: StageContext, row: dict[str, Any]
) -> list[dict[str, str]]:
    reference = row.get("raw")
    if isinstance(reference, dict):
        path = _resolve_raw(reference, context.directory / "plan.json")
        if path is not None and path.is_file():
            return [{"kind": "raw", "path": str(path)}]
    return []


def _decide(
    context: StageContext,
    report: dict[str, Any],
    suppressed: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    book_id = report["book"]["book_id"]
    body_reuse: list[dict[str, Any]] = []
    compile_items: list[dict[str, Any]] = []
    ocr_items: list[dict[str, Any]] = []
    metadata_items: list[dict[str, Any]] = []
    handed_off: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    decisions: dict[str, dict[str, Any]] = {}

    for row in report["documents"]:
        doc_id = row["document_id"]
        version = row["source_version"]
        stamp = version_stamp(version)
        base = {
            "book_id": book_id,
            "document_id": doc_id,
            "source_version": version,
            "raw": row.get("raw"),
        }
        key = (str(doc_id), stamp)
        if key in suppressed:
            record = suppressed[key]
            handed_off.append(
                {
                    **base,
                    "anomaly_id": record["id"],
                    "code": record["code"],
                    "stage": record["stage"],
                    "disposition": record["disposition"],
                }
            )
            decisions[str(doc_id)] = {
                "decision": "suppressed",
                "anomaly_id": record["id"],
            }
            continue
        if row.get("anomaly") is not None or not row.get("input_available"):
            anomaly = row.get("anomaly") or {
                "code": "CAPTURE_RAW_UNAVAILABLE",
                "reason": "CAPTURE_RAW_UNAVAILABLE",
            }
            anomalies.append(
                {
                    "stage": "plan",
                    "book_id": book_id,
                    "document_id": doc_id,
                    "code": anomaly["code"],
                    "reason": anomaly.get("reason") or anomaly["code"],
                    "source_version": version,
                    "necessary_inputs": _necessary_inputs(context, row),
                }
            )
            decisions[str(doc_id)] = {
                "decision": "input_unavailable",
                "code": anomaly["code"],
            }
            continue

        body = row.get("body") or {}
        comparison = row.get("comparison") or {}
        action = (body.get("graphify_plan") or {}).get("action")
        status = body.get("status")
        old_exists = row.get("previous_kind") is not None
        reasons = list(row.get("reason_codes") or [])

        # The OCR work list is every incoming image the comparator did not prove
        # reusable: processing_required (NEW/CHANGED) plus unverified UNKNOWN
        # images. A changed or unprovable image therefore reaches OCR even when
        # the body is reused, and body reuse never depends on OCR completing.
        ocr = comparison.get("ocr") or {}
        reusable = set(ocr.get("reusable") or [])
        required = [
            entry["resource_id"]
            for entry in row.get("resources") or []
            if entry.get("kind") == "image" and entry["resource_id"] not in reusable
        ]

        # Body reuse is independent of OCR: an identical body keeps its knowledge
        # even when images changed and must be re-OCR'd.
        if old_exists and action == REUSE_ACTION and status == REUSE_STATUS:
            body_reuse.append(
                {
                    **base,
                    "previous_raw": row.get("previous_raw"),
                    "previous_graph_version": row.get("previous_graph_version"),
                    "reason": "BODY_REUSE_VERIFIED",
                }
            )
            decision = "body_reuse"
        else:
            if not old_exists:
                reasons.append("NEW_DOCUMENT_NOT_IN_GRAPH")
            elif (
                status in {"REVIEW", "UNKNOWN", "NO_BODY"} or action == "HOLD_EXISTING"
            ):
                reasons.append("COMPARISON_UNCERTAIN_FULL_RECOMPILE")
            elif action == "BODY_DELTA_CANDIDATE" or status == "DELTA":
                reasons.append("BODY_DELTA_FULL_RECOMPILE")
            compile_items.append(
                {
                    **base,
                    "previous_raw": row.get("previous_raw"),
                    "previous_kind": row.get("previous_kind"),
                    "scope": "whole_document",
                    "reason_codes": sorted(set(reasons)),
                    # Compile may consume these OCR results when available but
                    # proceeds with currently-available content otherwise.
                    "ocr_dependencies": required,
                    "ocr_blocking": False,
                }
            )
            decision = "compile"

        if required:
            ocr_items.append(
                {
                    **base,
                    "resources": _ocr_resources(row, required),
                    "reusable": sorted(reusable),
                    "processing_required": ocr.get("processing_required") or [],
                    "reason_codes": ocr.get("reason_codes") or [],
                }
            )
        tasks = _metadata_tasks(row)
        if tasks:
            metadata_items.append({**base, "tasks": tasks})
        decisions[str(doc_id)] = {
            "decision": decision,
            "ocr": bool(required),
            "metadata": bool(tasks),
        }

    return {
        "body_reuse": body_reuse,
        "compile": compile_items,
        "ocr": ocr_items,
        "metadata": metadata_items,
        "suppressed": handed_off,
        "anomalies": anomalies,
        "decisions": decisions,
    }


def _update_state(
    context: StageContext,
    report: dict[str, Any],
    plan: dict[str, Any],
    state_path: Path,
) -> None:
    state = _load_state(state_path, report["book"])
    by_id = {str(row["document_id"]): row for row in report["documents"]}
    reuse_ids = {str(row["document_id"]) for row in plan["body_reuse"]}
    for doc_id, decision in plan["decisions"].items():
        row = by_id.get(doc_id)
        if row is None:
            continue
        record = state["documents"].setdefault(
            doc_id,
            {
                "observation": None,
                "corpus": {"status": "not_captured", "version": None, "raw": None},
                "graph": {},
                "pending": [],
            },
        )
        record["observation"] = {
            "version": row["source_version"],
            "observed_at": report["observation"]["ended_at"],
        }
        if row.get("input_available"):
            record["corpus"] = {
                "status": "captured",
                "version": row["source_version"],
                "raw": row.get("raw"),
            }
        if doc_id in reuse_ids and row.get("raw") is not None:
            record["graph"] = {
                "status": "covered",
                "coverage": "body_reuse",
                "version": row["source_version"],
                "raw": row.get("raw"),
                "metadata": (row.get("body") or {}).get("metadata")
                or record.get("graph", {}).get("metadata"),
            }
        record.setdefault("steps", {})["plan"] = {
            "status": "completed",
            "recorded_at": now_iso(),
            "version": row["source_version"],
            "decision": decision["decision"],
        }
    state["book"] = report["book"]
    report_path = context.require("mdcompare", "report")
    inputs = {report_path}
    for row in report["documents"]:
        for key in ("raw", "previous_raw"):
            resolved = _resolve_raw(row.get(key), report_path)
            if resolved is not None:
                inputs.add(resolved)
    _write_plan(inputs, state_path, context.directory, plan, state)


def handle(context: StageContext) -> StageResult:
    report = _report(context)
    state_path = context.corpus / ".sync-state.json"
    plan_path = context.directory / "plan.json"
    if plan_path.exists() or plan_path.is_symlink():
        raise StageFailed("plan output already exists; resume the recorded run instead")
    suppressed = _suppressed_keys(context)
    with state_lock(state_path):
        decided = _decide(context, report, suppressed)
        anomalies = decided.pop("anomalies")
        if anomalies:
            try:
                decided["anomalies"] = archive_anomalies(context, anomalies)
            except (OSError, ValueError) as error:
                context.log.write(f"anomaly archiving failed: {safe_diagnostic(error)}")
                decided["anomalies"] = anomalies
        else:
            decided["anomalies"] = []
        plan = {
            "schema_version": PLAN_SCHEMA,
            "status": "planned",
            "book": report["book"],
            "observation": report["observation"],
            "mdcompare": context.layout.relative(
                context.require("mdcompare", "report")
            ),
            "body_reuse": decided["body_reuse"],
            "compile": decided["compile"],
            "ocr": decided["ocr"],
            "metadata": decided["metadata"],
            "suppressed": decided["suppressed"],
            "anomalies": decided["anomalies"],
            "ordering": {
                "stages": ["mdcompare", "plan", "ocr", "compile"],
                "compile_waits_for_ocr": False,
                "second_approval_after_ocr": False,
            },
            "counts": {
                "documents": len(report["documents"]),
                "body_reuse": len(decided["body_reuse"]),
                "compile": len(decided["compile"]),
                "ocr": len(decided["ocr"]),
                "metadata": len(decided["metadata"]),
                "suppressed": len(decided["suppressed"]),
                "anomalies": len(decided["anomalies"]),
                "external_calls": 0,
            },
            "execution": {
                "plan_only": False,
                "approval_required": False,
                "automatic_extraction_allowed": True,
                "full_document_fallback": True,
            },
        }
        plan["decisions"] = decided["decisions"]
        _update_state(context, report, plan, state_path)
    counts = plan["counts"]
    context.count(**{k: v for k, v in counts.items() if k != "external_calls"})
    status = "completed"
    if counts["documents"] == 0:
        status = "skipped"
    elif decided["anomalies"]:
        status = "partial"
    return StageResult(
        status=status,
        counts=counts,
        artifacts={"plan": context.layout.relative(plan_path)},
        inputs={"report": plan["mdcompare"]},
        anomalies=decided["anomalies"],
        reason=None
        if status == "completed"
        else "input unavailable for some documents",
    )


HANDLER = handle
