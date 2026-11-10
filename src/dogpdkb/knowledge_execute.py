"""Execute explicitly approved whole-document knowledge updates safely."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from dogpdkb.instance_lock import subprocess_lock_options
from dogpdkb.knowledge_plan import (
    _load_snapshot,
    _load_state,
    _relative_raw,
    _resolve_raw,
    state_lock,
    state_lock_path,
)
from dogpdkb.knowledge_state import BookState, canonical_view, root_state, save_view
from dogpdkb.sync_time import now_iso
from dogpdkb.workflows.graph_sync import doc_id
from mdcompare.io import publish_reports, validate_outputs

EXECUTION_SCHEMA = "knowledge-execution-1.0"
APPROVAL_SCHEMA = "knowledge-approvals-2.0"
QUALITY_SCHEMA = "knowledge-quality-cases-2.0"


class ExecutionError(ValueError):
    """Approved execution inputs or results do not satisfy their contract."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionError(f"cannot read JSON input {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ExecutionError(f"JSON input must be an object: {path.name}")
    return value


def _bound_document(path: Path, plan_path: Path, schema: str) -> dict[str, Any]:
    document = _read_json(path)
    if document.get("schema_version") != schema:
        raise ExecutionError(f"{path.name} has an unsupported schema")
    binding = document.get("plan")
    if not isinstance(binding, str) or not binding:
        raise ExecutionError(f"{path.name} must bind the selected plan")
    bound = Path(binding)
    if not bound.is_absolute():
        bound = path.parent / bound
    if bound.resolve() != plan_path.resolve():
        raise ExecutionError(f"{path.name} refers to a different plan")
    return document


def _empty_counts() -> dict[str, int | None]:
    return {
        "approved": 0,
        "executions": 0,
        "external_calls": 0,
        "cache_hits": 0,
        "graph_successes": 0,
    }


def _approved_rows(
    args: Any,
    plan: dict[str, Any],
    approvals: dict[str, Any],
    *,
    allow_completed: bool = False,
) -> list[tuple[dict[str, Any], dict[str, Any], Path]]:
    entries = approvals.get("approvals")
    if not isinstance(entries, list):
        raise ExecutionError("approvals must be a list")
    candidates = {
        (row.get("book_id"), row.get("document_id")): row
        for row in plan.get("results", [])
        if row.get("action") == "BODY_DELTA_CANDIDATE"
    }
    snapshot_path = args.snapshot.expanduser().resolve()
    snapshot = _load_snapshot(snapshot_path)
    if snapshot["book"]["book_id"] != plan["book"]["book_id"]:
        raise ExecutionError("snapshot and plan refer to different books")
    state = _load_state(args.state.expanduser().resolve(), plan["book"])
    selected = []
    seen = set()
    plan_path = args.staging.expanduser().resolve() / "plan.json"
    for approval in entries:
        if not isinstance(approval, dict):
            raise ExecutionError("each approval must be an object")
        key = (approval.get("book_id"), approval.get("document_id"))
        if key in seen:
            raise ExecutionError("duplicate document approval")
        seen.add(key)
        row = candidates.get(key)
        if row is None:
            raise ExecutionError("approval does not identify a current body candidate")
        execution = row.get("execution")
        if not isinstance(execution, dict):
            raise ExecutionError("candidate has no whole-document execution contract")
        if (
            approval.get("source_version") != execution.get("source_version")
            or approval.get("raw") != execution.get("raw")
            or approval.get("source_scope") != "whole_document"
            or execution.get("source_scope") != "whole_document"
        ):
            raise ExecutionError("approval does not match the frozen source version")
        if row.get("metadata_tasks"):
            raise ExecutionError(
                "metadata relocation requires explicit source maintenance; HOLD"
            )
        issues = execution.get("review_issues")
        dispositions = approval.get("review_dispositions")
        if not isinstance(issues, list) or not isinstance(dispositions, list):
            raise ExecutionError("review dispositions must cover every review issue")
        if any(
            not isinstance(issue, dict)
            or not isinstance(issue.get("issue_id"), str)
            or not issue["issue_id"]
            or not isinstance(issue.get("allowed_decisions"), list)
            for issue in issues
        ) or len({issue["issue_id"] for issue in issues}) != len(issues):
            raise ExecutionError("plan contains invalid review issue identities")
        issue_by_id = {issue["issue_id"]: issue for issue in issues}
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("issue_id"), str)
            or not isinstance(item.get("decision"), str)
            for item in dispositions
        ):
            raise ExecutionError("review dispositions must identify each decision")
        disposition_ids = [item["issue_id"] for item in dispositions]
        if len(disposition_ids) != len(set(disposition_ids)) or set(
            disposition_ids
        ) != set(issue_by_id):
            raise ExecutionError("review dispositions must exactly cover review issues")
        for disposition in dispositions:
            issue = issue_by_id[disposition["issue_id"]]
            if disposition["decision"] not in issue["allowed_decisions"]:
                raise ExecutionError(
                    f"invalid decision for review issue: {issue['issue_id']}"
                )
            if disposition["decision"] == "preserve" and (
                not isinstance(disposition.get("quality_case_ids"), list)
                or not disposition["quality_case_ids"]
                or any(
                    not isinstance(case_id, str) or not case_id
                    for case_id in disposition["quality_case_ids"]
                )
            ):
                raise ExecutionError(
                    "preserve disposition requires explicit quality case bindings"
                )
        doc_id = str(row["document_id"])
        observed = snapshot["documents"].get(doc_id)
        if observed is None or observed.get("version") != execution["source_version"]:
            raise ExecutionError("snapshot source version changed after planning")
        reference = observed.get("raw") or (
            snapshot.get("exports", {}).get(doc_id) or {}
        ).get("raw")
        current_raw = _resolve_raw(reference, snapshot_path)
        frozen_raw = _resolve_raw(execution["raw"], plan_path)
        if current_raw is None or frozen_raw is None or not frozen_raw.is_file():
            raise ExecutionError("approved complete raw source is unavailable")
        if (
            not current_raw.is_file()
            or current_raw.read_bytes() != frozen_raw.read_bytes()
        ):
            raise ExecutionError("approved raw input changed after planning")
        state_record = state.get("documents", {}).get(doc_id)
        actual_graph = (
            state_record.get("graph") if isinstance(state_record, dict) else None
        )
        completed_graph = (
            allow_completed
            and isinstance(actual_graph, dict)
            and actual_graph.get("status") == "covered"
            and actual_graph.get("coverage") == "approved_whole_document"
            and actual_graph.get("version") == execution["source_version"]
            and not state_record.get("pending")
        )
        if not completed_graph and (
            not isinstance(state_record, dict)
            or state_record.get("observation") != row.get("observation")
            or state_record.get("corpus") != row.get("corpus")
            or state_record.get("pending") != row.get("pending")
        ):
            raise ExecutionError("plan is superseded by current observation or work")
        if actual_graph != row.get("graph") and not completed_graph:
            raise ExecutionError("graph source version changed after planning")
        if not completed_graph and (row.get("graph") or {}).get("status") == "covered":
            previous_raw = _resolve_raw(execution.get("previous_raw"), plan_path)
            registered_old = _resolve_raw(
                (row.get("graph") or {}).get("raw"),
                args.state.expanduser().resolve(),
            )
            if (
                previous_raw is None
                or registered_old is None
                or not previous_raw.is_file()
                or not registered_old.is_file()
                or previous_raw.read_bytes() != registered_old.read_bytes()
            ):
                raise ExecutionError("old comparison input changed after planning")
        selected.append((row, approval, frozen_raw))
    return selected


def _freeze_source(source: Path, destination: Path) -> None:
    content = source.read_bytes()
    if destination.exists():
        if destination.read_bytes() != content:
            raise ExecutionError("execution corpus differs from the approved raw input")
        return
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".approved-source-", dir=destination.parent
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


def _run_graphify(
    args: Any, phase: str, corpus: Path, manifest: Path
) -> dict[str, Any]:
    graph_dir = args.graph_dir.expanduser().resolve()
    graph_staging = args.staging.expanduser().resolve() / "graph-update"
    interpreter = (
        args.graphify_python.expanduser().absolute()
        if args.graphify_python
        else Path(sys.executable)
    )
    if not interpreter.is_file():
        raise ExecutionError("Graphify Python is unavailable")
    script = Path(__file__).resolve().parent / "workflows" / "update_graph.py"
    environment = os.environ.copy()
    environment["GRAPHIFY_OUT"] = graph_dir.name
    # The parent executor owns canonical progress; this corpus is an isolated input.
    for name in ("DOGPDKB_RUN_CORPUS", "DOGPDKB_RUN_BOOK", "DOGPDKB_RUN_STAGING"):
        environment.pop(name, None)
    completed = subprocess.run(
        [
            str(interpreter),
            str(script),
            "--input",
            str(corpus),
            "--manifest",
            str(manifest),
            "--staging",
            str(graph_staging),
            "--graph-dir",
            str(graph_dir),
            phase,
        ],
        env=environment,
        **subprocess_lock_options(),
        text=True,
        capture_output=True,
        check=False,
    )
    result_name = (
        "candidate-result.json" if phase == "--candidate-only" else "result.json"
    )
    result_path = graph_staging / result_name
    if completed.returncode != 0 or not result_path.is_file():
        raise ExecutionError(
            f"Graphify {phase.removeprefix('--')} failed with exit {completed.returncode}; "
            "the old graph and retained transaction are preserved"
        )
    return _read_json(result_path)


def _validate_candidate_graph(
    candidate_path: Path,
    selected: list[tuple[dict[str, Any], dict[str, Any], Path]],
) -> dict[str, Any]:
    graph = _read_json(candidate_path)
    nodes = graph.get("nodes")
    links = graph.get("links")
    hyperedges = graph.get("hyperedges") or []
    if (
        not isinstance(nodes, list)
        or not isinstance(links, list)
        or not isinstance(hyperedges, list)
    ):
        raise ExecutionError("candidate graph has invalid record collections")
    ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(ids) != len(nodes) or any(
        not isinstance(value, str) or not value for value in ids
    ):
        raise ExecutionError("candidate graph contains an invalid node identity")
    if len(set(ids)) != len(ids):
        raise ExecutionError("candidate graph contains duplicate node IDs")
    identities = set(ids)
    if any(
        not isinstance(edge, dict)
        or edge.get("source") not in identities
        or edge.get("target") not in identities
        for edge in links
    ):
        raise ExecutionError("candidate graph contains a dangling edge")
    for hyperedge in hyperedges:
        members = (
            hyperedge.get("nodes")
            or hyperedge.get("members")
            or hyperedge.get("node_ids")
            or []
        )
        if not isinstance(members, list) or not set(members) <= identities:
            raise ExecutionError("candidate graph contains a dangling hyperedge")
    current_sources = {f"source-[{row['document_id']}].md" for row, _, _ in selected}
    covered = {
        str(record.get("source_file"))
        for record in [*nodes, *hyperedges]
        if isinstance(record, dict)
    }
    missing = current_sources - covered
    if missing:
        raise ExecutionError(
            "candidate graph does not cover every approved full source"
        )
    selected_ids = {str(row["document_id"]) for row, _, _ in selected}
    for record in [*nodes, *links, *hyperedges]:
        identity = doc_id(str(record.get("source_file") or ""))
        if (
            identity in selected_ids
            and record.get("source_file") not in current_sources
        ):
            raise ExecutionError(
                "candidate retained duplicate or stale source ownership"
            )
    return graph


def _record_text(record: dict[str, Any], field: str, nodes: dict[str, Any]) -> str:
    if field in {"source_label", "target_label"}:
        endpoint = record.get("source" if field == "source_label" else "target")
        if not isinstance(endpoint, str):
            return ""
        return str((nodes.get(endpoint) or {}).get("label") or "")
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _quality_case_definitions(
    quality: dict[str, Any],
    selected: list[tuple[dict[str, Any], dict[str, Any], Path]],
) -> list[dict[str, Any]]:
    cases = quality.get("cases")
    if not isinstance(cases, list):
        raise ExecutionError("quality cases must be a list")
    selected_by_id = {row["document_id"]: row for row, _, _ in selected}
    seen_cases = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ExecutionError("each quality case must be an object")
        case_id = case.get("case_id")
        document_id = case.get("document_id")
        if not isinstance(case_id, str) or not case_id or case_id in seen_cases:
            raise ExecutionError("quality case identities must be unique strings")
        seen_cases.add(case_id)
        row = selected_by_id.get(document_id)
        if (
            row is None
            or case.get("book_id") != row["book_id"]
            or case.get("source_version") != row["execution"]["source_version"]
        ):
            raise ExecutionError(
                "quality case is not bound to an approved source version"
            )
        where = case.get("where")
        required = case.get("required")
        if (
            case.get("record") not in {"node", "edge"}
            or not isinstance(where, dict)
            or not where
            or any(
                not isinstance(field, str) or not isinstance(value, str)
                for field, value in where.items()
            )
            or not isinstance(required, dict)
            or not required
            or any(
                not isinstance(field, str)
                or not isinstance(fragments, list)
                or not fragments
                or any(not isinstance(value, str) or not value for value in fragments)
                for field, fragments in required.items()
            )
        ):
            raise ExecutionError("quality case requires bounded record assertions")
    missing = set(selected_by_id) - {
        case.get("document_id") for case in cases if isinstance(case, dict)
    }
    if missing:
        raise ExecutionError(
            "every approved document requires at least one quality case"
        )
    for row, approval, _frozen in selected:
        available = {
            case["case_id"]
            for case in cases
            if case["document_id"] == row["document_id"]
        }
        for disposition in approval["review_dispositions"]:
            if disposition["decision"] != "preserve":
                continue
            if not set(disposition["quality_case_ids"]) <= available:
                raise ExecutionError(
                    "preserve disposition must bind a quality case for the same source"
                )
    return cases


def _validate_quality_cases(
    quality: dict[str, Any],
    graph: dict[str, Any],
    selected: list[tuple[dict[str, Any], dict[str, Any], Path]],
) -> dict[str, Any]:
    cases = _quality_case_definitions(quality, selected)
    nodes = {node["id"]: node for node in graph["nodes"]}
    passed = []
    for case in cases:
        document_id = case["document_id"]
        where = case["where"]
        required = case["required"]
        records = graph["nodes"] if case["record"] == "node" else graph["links"]
        matched = False
        for record in records:
            if doc_id(str(record.get("source_file") or "")) != str(document_id):
                continue
            if any(
                _record_text(record, field, nodes) != value
                for field, value in where.items()
            ):
                continue
            if all(
                all(
                    fragment in _record_text(record, field, nodes)
                    for fragment in fragments
                )
                for field, fragments in required.items()
            ):
                matched = True
                break
        if not matched:
            raise ExecutionError(f"quality case failed: {case['case_id']}")
        passed.append(case["case_id"])
    return {
        "status": "passed",
        "scope": "bounded_explicit_cases_only",
        "passed_cases": len(passed),
        "case_ids": passed,
        "limitation": "does not prove complete knowledge preservation",
    }


def _completed_execution(
    args: Any,
    plan: dict[str, Any],
    approvals: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any] | None:
    staging = args.staging.expanduser().resolve()
    report_path = staging / "execution-result.json"
    journal_path = staging / "graph-update" / "execution-journal.json"
    recovering = not report_path.is_file()
    if recovering and not journal_path.is_file():
        return None
    selected = _approved_rows(args, plan, approvals, allow_completed=True)
    journal = _read_json(journal_path) if recovering else {}
    if recovering:
        if journal.get("schema_version") != "knowledge-execution-journal-1.0":
            raise ExecutionError("execution journal has an unsupported schema")
        current_state = _read_json(args.state.expanduser().resolve())
        if current_state != journal.get("target_state"):
            return None
        report = journal.get("report")
        if not isinstance(report, dict):
            raise ExecutionError("execution journal has no retained report")
    else:
        report = _read_json(report_path)
    if (
        report.get("schema_version") != EXECUTION_SCHEMA
        or report.get("status") != "completed"
        or report.get("book") != plan["book"]
        or report.get("counts", {}).get("approved") != len(selected)
        or len(report.get("results", [])) != len(selected)
    ):
        raise ExecutionError("retained execution result does not match this approval")
    results = {
        (row.get("book_id"), row.get("document_id")): row for row in report["results"]
    }
    if len(results) != len(selected):
        raise ExecutionError("retained execution result has duplicate identities")
    for row, _approval, frozen in selected:
        result = results.get((row["book_id"], row["document_id"]))
        if (
            result is None
            or result.get("source_version") != row["execution"]["source_version"]
        ):
            raise ExecutionError(
                "retained execution result has a different source version"
            )
        current = staging / "execution-corpus" / f"source-[{row['document_id']}].md"
        if not current.is_file() or current.read_bytes() != frozen.read_bytes():
            raise ExecutionError(
                "retained execution corpus does not match approved raw"
            )
    case_ids = [
        case["case_id"] for case in _quality_case_definitions(quality, selected)
    ]
    if report.get("quality_gate", {}).get("case_ids", []) != case_ids:
        raise ExecutionError("quality cases changed after execution")
    if selected:
        transaction = report.get("transaction") or {}
        candidate = staging / "graph-update" / "candidate"
        if transaction.get("candidate") != str(candidate):
            raise ExecutionError(
                "retained execution transaction is not bound to staging"
            )
        graph_path = args.graph_dir.expanduser().resolve() / "graph.json"
        if (
            not graph_path.is_file()
            or graph_path.read_bytes() != (candidate / "graph.json").read_bytes()
        ):
            raise ExecutionError(
                "published graph no longer matches the retained candidate"
            )
        # Case identity alone does not bind its assertions: operators can amend
        # requirements without renaming the case, including during recovery.
        retained_graph = _validate_candidate_graph(candidate / "graph.json", selected)
        _validate_quality_cases(quality, retained_graph, selected)
    if recovering:
        if journal.get("plan") != str(staging / "plan.json") or journal.get(
            "candidate"
        ) != str(staging / "graph-update" / "candidate"):
            raise ExecutionError("execution journal is not bound to this plan")
        publication = _read_json(staging / "graph-update" / "result.json")
        if publication.get("status") != "completed" or publication.get(
            "candidate"
        ) != str(staging / "graph-update" / "candidate"):
            raise ExecutionError("execution journal lacks publication evidence")
        original_state = journal.get("original_state") or {}
        if original_state.get("version") == 2:
            original_state = canonical_view(original_state, plan["book"])
        for row, _approval, _frozen in selected:
            original_record = (original_state.get("documents") or {}).get(
                str(row["document_id"])
            )
            if (
                not isinstance(original_record, dict)
                or original_record.get("observation") != row.get("observation")
                or original_record.get("corpus") != row.get("corpus")
                or original_record.get("graph") != row.get("graph")
                or original_record.get("pending") != row.get("pending")
            ):
                raise ExecutionError("execution journal original state does not match")
        publish_reports(
            [
                (
                    report_path,
                    json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                ),
                (
                    journal_path,
                    json.dumps(
                        {**journal, "status": "recovered"},
                        ensure_ascii=False,
                        indent=1,
                    )
                    + "\n",
                ),
            ]
        )
    return report


def _protect_execution_inputs(
    args: Any,
    plan: dict[str, Any],
    approvals_path: Path,
    quality_path: Path,
) -> Path:
    staging = args.staging.expanduser().resolve()
    plan_path = staging / "plan.json"
    snapshot_path = args.snapshot.expanduser().resolve()
    state_path = args.state.expanduser().resolve()
    graph_dir = args.graph_dir.expanduser().resolve()
    snapshot = _load_snapshot(snapshot_path)
    protected = {plan_path, snapshot_path, approvals_path, quality_path}
    derived_raw = []
    for row in plan.get("results", []):
        if isinstance(row, dict) and isinstance(row.get("execution"), dict):
            raw = _resolve_raw(row["execution"].get("raw"), plan_path)
            if raw:
                protected.add(raw)
            derived_raw.append(
                staging / "execution-corpus" / f"source-[{row.get('document_id')}].md"
            )
    exports = snapshot.get("exports") or {}
    for document_id, document in snapshot["documents"].items():
        reference = document.get("raw") or (exports.get(document_id) or {}).get("raw")
        raw = _resolve_raw(reference, snapshot_path)
        if raw:
            protected.add(raw)
    state = _load_state(state_path, plan["book"])
    for record in (state.get("documents") or {}).values():
        if not isinstance(record, dict):
            continue
        for section in ("graph", "corpus"):
            value = record.get(section)
            if isinstance(value, dict):
                raw = _resolve_raw(value.get("raw"), state_path)
                if raw:
                    protected.add(raw)
    graph_staging = staging / "graph-update"
    outputs = [
        state_path,
        state_lock_path(state_path),
        staging / "execution-result.json",
        staging / "execution-manifest.json",
        staging / "execution-corpus",
        *derived_raw,
        graph_staging,
        *(
            graph_staging / name
            for name in (
                "base-state.json",
                "candidate-ready.json",
                "candidate-result.json",
                "candidate-validation.json",
                "result.json",
                "extraction.json",
                "extraction-failed.json",
                "extraction-usage.json",
                "tokens.json",
                "execution-journal.json",
            )
        ),
        graph_staging / "candidate",
        *(
            graph_staging / "candidate" / name
            for name in (
                "graph.json",
                "manifest.json",
                ".graphify_analysis.json",
                ".graphify_labels.json",
                ".graphify_labels.json.sig",
                "GRAPH_REPORT.md",
            )
        ),
        graph_staging / "graph-backup",
        *(
            graph_staging / "graph-backup" / name
            for name in (
                "graph.json",
                "manifest.json",
                ".graphify_analysis.json",
                ".graphify_labels.json",
                ".graphify_labels.json.sig",
                "GRAPH_REPORT.md",
            )
        ),
        graph_dir,
        *(
            graph_dir / name
            for name in (
                "graph.json",
                "manifest.json",
                ".graphify_analysis.json",
                ".graphify_labels.json",
                ".graphify_labels.json.sig",
                "GRAPH_REPORT.md",
            )
        ),
    ]
    validate_outputs(outputs, protected - {state_path})
    validate_outputs([state_path], set(outputs) - {state_path})
    return state_path


def _execute(args: Any) -> dict[str, Any]:
    staging = args.staging.expanduser().resolve()
    plan_path = staging / "plan.json"
    plan = _read_json(plan_path)
    if plan.get("schema_version") != "knowledge-plan-2.0":
        raise ExecutionError("plan has an unsupported schema")
    approvals_path = args.approvals.expanduser().resolve()
    quality_path = args.quality_cases.expanduser().resolve()
    approvals = _bound_document(approvals_path, plan_path, APPROVAL_SCHEMA)
    quality = _bound_document(quality_path, plan_path, QUALITY_SCHEMA)
    state_path = _protect_execution_inputs(args, plan, approvals_path, quality_path)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with state_lock(state_path):
        return _execute_locked(args, plan, approvals, quality)


def _execute_locked(
    args: Any,
    plan: dict[str, Any],
    approvals: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    staging = args.staging.expanduser().resolve()
    completed = _completed_execution(args, plan, approvals, quality)
    if completed is not None:
        return completed
    selected = _approved_rows(args, plan, approvals)
    counts = _empty_counts()
    counts["approved"] = len(selected)
    counts["executions"] = len(selected)
    quality_report: dict[str, Any] = {
        "status": "not_applicable",
        "scope": "bounded_explicit_cases_only",
    }
    results = []
    tokens: dict[str, Any] = {"status": "unknown", "evidence": "none"}
    usage_report: dict[str, Any] = {
        "external_calls_evidence": "not_applicable",
        "token_evidence": "not_applicable",
    }
    transaction = None
    if selected:
        _quality_case_definitions(quality, selected)
        graph_dir = args.graph_dir.expanduser().resolve()
        if not (graph_dir / "graph.json").is_file():
            raise ExecutionError("an existing graph baseline is required")
        state_path = args.state.expanduser().resolve()
        state_before = state_path.read_bytes()
        corpus = staging / "execution-corpus"
        old_graph = _read_json(graph_dir / "graph.json")
        source_urls = {}
        for record in [
            *old_graph.get("nodes", []),
            *old_graph.get("links", []),
            *old_graph.get("hyperedges", []),
        ]:
            identity = doc_id(str(record.get("source_file") or ""))
            url = record.get("source_url")
            if identity and isinstance(url, str) and url:
                source_urls.setdefault(identity, url)
        exported = []
        for row, _approval, frozen in selected:
            name = f"source-[{row['document_id']}].md"
            _freeze_source(frozen, corpus / name)
            exported_row = {"id": row["document_id"], "file": name}
            url = source_urls.get(str(row["document_id"]))
            if url:
                exported_row["url"] = url
            exported.append(exported_row)
        manifest_path = staging / "execution-manifest.json"
        publish_reports(
            [
                (
                    manifest_path,
                    json.dumps(
                        {"exported": exported, "deleted": []},
                        ensure_ascii=False,
                        indent=1,
                    )
                    + "\n",
                )
            ]
        )
        candidate_result = _run_graphify(
            args, "--candidate-only", corpus, manifest_path
        )
        if candidate_result.get("status") != "candidate_ready":
            raise ExecutionError("Graphify did not retain a validated candidate")
        candidate = Path(candidate_result["candidate"])
        if state_path.read_bytes() != state_before:
            raise ExecutionError("knowledge state changed concurrently")
        candidate_state = _load_state(state_path, plan["book"])
        if isinstance(candidate_state, BookState):
            for row, _approval, _frozen in selected:
                candidate_state["documents"][str(row["document_id"])].setdefault(
                    "steps", {}
                )["graph_candidate"] = {
                    "status": "completed",
                    "recorded_at": now_iso(),
                    "candidate": str(candidate),
                    "version": row["execution"]["source_version"],
                }
            save_view(state_path, candidate_state)
            state_before = state_path.read_bytes()
        graph = _validate_candidate_graph(candidate / "graph.json", selected)
        quality_report = _validate_quality_cases(quality, graph, selected)
        if state_path.read_bytes() != state_before:
            raise ExecutionError("knowledge state changed concurrently")
        if {
            row["document_id"] for row, _, _ in _approved_rows(args, plan, approvals)
        } != {row["document_id"] for row, _, _ in selected}:
            raise ExecutionError("approved inputs changed before publication")
        usage = candidate_result.get("usage") or {}
        usage_report = usage
        counts["external_calls"] = usage.get("external_calls")
        counts["cache_hits"] = usage.get("cache_hits", 0)
        counts["graph_successes"] = len(selected)
        if isinstance(usage.get("tokens"), dict):
            tokens = {
                **usage["tokens"],
                "evidence": usage.get("token_evidence", "unknown"),
            }
        original_state = _read_json(state_path)
        state = _load_state(state_path, plan["book"])
        snapshot = _load_snapshot(args.snapshot.expanduser().resolve())
        for row, _approval, frozen in selected:
            doc_id_value = str(row["document_id"])
            observed = snapshot["documents"][doc_id_value]
            record = state["documents"][doc_id_value]
            record["graph"] = {
                "status": "covered",
                "coverage": "approved_whole_document",
                "version": row["execution"]["source_version"],
                "raw": _relative_raw(frozen, state_path),
                "metadata": {
                    "name": observed["title"],
                    "logical_path": "/".join(
                        str(value) for value in observed.get("toc_path") or []
                    ),
                    "source_ref": f"{row['book_id']}:{row['document_id']}",
                },
            }
            record.setdefault("steps", {})["graph_publish"] = {
                "status": "completed",
                "recorded_at": now_iso(),
                "version": row["execution"]["source_version"],
            }
            record["pending"] = []
            results.append(
                {
                    "book_id": row["book_id"],
                    "document_id": row["document_id"],
                    "status": "published",
                    "source_scope": "whole_document",
                    "source_version": row["execution"]["source_version"],
                    "previous_graph_version": row["execution"][
                        "previous_graph_version"
                    ],
                }
            )
        if isinstance(state, BookState):
            state.root["active_run"] = None
        state = root_state(state)
        transaction = {
            "candidate": str(candidate),
            "backup": str(staging / "graph-update" / "graph-backup"),
            "journal": str(staging / "graph-update" / "execution-journal.json"),
            "resume": "reuse_retained_candidate",
        }
        report = {
            "schema_version": EXECUTION_SCHEMA,
            "status": "completed",
            "book": plan["book"],
            "counts": counts,
            "results": results,
            "tokens": tokens,
            "usage": usage_report,
            "cost": {"status": "unknown", "evidence": "none"},
            "quality_gate": quality_report,
            "transaction": transaction,
        }
        journal = {
            "schema_version": "knowledge-execution-journal-1.0",
            "status": "prepared",
            "plan": str(staging / "plan.json"),
            "candidate": str(candidate),
            "approvals": str(args.approvals.expanduser().resolve()),
            "quality_cases": str(args.quality_cases.expanduser().resolve()),
            "approved_inputs": [
                {
                    "book_id": row["book_id"],
                    "document_id": row["document_id"],
                    "source_version": row["execution"]["source_version"],
                    "raw": row["execution"]["raw"],
                    "previous_raw": row["execution"].get("previous_raw"),
                }
                for row, _approval, _frozen in selected
            ],
            "original_state": original_state,
            "target_state": state,
            "report": report,
        }
        publish_reports(
            [
                (
                    staging / "graph-update" / "execution-journal.json",
                    json.dumps(journal, ensure_ascii=False, indent=1) + "\n",
                )
            ]
        )
        published = _run_graphify(args, "--publish-validated", corpus, manifest_path)
        if published.get("status") != "completed":
            raise ExecutionError("Graphify publication did not complete")
        if (graph_dir / "graph.json").read_bytes() != (
            candidate / "graph.json"
        ).read_bytes():
            raise ExecutionError("published graph differs from the retained candidate")
        if state_path.read_bytes() != state_before:
            raise ExecutionError("knowledge state changed concurrently")
        publish_reports(
            [
                (
                    state_path,
                    json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                ),
                (
                    staging / "execution-result.json",
                    json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                ),
            ]
        )
        return report
    report = {
        "schema_version": EXECUTION_SCHEMA,
        "status": "completed",
        "book": plan["book"],
        "counts": counts,
        "results": results,
        "tokens": tokens,
        "usage": usage_report,
        "cost": {"status": "unknown", "evidence": "none"},
        "quality_gate": quality_report,
        "transaction": transaction,
    }
    current = _load_state(args.state.expanduser().resolve(), plan["book"])
    if isinstance(current, BookState):
        current.root["active_run"] = None
        save_view(args.state.expanduser().resolve(), current)
    publish_reports(
        [
            (
                staging / "execution-result.json",
                json.dumps(report, ensure_ascii=False, indent=1) + "\n",
            )
        ]
    )
    return report


def run_execution(args: Any) -> int:
    try:
        _execute(args)
    except (OSError, ExecutionError, ValueError) as error:
        print(f"knowledge sync: {error}", file=sys.stderr)
        return 1
    return 0
