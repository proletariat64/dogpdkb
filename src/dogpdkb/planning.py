"""Product decisions over documented comparison reports, never tool algorithms."""

from __future__ import annotations

import hashlib
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
    version = row.get("version")
    if version is not None:
        result["provenance"] = {"version": version}
    if evidence:
        result.update(resources=evidence.get("resources", []))
        if evidence.get("ocr"):
            result["ocr"] = evidence["ocr"]
    return result


def make_plan(book, run, documents, knowledge, comparison, semantic, *, workspace=None):
    bodies = {str(item["document_id"]): item for item in semantic.get("results", [])}
    full = {str(item["document_id"]): item for item in comparison.get("results", [])}
    rows = []
    for row in documents:
        identifier = row["document_id"]
        body = bodies.get(identifier, {})
        status = body.get("semantic_status")
        if (
            status in {"NEW", "CHANGED", "UNCERTAIN"}
            and body.get("action") == "COMPILE"
        ):
            action, reasons = "compile", list(body["reason_codes"])
        elif body.get("action") == "SKIP" and (
            status == "EQUIVALENT"
            or status == "CHANGED"
            and body.get("routing_reason") == "SCORE_AT_OR_BELOW_THRESHOLD"
        ):
            action = (
                "metadata"
                if full.get(identifier, {}).get("metadata", {}).get("status")
                == "CHANGED"
                else "reuse"
            )
            reasons = list(body["reason_codes"])
        else:
            action, reasons = (
                "defer",
                list(body.get("reason_codes", [])) or ["SEMANTIC_RESULT_UNAVAILABLE"],
            )
            run.failure(
                persist=False,
                document_id=identifier,
                version=row.get("version"),
                step="compare",
                reason="AUTHORED_BODY_UNAVAILABLE",
                input=row.get("raw_path"),
                reason_codes=reasons,
                reports=[run.data["artifacts"].get("semantic")],
            )
        if body.get("routing_reason") in {
            "SCORE_AT_OR_BELOW_THRESHOLD",
            "SCORE_ABOVE_THRESHOLD",
        }:
            reasons.append(body["routing_reason"])
        rows.append(
            {
                **row,
                "action": action,
                "reason_codes": reasons,
                "ocr_resources": None,
                "semantic_status": status,
                "semantic_score": body.get("score"),
                "semantic_ambiguity_advisory": body.get("ambiguous"),
            }
        )
    plan = {
        "schema": "dogpdkb-plan-1",
        "book": book.key,
        "run_id": run.id,
        "sources": run.data.get("sources", []),
        "conflicts": [
            item
            for item in run.data["failures"]
            if item.get("reason") == "CORPUS_DESTINATION_CONFLICT"
        ],
        "documents": rows,
        "semantic_report": run.data["artifacts"].get("semantic"),
        "semantic_diff": run.data["artifacts"].get("semantic_diff"),
        "body_recovery": run.data["artifacts"].get("body_recovery"),
        "semantic_scoring": run.data["artifacts"].get("semantic_scoring"),
    }
    path = (workspace or run.directory) / "plan.json"
    atomic_json(path, plan)
    run.data["artifacts"]["plan"] = str(path)
    run.save()
    return plan


def validate_plan(plan, book, store, *, check_baseline=False):
    if plan.get("schema") != "dogpdkb-plan-1" or plan.get("book") != book.key:
        raise ValueError("plan schema or book mismatch")
    if plan.get("body_recovery"):
        from dogpdkb.semantic import validate_recovery

        validate_recovery(plan["body_recovery"])
    if plan.get("semantic_scoring"):
        from dogpdkb.semantic import verify_score_artifacts

        verify_score_artifacts(plan["semantic_scoring"])
    reject_rollback(store, plan["documents"])
    validate_inputs(plan["documents"], check_baseline=check_baseline)


def validate_inputs(documents, *, check_baseline=False):
    for row in documents:
        try:
            body = Path(row["raw_path"]).read_bytes()
            if body != Path(row["input_copy"]).read_bytes():
                raise ValueError("changed")
            version = row.get("version")
            if (
                isinstance(version, dict)
                and version.get("precision") == "content"
                and (
                    f"sha256:{hashlib.sha256(body).hexdigest()}" != version_key(version)
                )
            ):
                raise ValueError("content version changed")
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
