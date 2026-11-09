"""Read-only body knowledge reuse and incremental unit reports.

Neither resource files nor OCR artifacts/configuration participate in this path.
Normalized equality establishes bounded textual reuse; fuzzy alignment only
proposes a revision for review, never semantic equivalence.
"""

import json
import re
from collections import Counter
from typing import Any

from .io import InputError, InputReader
from .knowledge_units import (
    BodyScopeError,
    Unit,
    _prose_key,
    _table_key,
    align_units,
    extract_units,
)
from .model import CompareOptions, Document

KINDS = ("reuse", "add", "revise", "old_only_review", "unresolved")
STATUSES = ("REUSE", "DELTA", "REVIEW", "NO_BODY", "UNKNOWN")
GRAPHIFY_ACTIONS = ("KEEP_EXISTING", "HOLD_EXISTING", "BODY_DELTA_CANDIDATE")
_RICH_LAYOUT_TAG = re.compile(
    r"<(?:font|span|u|div|p|center|b|strong|em|i|table|tr|td|th|thead|tbody|tfoot|br)\b",
    re.IGNORECASE,
)
_RICH_LAYOUT_LITERAL = re.compile(
    r"<(?:pre|code|script|style)\b|white-space\s*:", re.IGNORECASE
)


def _rich_layout_key(unit: Unit) -> str | None:
    """Project strict rich-layout artifacts for planning, never unit reuse.

    Indented rich HTML/table fragments can be extracted as literal code. Match
    their normalized body or table representation to a plain counterpart while
    retaining the original comparison kind and all nonempty fact bindings.
    Executable, preformatted and unknown literal code never enters this view.
    """
    text = unit.text
    first = text.lstrip()
    rich_table = unit.kind == "table" or (
        first.startswith("|") and _RICH_LAYOUT_TAG.search(text)
    )
    rich_prose = _RICH_LAYOUT_TAG.match(first)
    if unit.kind not in {"code", "paragraph", "table"}:
        return None
    if unit.kind == "code" and not (rich_table or rich_prose):
        return None
    if _RICH_LAYOUT_LITERAL.search(text):
        return None
    if rich_table:
        return _table_key(text)
    # Empty rich wrappers are presentation-only too; do not turn their changed
    # markup into a new body candidate merely because the projected text is empty.
    return "prose:" + _prose_key(text)


def _graphify_plan(row: dict[str, Any]) -> dict[str, Any]:
    """Separate comparison uncertainty from permission to spend on extraction.

    The current Graphify caller replaces whole source ownership. These are
    planning candidates only, never a list that can be passed to it as patches.
    """
    unavailable = row["status"] == "UNKNOWN" or not row["comparison_complete"]
    candidates = [
        index
        for index, unit in enumerate(row["units"])
        if unit["new"] is not None
        and unit["unit_type"] != "heading"
        and not unit["body_seen_before"]
        and not unit["presentation_seen_before"]
        and (
            unit["kind"] in {"add", "revise"}
            or (
                unit["kind"] == "unresolved"
                and unit["basis"] == "AMBIGUOUS_BODY_ALIGNMENT"
            )
        )
    ]
    context_review = [
        index
        for index, unit in enumerate(row["units"])
        if unit["new"] is not None
        and unit["kind"] != "reuse"
        and (unit["body_seen_before"] or unit["unit_type"] == "heading")
    ]
    presentation_review = [
        index
        for index, unit in enumerate(row["units"])
        if unit["new"] is not None
        and unit["kind"] != "reuse"
        and unit["presentation_seen_before"]
    ]
    action = (
        "HOLD_EXISTING"
        if unavailable
        else "BODY_DELTA_CANDIDATE"
        if candidates
        else "KEEP_EXISTING"
        if row["status"] == "REUSE"
        else "HOLD_EXISTING"
    )
    return {
        "action": action,
        "plan_only": True,
        "automatic_extraction_allowed": False,
        "full_document_fallback": False,
        "candidate_unit_indexes": candidates if not unavailable else [],
        "context_review_unit_indexes": context_review if not unavailable else [],
        "presentation_review_unit_indexes": presentation_review
        if not unavailable
        else [],
        "candidate_body_chars": (
            sum(row["units"][index]["new_body_chars"] for index in candidates)
            if not unavailable
            else 0
        ),
    }


def _read_units(document: Document, reader: InputReader) -> list[Unit]:
    reader.begin_document(document.identity)
    try:
        text = reader.read_bytes(document.path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("TEXT_ENCODING_UNSUPPORTED") from exc
    try:
        return extract_units(text, document.body_scope)
    except BodyScopeError as exc:
        raise InputError(exc.reason_code) from exc


def _reference(document: Document, unit: Unit) -> dict[str, Any]:
    return {
        "knowledge_base": document.knowledge_base,
        "document_id": document.document_id,
        "start_line": unit.start_line,
        "end_line": unit.end_line,
    }


def _index_key(document: Document, unit: Unit) -> tuple[Any, ...]:
    # Generic headings do not establish that two documents concern one subject.
    return (
        document.knowledge_base,
        document.metadata["name"],
        document.metadata["logical_path"],
        unit.kind,
        unit.context,
        unit.key,
    )


def _error(document: Document, reason: str, side: str) -> dict[str, str]:
    return {
        "knowledge_base": document.knowledge_base,
        "document_id": document.document_id,
        "side": side,
        "reason_code": reason,
    }


def _status(items: list[dict[str, Any]]) -> str:
    kinds = {item["kind"] for item in items}
    if not kinds:
        return "NO_BODY"
    if kinds & {"unresolved", "old_only_review", "revise"}:
        return "REVIEW"
    if "add" in kinds:
        return "DELTA"
    return "REUSE"


def compare_knowledge_documents(
    baseline: list[Document],
    incoming: list[Document],
    options: CompareOptions,
    reader: InputReader,
) -> dict[str, Any]:
    """Compare body units, retaining partial reuse even when other units differ."""
    old_by_id = {document.identity: document for document in baseline}
    old_units: dict[tuple[str, str], list[Unit]] = {}
    failed_baselines: dict[tuple[str, str], str] = {}
    index: dict[tuple[Any, ...], tuple[Document, Unit, str]] = {}
    errors: list[dict[str, str]] = []
    for document in sorted(baseline, key=lambda d: d.identity):
        try:
            units = _read_units(document, reader)
        except InputError as exc:
            failed_baselines[document.identity] = exc.reason_code
            errors.append(_error(document, exc.reason_code, "baseline"))
            continue
        old_units[document.identity] = units
        for unit in units:
            index.setdefault(_index_key(document, unit), (document, unit, "baseline"))

    incomplete_books = {identity[0] for identity in failed_baselines}
    results = []
    for document in sorted(incoming, key=lambda d: d.identity):
        old = old_by_id.get(document.identity)
        row: dict[str, Any] = {
            "knowledge_base": document.knowledge_base,
            "document_id": document.document_id,
            "status": "UNKNOWN",
            "counts": dict.fromkeys(KINDS, 0),
            "units": [],
            "reason_codes": [],
            "comparison_complete": False,
            "evidence_truncated": False,
            "source_limitations": (
                ["HISTORICAL_UNMARKED_TEXT_ORIGIN_UNVERIFIED"]
                if document.content_kind == "historical_enriched"
                or (old and old.content_kind == "historical_enriched")
                else []
            ),
        }
        results.append(row)
        try:
            new_units = _read_units(document, reader)
        except InputError as exc:
            row["reason_codes"] = [exc.reason_code]
            errors.append(_error(document, exc.reason_code, "incoming"))
            continue
        if document.identity in failed_baselines:
            row["reason_codes"] = [failed_baselines[document.identity]]
            continue
        aligned = align_units(
            old_units.get(document.identity, []),
            new_units,
            max_work=options.max_diff_work,
        )
        observed_old_body = {
            (unit.kind, unit.key): unit for unit in old_units.get(document.identity, [])
        }
        observed_old_layout = {
            key: unit
            for unit in old_units.get(document.identity, [])
            if (key := _rich_layout_key(unit)) is not None
        }
        evidence_remaining = options.max_evidence_chars
        for item in aligned["items"]:
            previous = item["old"]
            current = item["new"]
            source = old
            source_side = "baseline"
            kind = item["kind"]
            basis = item["basis"]
            similarity = item.get("similarity")
            if current is not None and previous is None and kind == "add":
                match = (
                    index.get(_index_key(document, current))
                    if document.metadata["name"].strip()
                    else None
                )
                if match is not None:
                    source, previous, source_side = match
                    kind = "reuse"
                    basis = (
                        "BASELINE_CONTEXTUAL_BODY_REUSE"
                        if source_side == "baseline"
                        else "INCOMING_CONTEXTUAL_BODY_REUSE"
                    )
                    similarity = 1.0
                elif kind == "add" and document.knowledge_base in incomplete_books:
                    kind = "unresolved"
                    basis = "BASELINE_BODY_INCOMPLETE"
            if (
                kind == "reuse"
                and old is not None
                and old.metadata["name"] != document.metadata["name"]
            ):
                # Document titles can carry the subject of generic body rules.
                kind = "unresolved"
                basis = "DOCUMENT_SUBJECT_CONTEXT_CHANGED"
            layout_key = (
                _rich_layout_key(current)
                if current is not None
                and (current.kind, current.key) not in observed_old_body
                else None
            )
            layout_match = (
                observed_old_layout.get(layout_key) if layout_key is not None else None
            )
            located = current if current is not None else previous
            if located is None:
                raise AssertionError("alignment item has no source unit")
            entry = {
                "kind": kind,
                "unit_type": located.kind,
                "new_body_chars": len(current.text) if current is not None else 0,
                # Identical wording in another context is not knowledge
                # equivalence. Preserve context review without re-extracting
                # that already observed wording as if it were new text.
                "body_seen_before": bool(
                    current is not None
                    and (current.kind, current.key) in observed_old_body
                ),
                "existing_body": (
                    _reference(old, observed_old_body[(current.kind, current.key)])
                    if current is not None
                    and old is not None
                    and (current.kind, current.key) in observed_old_body
                    else None
                ),
                "presentation_seen_before": layout_match is not None,
                "existing_presentation_body": (
                    _reference(old, layout_match)
                    if old is not None and layout_match is not None
                    else None
                ),
                "basis": basis,
                "similarity": similarity,
                "old": _reference(source, previous)
                if previous is not None and source is not None
                else None,
                "old_side": source_side if previous is not None else None,
                "new": _reference(document, current) if current is not None else None,
            }
            if options.include_evidence:
                evidence = {}
                for label, unit in (("old", previous), ("new", current)):
                    if unit is not None:
                        excerpt = unit.text[:evidence_remaining]
                        evidence_remaining -= len(excerpt)
                        evidence[label] = excerpt
                        if len(excerpt) < len(unit.text):
                            row["evidence_truncated"] = True
                entry["evidence"] = evidence
            row["units"].append(entry)
        # Later incoming documents may reuse this observed body with its source
        # reference. This is report advice, not extraction or publication.
        for unit in new_units:
            index.setdefault(_index_key(document, unit), (document, unit, "incoming"))
        counts = Counter(item["kind"] for item in row["units"])
        row["counts"] = {kind: counts[kind] for kind in KINDS}
        row["status"] = _status(row["units"])
        row["comparison_complete"] = bool(aligned["complete"])
        if not aligned["complete"]:
            row["reason_codes"].append("KNOWLEDGE_WORK_BUDGET_EXCEEDED")
        if any(item["basis"] == "BASELINE_BODY_INCOMPLETE" for item in row["units"]):
            row["reason_codes"].append("BASELINE_BODY_INCOMPLETE")
        if any(
            item["basis"] == "DOCUMENT_SUBJECT_CONTEXT_CHANGED" for item in row["units"]
        ):
            row["reason_codes"].append("DOCUMENT_SUBJECT_CONTEXT_CHANGED")

    # Cross-document evidence may be used anywhere in this report. Ordinary
    # observed drift invalidates the run's advice rather than retaining stale
    # equality claims. Callers must still supply immutable snapshots.
    if reader.changed_paths():
        for row in results:
            row.update(status="UNKNOWN", comparison_complete=False)
            row["reason_codes"] = sorted(set(row["reason_codes"]) | {"INPUT_CHANGED"})
            for entry in row["units"]:
                entry.update(kind="unresolved", basis="INPUT_CHANGED")
            row["counts"] = dict.fromkeys(KINDS, 0)
            row["counts"]["unresolved"] = len(row["units"])
        errors.append({"reason_code": "INPUT_CHANGED", "side": "snapshot"})
    statuses = Counter(row["status"] for row in results)
    totals: Counter[str] = Counter()
    for row in results:
        row["graphify_plan"] = _graphify_plan(row)
        totals.update(row["counts"])
    graphify_actions = Counter(row["graphify_plan"]["action"] for row in results)
    incoming_ids = {document.identity for document in incoming}
    return {
        "schema_version": "knowledge-1.0",
        "scope": "body_only",
        "rules": {
            "version": "body-units-v4",
            "reuse": "contextual_normalized_body_equality",
            "generated_regions": "outside_code_or_verified_generated_regions_v1",
            "similarity": "revision_candidate_only",
            "images_and_ocr": "excluded",
            "cross_document": "same_book_name_path_and_unit_context",
            "max_work_per_document": options.max_diff_work,
            "automatic_knowledge_merge": False,
            "graphify_execution": "plan_only_no_full_document_fallback",
        },
        "results": results,
        "baseline_only": [
            {"knowledge_base": d.knowledge_base, "document_id": d.document_id}
            for d in sorted(baseline, key=lambda d: d.identity)
            if d.identity not in incoming_ids
        ],
        "summary": {
            "total": len(results),
            "statuses": {status: statuses[status] for status in STATUSES},
            "units": {kind: totals[kind] for kind in KINDS},
            "graphify_plan": {
                "actions": {
                    action: graphify_actions[action] for action in GRAPHIFY_ACTIONS
                },
                "candidate_body_chars": sum(
                    row["graphify_plan"]["candidate_body_chars"] for row in results
                ),
                "automatic_full_document_extractions": 0,
                "context_review_documents": sum(
                    bool(row["graphify_plan"]["context_review_unit_indexes"])
                    for row in results
                ),
                "presentation_review_documents": sum(
                    bool(row["graphify_plan"]["presentation_review_unit_indexes"])
                    for row in results
                ),
            },
        },
        "errors": errors,
    }


def render_knowledge_report(report: dict[str, Any]) -> str:
    """Render the same bounded, opt-in evidence as the authoritative JSON."""
    lines = [
        "# mdcompare body knowledge report",
        "",
        "Scope: body_only. Images and OCR are excluded; no production writes.",
        "",
        f"Documents: {report['summary']['total']}",
        "",
        "Unit counts: " + str(report["summary"]["units"]),
        "",
        "Graphify plan (not an extraction queue): "
        + str(report["summary"]["graphify_plan"]),
        "",
        (
            "Candidates require confirmation. No automatic extraction or full-document fallback; "
            "the current Graphify source-replacement API cannot execute these as patches."
        ),
        "",
    ]
    for row in report["results"]:
        lines.extend(
            [
                f"## {row['knowledge_base']} / {row['document_id']}",
                "",
                f"Status: {row['status']}",
                "",
                "Counts: " + str(row["counts"]),
                "",
                "Graphify plan: " + str(row["graphify_plan"]),
                "",
                "```json",
                json.dumps(row["units"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines)
