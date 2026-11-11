"""compile: automatic whole-source knowledge publication (ticket 06).

Consumes the ``unified-plan-1.0`` compile list and publishes each successfully
compiled document as a complete source through the existing Graphify candidate /
publication-recovery machinery. There is no human approval and no semantic
quality gate: publication is bounded by artifact checks only. A source that fails
extraction keeps its old knowledge and old success version (partial publication),
and is archived as a durable anomaly outside wash so the daily batch does not
retry the same version. Graphify's own output streams into the stage log.
"""

from __future__ import annotations

from typing import Any

from dogpdkb.knowledge_execute import COMPILE_SCHEMA, ExecutionError, execute_compile
from dogpdkb.sync_results import archive_anomalies
from dogpdkb.sync_run import read_json
from dogpdkb.sync_stages import StageContext, StageFailed, StageResult
from dogpdkb.workflows.get_sync_updates import safe_diagnostic

PLAN_SCHEMA = "unified-plan-1.0"


def _plan(context: StageContext) -> dict[str, Any]:
    path = context.require("plan", "plan")
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != PLAN_SCHEMA:
        raise StageFailed(f"plan has an unsupported schema: {path}")
    return payload


def _anomalies(context: StageContext, report: dict[str, Any]) -> list[dict[str, Any]]:
    book_id = report["book"]["book_id"]
    return [
        {
            "stage": "compile",
            "book_id": book_id,
            "document_id": row["document_id"],
            "code": row["code"],
            "reason": row["reason"],
            "source_version": row["source_version"],
            "necessary_inputs": [],
        }
        for row in report.get("failed", [])
    ]


def handle(context: StageContext) -> StageResult:
    plan = _plan(context)
    result_path = context.directory / "compile-result.json"
    if result_path.exists() or result_path.is_symlink():
        raise StageFailed(
            "compile output already exists; resume the recorded run instead"
        )
    try:
        report = execute_compile(context, plan)
    except (ExecutionError, OSError, ValueError, RuntimeError) as error:
        raise StageFailed(f"compile failed: {safe_diagnostic(error)}") from error
    if report.get("schema_version") != COMPILE_SCHEMA:
        raise StageFailed("compile produced an unsupported result schema")
    anomalies = _anomalies(context, report)
    if anomalies:
        try:
            anomalies = archive_anomalies(context, anomalies)
        except (OSError, ValueError) as error:
            context.log.write(f"anomaly archiving failed: {safe_diagnostic(error)}")
    counts = report["counts"]
    context.count(**{k: v for k, v in counts.items() if v is not None})
    status = report["status"]
    if counts["compile"] == 0 and not counts.get("metadata") and not anomalies:
        status = "skipped"
    return StageResult(
        status=status,
        counts=counts,
        artifacts={"result": context.layout.relative(result_path)},
        inputs={"plan": context.layout.relative(context.require("plan", "plan"))},
        anomalies=anomalies,
        reason=None if status == "completed" else "some sources failed to compile",
    )


HANDLER = handle
