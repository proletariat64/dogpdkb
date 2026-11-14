"""Product decisions over documented comparison reports, never tool algorithms."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dogpdkb.records import UTC8, atomic_json, read_json, version_key


def same_version(left, right):
    try:
        return datetime.fromisoformat(
            version_key(left).replace("Z", "+00:00")
        ) == datetime.fromisoformat(version_key(right).replace("Z", "+00:00"))
    except ValueError:
        return version_key(left) == version_key(right)


def reject_rollback(store, documents):
    for row in documents:
        requested = version_key(row["version"])
        current = store.document(row["document_id"])
        for stage in ("corpus", "graph"):
            version = current.get(stage, {}).get("version")
            if version is None or version_key(version) == requested:
                continue
            try:
                before = datetime.fromisoformat(
                    version_key(version).replace("Z", "+00:00")
                )
                desired = datetime.fromisoformat(requested.replace("Z", "+00:00"))
                before = before.replace(tzinfo=before.tzinfo or UTC8)
                desired = desired.replace(tzinfo=desired.tzinfo or UTC8)
            except ValueError:
                ordered = False
            else:
                ordered = True
                day_precision = any(
                    isinstance(value, dict)
                    and value.get("precision") == "day"
                    or len(version_key(value)) == 10
                    for value in (version, row["version"])
                )
                rollback = (
                    before.astimezone(UTC8).date() > desired.astimezone(UTC8).date()
                    if day_precision
                    else before > desired
                )
                if rollback:
                    raise ValueError(
                        f"newer {stage} version exists for {row['document_id']}; generate a new plan"
                    )
            # A saved plan/difference binds the successful facts it observed.
            # No ordering can be inferred from opaque versions such as v2/v10.
            key = f"previous_{stage}"
            frozen = row.get(key) if key in row else row.get("local", {}).get(stage)
            if frozen is not None:
                previous = frozen.get("version")
                if previous is not None and version_key(previous) == version_key(
                    version
                ):
                    continue
                raise ValueError(
                    f"changed {stage} version exists for {row['document_id']}; generate a new plan"
                )
            if not ordered:
                raise ValueError(
                    f"changed {stage} version lacks frozen facts for {row['document_id']}; generate a new plan"
                )


def descriptor(book, row, path, *, historical=False, evidence=None):
    metadata = row.get("metadata", {})
    logical = metadata.get("logical_path", row.get("logical_path", row.get("path", "")))
    if isinstance(logical, list):
        logical = "/".join(str(part) for part in logical)
    result = {
        "knowledge_base": book.key,
        "document_id": row["document_id"],
        "path": str(path),
        "content_kind": "historical_enriched" if historical else "raw",
        "metadata": {
            "name": str(metadata.get("name", row.get("title", ""))),
            "logical_path": str(logical),
            "source_ref": str(metadata.get("source_ref", row.get("source_url", ""))),
        },
        "resources": [],
    }
    if evidence:
        result.update(resources=evidence.get("resources", []))
        if evidence.get("ocr"):
            result["ocr"] = evidence["ocr"]
    return result


def make_plan(book, run, documents, knowledge, comparison, *, workspace=None):
    bodies = {str(r["document_id"]): r for r in knowledge.get("results", [])}
    full = {str(r["document_id"]): r for r in comparison.get("results", [])}
    rows = []
    for row in documents:
        identifier = row["document_id"]
        body, detail = bodies.get(identifier, {}), full.get(identifier, {})
        advice = body.get("graphify_plan", {})
        hold_reasons = advice.get("hold_reason_codes", [])
        unavailable = (
            body.get("status") == "UNKNOWN"
            or not body.get("comparison_complete", False)
            or "BASELINE_BODY_INCOMPLETE" in hold_reasons
            or "BASELINE_BODY_INCOMPLETE" in body.get("reason_codes", [])
            or advice.get("action")
            not in {"KEEP_EXISTING", "HOLD_EXISTING", "BODY_DELTA_CANDIDATE"}
        )
        if unavailable:
            action, reasons = "defer", ["BODY_COMPARISON_DEFERRED"]
            run.failure(
                persist=False,
                document_id=identifier,
                version=row.get("version"),
                step="compare",
                reason="BODY_COMPARISON_DEFERRED",
                input=row.get("raw_path"),
                reason_codes=body.get("reason_codes", []),
                reports=[
                    value
                    for key, value in run.data["artifacts"].items()
                    if key in {"knowledge", "compare"}
                ],
            )
        elif advice["action"] == "HOLD_EXISTING":
            action, reasons = "retain", ["BODY_RETAINED", *hold_reasons]
        elif advice["action"] == "KEEP_EXISTING":
            action = (
                "metadata"
                if detail.get("metadata", {}).get("status") == "CHANGED"
                else "reuse"
            )
            reasons = ["BODY_REUSABLE"]
        else:
            action, reasons = "compile", ["FULL_SOURCE_REQUIRED"]
        resources = detail.get("resources", {}).get("status")
        ocr = detail.get("ocr", {}).get("status")
        if action not in {"defer", "retain"} and (
            resources in {"CHANGED", "NEW"}
            or detail.get("resources", {}).get("references_changed")
            or ocr in {"CHANGED", "NEW", "REMOVED"}
            or detail.get("ocr", {}).get("removed_contributions")
        ):
            action = "compile"
            reasons.append("RESOURCE_OR_OCR_CHANGED")
        rows.append(
            {
                **row,
                "action": action,
                "reason_codes": reasons,
                "ocr_resources": None,
            }
        )
    plan = {
        "schema": "dogpdkb-plan-1",
        "book": book.key,
        "run_id": run.id,
        "documents": rows,
    }
    path = (workspace or run.directory) / "plan.json"
    atomic_json(path, plan)
    run.data["artifacts"]["plan"] = str(path)
    run.save()
    return plan


def validate_plan(plan, book, store, *, check_baseline=False):
    if plan.get("schema") != "dogpdkb-plan-1" or plan.get("book") != book.key:
        raise ValueError("plan schema or book mismatch")
    reject_rollback(store, plan["documents"])
    validate_inputs(plan["documents"], check_baseline=check_baseline)


def validate_inputs(documents, *, check_baseline=False):
    for row in documents:
        try:
            if (
                Path(row["raw_path"]).read_bytes()
                != Path(row["input_copy"]).read_bytes()
            ):
                raise ValueError("changed")
            if (
                check_baseline
                and row.get("baseline_input_copy")
                and Path(row["baseline"]["path"]).read_bytes()
                != Path(row["baseline_input_copy"]).read_bytes()
            ):
                raise ValueError("baseline changed")
        except (OSError, ValueError) as error:
            raise ValueError(
                f"plan input missing or changed for {row['document_id']}; generate a new plan"
            ) from error


def load_plan(run, book, store):
    path = run.data.get("artifacts", {}).get("plan")
    if not path:
        raise ValueError("run has no saved plan; generate a new plan")
    plan = read_json(Path(path))
    validate_plan(plan, book, store)
    return plan
