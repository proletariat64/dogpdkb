"""Read-only knowledge plans and independent observation/corpus/graph state."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from mdcompare.io import InputReader, publish_reports, validate_outputs
from mdcompare.knowledge import compare_knowledge_documents
from mdcompare.manifest import load_manifest
from mdcompare.model import CompareOptions, Document

PLAN_SCHEMA = "knowledge-plan-2.0"
STATE_SCHEMA = "knowledge-state-1.0"
APPROVAL_SCHEMA = "knowledge-approvals-2.0"
SNAPSHOT_SCHEMA = "knowledge-capture-1.0"


class PlanError(ValueError):
    """Inputs cannot produce an auditable knowledge plan."""


def state_lock_path(state_path: Path) -> Path:
    """Keep the coordination inode separate from atomically replaced state."""
    return state_path.with_name(f".{state_path.name}.lock")


@contextmanager
def state_lock(state_path: Path):
    """Serialize cooperative knowledge-state readers and writers."""
    descriptor = os.open(
        state_lock_path(state_path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _document_id(value: Any) -> int:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as error:
            raise PlanError("document identities must be positive integers") from error
        if parsed > 0 and str(parsed) == value:
            return parsed
    raise PlanError("document identities must be canonical positive integers")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlanError(f"cannot read JSON input {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise PlanError(f"JSON input must be an object: {path.name}")
    return value


def _load_snapshot(path: Path) -> dict[str, Any]:
    snapshot = _read_json(path)
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA:
        raise PlanError("snapshot has an unsupported schema")
    listing = snapshot.get("listing")
    documents = snapshot.get("documents")
    book = snapshot.get("book")
    observation = snapshot.get("observation")
    if (
        not isinstance(listing, dict)
        or listing.get("complete") is not True
        or not isinstance(documents, dict)
        or not isinstance(book, dict)
        or type(book.get("book_id")) is not int
        or not isinstance(observation, dict)
        or not isinstance(observation.get("ended_at"), str)
    ):
        raise PlanError("snapshot requires a complete listing and document identities")
    try:
        raw_listed_ids = listing["document_ids"]
    except KeyError as error:
        raise PlanError("snapshot document identities are invalid") from error
    if not isinstance(raw_listed_ids, list) or any(
        type(value) is not int for value in raw_listed_ids
    ):
        raise PlanError("snapshot listing IDs must be integers")
    listed_ids = sorted(_document_id(value) for value in raw_listed_ids)
    document_ids = sorted(_document_id(value) for value in documents)
    if (
        len(set(listed_ids)) != len(listed_ids)
        or len(set(document_ids)) != len(document_ids)
        or listed_ids != document_ids
        or listing.get("document_count") != len(document_ids)
        or any(
            not isinstance(record, dict) or record.get("id") != _document_id(doc_id)
            for doc_id, record in documents.items()
        )
    ):
        raise PlanError("snapshot identities do not match its complete listing")
    return snapshot


def _load_state(path: Path, book: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": STATE_SCHEMA, "book": book, "documents": {}}
    state = _read_json(path)
    if (
        state.get("schema_version") != STATE_SCHEMA
        or (state.get("book") or {}).get("book_id") != book.get("book_id")
        or not isinstance(state.get("documents"), dict)
    ):
        raise PlanError("state has an unsupported schema or different book")
    for doc_id in state["documents"]:
        _document_id(doc_id)
    return state


def _relative_raw(path: Path, owner: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(owner.parent.resolve()))
        if path.resolve().is_relative_to(owner.parent.resolve())
        else str(path.resolve()),
        "stage": "raw_export",
    }


def _resolve_raw(reference: Any, owner: Path) -> Path | None:
    if not isinstance(reference, dict) or reference.get("stage") != "raw_export":
        return None
    value = reference.get("path")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (owner.parent / path).resolve()


def _register_baseline(
    baseline_path: Path,
    state: dict[str, Any],
    state_path: Path,
) -> set[Path]:
    documents = load_manifest(
        baseline_path, InputReader(16 * 1024 * 1024), body_only=True
    )
    protected = {baseline_path}
    for document in documents:
        if document.content_kind != "raw":
            raise PlanError("graph baseline must contain explicit raw snapshots")
        if document.knowledge_base != str(state["book"]["book_id"]):
            raise PlanError("graph baseline and snapshot refer to different books")
        document_id = str(_document_id(document.document_id))
        record = state["documents"].setdefault(
            document_id,
            {
                "observation": None,
                "corpus": {"status": "not_captured", "version": None, "raw": None},
                "graph": {},
                "pending": [],
            },
        )
        if record["graph"].get("status") == "covered":
            raise PlanError("graph baseline is already registered")
        stamp = document.metadata.get("sync_timestamp") or None
        record["graph"] = {
            "status": "covered",
            "coverage": "registered_raw_baseline",
            "version": (
                {"field": "sync_timestamp", "value": stamp, "utc": stamp}
                if stamp
                else None
            ),
            "raw": _relative_raw(document.path, state_path),
            "metadata": document.metadata,
        }
        record["pending"] = [
            reason
            for reason in record.get("pending", [])
            if reason != "GRAPH_BASELINE_NOT_REGISTERED"
        ]
        protected.add(document.path.resolve())
    return protected


def _document(document: dict[str, Any], book_id: int, path: Path) -> Document:
    title = document.get("title")
    if not isinstance(title, str):
        raise PlanError("snapshot document title is invalid")
    toc_path = document.get("toc_path") or []
    return Document(
        knowledge_base=str(book_id),
        document_id=str(document["id"]),
        content_kind="raw",
        path=path,
        metadata={
            "name": title,
            "logical_path": "/".join(str(value) for value in toc_path),
            "source_ref": f"{book_id}:{document['id']}",
        },
        resources=[],
        base_dir=path.parent,
    )


def _graph_document(
    doc_id: str, graph: dict[str, Any], book_id: int, state_path: Path
) -> Document | None:
    path = _resolve_raw(graph.get("raw"), state_path)
    metadata = graph.get("metadata")
    if path is None or not isinstance(metadata, dict):
        return None
    return Document(
        knowledge_base=str(book_id),
        document_id=doc_id,
        content_kind="raw",
        path=path,
        metadata=metadata,
        resources=[],
        base_dir=path.parent,
    )


def _compare(old: Document | None, new: Document) -> dict[str, Any]:
    reader = InputReader(CompareOptions.max_file_bytes)
    report = compare_knowledge_documents(
        [old] if old else [], [new], CompareOptions(), reader
    )
    if len(report["results"]) != 1:
        raise PlanError("comparison did not conserve the selected document identity")
    return report["results"][0]


def _freeze_raw(
    raw_path: Path,
    staging: Path,
    book_id: int,
    doc_id: int,
    protected: set[Path],
    state_path: Path,
    *,
    previous: bool = False,
) -> Path:
    suffix = "-previous" if previous else ""
    destination = staging / "inputs" / f"{book_id}-{doc_id}{suffix}.md"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    validate_outputs(
        [destination, staging / "plan.json", state_path, state_lock_path(state_path)],
        protected | {raw_path},
    )
    content = InputReader(CompareOptions.max_file_bytes).read_bytes(raw_path)
    if destination.exists():
        if destination.read_bytes() != content:
            raise PlanError("frozen planning input already exists with different bytes")
        return destination
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".knowledge-raw-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    protected.add(destination)
    return destination


def _write_plan(
    inputs: set[Path],
    state_path: Path,
    staging: Path,
    plan: dict[str, Any],
    state: dict[str, Any],
) -> None:
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging.chmod(0o700)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    plan_path = staging / "plan.json"
    validate_outputs([plan_path, state_path, state_lock_path(state_path)], inputs)
    publish_reports(
        [
            (state_path, json.dumps(state, ensure_ascii=False, indent=1) + "\n"),
            (plan_path, json.dumps(plan, ensure_ascii=False, indent=1) + "\n"),
        ]
    )


def run_planner(args: Any) -> dict[str, Any]:
    snapshot_path = args.snapshot.expanduser().resolve()
    state_path = args.state.expanduser().resolve()
    staging = args.staging.expanduser().resolve()
    snapshot = _load_snapshot(snapshot_path)
    protected = {snapshot_path}
    if args.baseline:
        protected.add(args.baseline.expanduser().resolve())
    exports = snapshot.get("exports") or {}
    for document_id, document in snapshot["documents"].items():
        reference = document.get("raw") or (exports.get(document_id) or {}).get("raw")
        raw_path = _resolve_raw(reference, snapshot_path)
        if raw_path:
            protected.add(raw_path)
    validate_outputs(
        [staging / "plan.json", state_path, state_lock_path(state_path)], protected
    )
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with state_lock(state_path):
        return _run_planner(args)


def _run_planner(args: Any) -> dict[str, Any]:
    snapshot_path = args.snapshot.expanduser().resolve()
    state_path = args.state.expanduser().resolve()
    staging = args.staging.expanduser().resolve()
    plan_path = staging / "plan.json"
    if plan_path.exists() or plan_path.is_symlink():
        raise PlanError("plan output already exists; use a new staging directory")
    snapshot = _load_snapshot(snapshot_path)
    book_id = snapshot["book"]["book_id"]
    state = _load_state(state_path, snapshot["book"])
    protected = {snapshot_path}
    if args.baseline:
        protected |= _register_baseline(
            args.baseline.expanduser().resolve(), state, state_path
        )
    exports = snapshot.get("exports") or {}
    for doc_id, document in snapshot["documents"].items():
        raw_reference = document.get("raw") or (exports.get(doc_id) or {}).get("raw")
        raw_path = _resolve_raw(raw_reference, snapshot_path)
        if raw_path:
            protected.add(raw_path)
    anomalies = snapshot.get("anomalies") or {}
    anomaly_codes: dict[int, list[str]] = {}
    if isinstance(anomalies, list):
        for row in anomalies:
            if isinstance(row, dict) and type(row.get("id")) is int:
                anomaly_codes.setdefault(row["id"], []).append(
                    str(row.get("code") or "CAPTURE_ANOMALY")
                )
    selected = {_document_id(value) for value in snapshot.get("selected") or []}
    changes = snapshot.get("changes") or {}
    results = []
    compared = 0
    for doc_id, document in sorted(
        snapshot["documents"].items(), key=lambda item: _document_id(item[0])
    ):
        numeric_id = _document_id(doc_id)
        previous = state["documents"].get(doc_id) or {}
        graph = previous.get("graph") or {
            "status": "uncovered",
            "version": None,
            "raw": None,
        }
        previous_pending = list(previous.get("pending") or [])
        if args.baseline:
            previous_pending = [
                reason
                for reason in previous_pending
                if reason != "GRAPH_BASELINE_NOT_REGISTERED"
            ]
        labels = sorted(
            label
            for label in ("new", "updated", "renamed", "moved")
            if numeric_id in (changes.get(label) or [])
        )
        export = exports.get(doc_id) or {}
        raw_reference = document.get("raw") or export.get("raw")
        raw_path = _resolve_raw(raw_reference, snapshot_path)
        capture_anomalies = anomaly_codes.get(numeric_id, [])
        if export.get("status") == "failed":
            capture_anomalies.append("CAPTURE_EXPORT_FAILED")
        raw_missing = raw_path is not None and not raw_path.is_file()
        if raw_missing:
            capture_anomalies.append("CAPTURE_RAW_UNAVAILABLE")
        if raw_path:
            protected.add(raw_path)
            corpus = {
                "status": "pending" if capture_anomalies else "captured",
                "version": None if capture_anomalies else document["version"],
                "raw": _relative_raw(raw_path, state_path),
            }
        else:
            corpus = previous.get("corpus") or {
                "status": "not_captured",
                "version": None,
                "raw": None,
            }
        metadata_tasks = [label for label in ("renamed", "moved") if label in labels]
        graph_version = graph.get("version")
        graph_needs_comparison = graph.get("status") == "covered" and (
            not isinstance(graph_version, dict)
            or graph_version.get("utc") != document["version"].get("utc")
        )
        work = (
            numeric_id in selected
            or bool(previous_pending)
            or graph_needs_comparison
            or (graph.get("status") != "covered" and raw_path is not None)
        )
        comparison = None
        transient_reasons = {
            "APPROVAL_REQUIRED",
            "BODY_COMPARISON_REVIEW_REQUIRED",
            "CAPTURE_ANOMALY",
            "CAPTURE_EXPORT_FAILED",
            "CAPTURE_RAW_UNAVAILABLE",
            "NEW_DOCUMENT_NOT_IN_GRAPH",
            "export_failed",
            "detail_newer_than_listing",
            "detail_older_than_listing",
        }
        persistent_pending = [
            reason for reason in previous_pending if reason not in transient_reasons
        ]
        reasons = list(persistent_pending)
        old = _graph_document(doc_id, graph, book_id, state_path)
        frozen = None
        frozen_old = None
        if work and not capture_anomalies and raw_path is not None:
            frozen = _freeze_raw(
                raw_path, staging, book_id, numeric_id, protected, state_path
            )
            if old is not None:
                frozen_old = _freeze_raw(
                    old.path,
                    staging,
                    book_id,
                    numeric_id,
                    protected,
                    state_path,
                    previous=True,
                )
                old = replace(old, path=frozen_old, base_dir=frozen_old.parent)
            comparison = _compare(old, _document(document, book_id, frozen))
            compared += 1
            action = comparison["graphify_plan"]["action"]
            if old is None:
                reasons.append("NEW_DOCUMENT_NOT_IN_GRAPH")
                if action == "KEEP_EXISTING":
                    action = "BODY_DELTA_CANDIDATE"
            if action == "BODY_DELTA_CANDIDATE":
                reasons.append("APPROVAL_REQUIRED")
            elif action == "HOLD_EXISTING":
                reasons.append("BODY_COMPARISON_REVIEW_REQUIRED")
        elif work:
            action = "HOLD_EXISTING"
            reasons.extend(capture_anomalies or ["CAPTURE_RAW_UNAVAILABLE"])
        elif graph.get("status") == "covered":
            action = "KEEP_EXISTING"
        else:
            action = "HOLD_EXISTING"
            reasons.append(
                "GRAPH_SOURCE_NOT_REGISTERED"
                if any(
                    item.get("graph", {}).get("status") == "covered"
                    for item in state["documents"].values()
                )
                else "GRAPH_BASELINE_NOT_REGISTERED"
            )
        if persistent_pending:
            action = "HOLD_EXISTING"
        if metadata_tasks:
            reasons.append("METADATA_MAINTENANCE_REQUIRED")
        reasons = sorted(set(reasons))
        if (
            action == "KEEP_EXISTING"
            and comparison is not None
            and not metadata_tasks
            and not reasons
        ):
            if raw_path is None:
                raise AssertionError("body reuse requires a captured raw source")
            graph = {
                "status": "covered",
                "coverage": "body_reuse",
                "version": document["version"],
                "raw": _relative_raw(raw_path, state_path),
                "metadata": _document(document, book_id, raw_path).metadata,
            }
        record = {
            "observation": {
                "version": document["version"],
                "observed_at": snapshot["observation"]["ended_at"],
            },
            "corpus": corpus,
            "graph": graph,
            "pending": reasons,
        }
        state["documents"][doc_id] = record
        execution = None
        if action == "BODY_DELTA_CANDIDATE":
            if frozen is None or comparison is None:
                raise AssertionError("candidate requires a compared raw source")
            context_indexes = set(
                comparison["graphify_plan"]["context_review_unit_indexes"]
                + comparison["graphify_plan"]["presentation_review_unit_indexes"]
            )
            review_issues = [
                {
                    "issue_id": f"document:{code}",
                    "code": code,
                    "allowed_decisions": ["acknowledged"],
                }
                for code in sorted(set(comparison["reason_codes"]))
            ]
            for index, unit in enumerate(comparison["units"]):
                unit_issues = []
                if unit["kind"] == "revise":
                    unit_issues.append(
                        ("REVISION_REVIEW_REQUIRED", ["accept_revision", "preserve"])
                    )
                if (
                    unit["kind"] in {"old_only_review", "unresolved"}
                    or index in context_indexes
                ):
                    allowed = (
                        ["approve_removal", "preserve"]
                        if unit["kind"] == "old_only_review"
                        else ["acknowledged", "preserve"]
                    )
                    unit_issues.append((unit["basis"], allowed))
                for code, allowed in unit_issues:
                    review_issues.append(
                        {
                            "issue_id": f"unit:{index}:{code}",
                            "code": code,
                            "unit_index": index,
                            "unit_kind": unit["kind"],
                            "old": unit.get("old"),
                            "new": unit.get("new"),
                            "allowed_decisions": allowed,
                        }
                    )
            execution = {
                "source_scope": "whole_document",
                "source_version": document["version"],
                "raw": _relative_raw(frozen, staging / "plan.json"),
                "previous_raw": (
                    _relative_raw(frozen_old, staging / "plan.json")
                    if frozen_old is not None
                    else None
                ),
                "previous_graph_version": previous.get("graph", {}).get("version"),
                "review_issues": sorted(
                    review_issues, key=lambda issue: issue["issue_id"]
                ),
            }
        results.append(
            {
                "book_id": book_id,
                "document_id": numeric_id,
                "labels": labels,
                "selected": numeric_id in selected,
                "action": action,
                "comparison": comparison,
                "observation": record["observation"],
                "corpus": corpus,
                "graph": graph,
                "reason_codes": reasons,
                "pending": reasons,
                "metadata_tasks": metadata_tasks,
                "approval": {
                    "required": action == "BODY_DELTA_CANDIDATE",
                    "status": "required"
                    if action == "BODY_DELTA_CANDIDATE"
                    else "not_applicable",
                },
                "execution": execution,
            }
        )
    current_ids = {_document_id(value) for value in snapshot["documents"]}
    baseline_only = [
        {"book_id": book_id, "document_id": _document_id(doc_id)}
        for doc_id, record in sorted(
            state["documents"].items(), key=lambda item: _document_id(item[0])
        )
        if _document_id(doc_id) not in current_ids
        and record["graph"].get("status") == "covered"
    ]
    actions = Counter(row["action"] for row in results)
    state["book"] = snapshot["book"]
    plan = {
        "schema_version": PLAN_SCHEMA,
        "status": "planned",
        "book": snapshot["book"],
        "observation": snapshot["observation"],
        "snapshot_status": snapshot["status"],
        "results": results,
        "baseline_only": baseline_only,
        "counts": {
            "documents": len(results),
            "results": len(results),
            "selected": len(selected),
            "compared": compared,
            "baseline_only": len(baseline_only),
            "new_documents": sum("new" in row["labels"] for row in results),
            "pending": sum(bool(row["pending"]) for row in results),
            "actions": dict(sorted(actions.items())),
            "approved": 0,
            "executions": 0,
            "external_calls": 0,
            "cache_hits": 0,
            "graph_successes": 0,
        },
        "execution": {
            "plan_only": True,
            "automatic_extraction_allowed": False,
            "full_document_fallback": False,
        },
        "approval_contract": {
            "schema_version": APPROVAL_SCHEMA,
            "binding": ["book_id", "document_id", "source_version", "raw"],
            "source_scope": "whole_document",
            "review_dispositions_required": True,
            "quality_cases_required": True,
        },
        "cost": {"status": "unknown", "evidence": "none"},
    }
    _write_plan(protected, state_path, staging, plan, state)
    return plan
