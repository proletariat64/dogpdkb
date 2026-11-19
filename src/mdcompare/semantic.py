"""Offline authored-text routing; complete compiler-neutral evidence, never OCR work."""

import json
from bisect import bisect_right
from collections import Counter, defaultdict, deque
from dataclasses import asdict
from pathlib import Path

from .io import InputError, InputReader
from .knowledge import extract_document_units
from .knowledge_units import align_units
from .manifest import ManifestError, _unique_object

_RECOVERY_SCHEMA = "mdcompare-body-recovery-1"
_MARKER_LINES = {
    "",
    "<!-- mdocr:ocr-begin -->",
    "<!-- mdocr:ocr-end -->",
    "> 转写说明：",
    "> **转写说明：**",
    "-->",
}


def load_recovery(path: Path | None, reader: InputReader):
    """Validate public shape globally; document-specific replay errors stay local."""
    if path is None:
        return {}, []
    try:
        obj = json.loads(
            reader.read_bytes(path, max_bytes=16 * 1024 * 1024),
            object_pairs_hook=_unique_object,
        )
    except (ValueError, InputError) as exc:
        raise ManifestError("RECOVERY_MANIFEST_INVALID") from exc
    if (
        not isinstance(obj, dict)
        or obj.get("schema_version") != _RECOVERY_SCHEMA
        or not isinstance(obj.get("documents"), list)
    ):
        raise ManifestError("RECOVERY_MANIFEST_INVALID")
    records = {}
    for row in obj["documents"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("side"), str)
            or row["side"] not in {"baseline", "incoming"}
            or any(
                not isinstance(row.get(key), str) or not row[key]
                for key in ("knowledge_base", "document_id", "source_copy")
            )
            or not isinstance(row.get("insertions"), list)
        ):
            raise ManifestError("RECOVERY_MANIFEST_INVALID")
        key = row["knowledge_base"], row["document_id"], row["side"]
        if key in records:
            raise ManifestError("RECOVERY_MANIFEST_INVALID")
        records[key] = row
    return records, [path.parent / row["source_copy"] for row in records.values()]


def _apply_recovery(doc, record, manifest_path, reader):
    original = reader.read_bytes(doc.path)
    if record is None:
        return original, original, []
    copy_path = manifest_path.parent / record["source_copy"]
    if reader.read_bytes(copy_path) != original:
        raise InputError("RECOVERY_SOURCE_DRIFT")
    previous = -1
    repairs = []
    for insertion in record["insertions"]:
        if (
            not isinstance(insertion, dict)
            or type(insertion.get("byte_offset")) is not int
            or not isinstance(insertion.get("text"), str)
        ):
            raise InputError("RECOVERY_INSERTION_INVALID")
        offset, text = insertion["byte_offset"], insertion["text"]
        if (
            not 0 <= offset <= len(original)
            or offset < previous
            or not text
            or not text.endswith("\n")
            or any(line not in _MARKER_LINES for line in text.splitlines())
        ):
            raise InputError("RECOVERY_INSERTION_INVALID")
        try:
            original[:offset].decode("utf-8")
            text.encode("utf-8")
        except UnicodeError as exc:
            raise InputError("RECOVERY_INSERTION_INVALID") from exc
        repairs.append(
            {
                "byte_offset": offset,
                "text": text,
                "source_line": len(original[:offset].decode("utf-8").splitlines())
                + (not original[:offset] or original[:offset].endswith((b"\n", b"\r"))),
            }
        )
        previous = offset
    repaired = original
    for item in reversed(repairs):
        offset = item["byte_offset"]
        repaired = repaired[:offset] + item["text"].encode("utf-8") + repaired[offset:]
    return original, repaired, repairs


def _coordinate_map(original, repaired, repairs):
    if not repairs:
        return None

    def offsets(raw):
        result = [0]
        for line in raw.decode("utf-8").splitlines(keepends=True):
            result.append(result[-1] + len(line.encode("utf-8")))
        return result

    projected_offsets, original_offsets = offsets(repaired), offsets(original)
    result = []
    for position in projected_offsets:
        inserted = 0
        source_position = position
        for item in repairs:
            start = item["byte_offset"] + inserted
            size = len(item["text"].encode("utf-8"))
            if position < start:
                source_position = position - inserted
                break
            if position < start + size:
                source_position = item["byte_offset"]
                break
            inserted += size
        else:
            source_position = position - inserted
        # An inserted EOF footer can extend a unit across ignored generated
        # lines. Its source endpoint is the last real line, not a new line
        # invented past the immutable original's end.
        result.append(
            min(
                bisect_right(original_offsets, source_position),
                max(1, len(original_offsets) - 1),
            )
        )
    return result


def _unit_evidence(unit, coordinates):
    result = asdict(unit)
    if coordinates is not None:
        result.update(
            projected_start_line=unit.start_line,
            projected_end_line=unit.end_line,
            start_line=coordinates[unit.start_line - 1],
            end_line=coordinates[unit.end_line - 1],
        )
    return result


def _changes(before, after, aligned, indexes):
    """Reference canonical evidence once; exact matches consume occurrences once."""
    used = set()
    # A moved exact-text candidate must not consume an occurrence that the
    # contextual aligner has already assigned to a later revision.
    reserved = {
        id(item["old"])
        for item in aligned["items"][: len(after)]
        if item["old"] is not None
    }
    exact = defaultdict(deque)
    movable = defaultdict(dict)
    positions = {id(unit): position for position, unit in enumerate(before)}
    for unit in before:
        exact[unit.kind, unit.context, unit.key].append(unit)
        if id(unit) not in reserved:
            movable[unit.kind, unit.key][id(unit)] = unit
    operations = []
    for position, item in enumerate(aligned["items"][: len(after)]):
        incoming = after[position]
        previous = item["old"]
        operation = {
            "reuse": "equal",
            "add": "add",
            "revise": "modify",
            "unresolved": "uncertain",
        }[item["kind"]]
        if operation == "equal":
            matches = exact[incoming.kind, incoming.context, incoming.key]
            while matches and id(matches[0]) in used:
                matches.popleft()
            previous = matches.popleft() if matches else None
            if previous is None:
                operation = "add"
            elif positions[id(previous)] != position:
                operation = "move"
        elif previous is None:
            candidates = movable[incoming.kind, incoming.key]
            if len(candidates) == 1:
                previous, operation = next(iter(candidates.values())), "move"
        if previous is not None:
            used.add(id(previous))
            movable[previous.kind, previous.key].pop(id(previous), None)
        operations.append(
            {
                "operation": operation,
                "old_ref": indexes["baseline"][id(previous)]
                if previous is not None
                else None,
                "new_ref": indexes["incoming"][id(after[position])],
                "basis": item["basis"],
            }
        )
    operations.extend(
        {
            "operation": "delete",
            "old_ref": indexes["baseline"][id(unit)],
            "new_ref": None,
            "basis": "AUTHORED_BODY_NOT_PRESENT",
        }
        for unit in before
        if id(unit) not in used
    )
    return operations


def _source(doc):
    if doc is None:
        return None
    return {
        "path": str(doc.path.absolute()),
        "content_kind": doc.content_kind,
        "metadata": doc.metadata,
        "version": doc.provenance.get("version"),
    }


def compare_semantic(
    baseline,
    incoming,
    options,
    reader,
    sources,
    recovery=None,
    recovery_path=None,
    diff_sink=None,
):
    old_by_id = {d.identity: d for d in baseline}
    new_by_id = {d.identity: d for d in incoming}
    results, diffs = [], []
    save_diff = diff_sink or (lambda evidence: evidence)
    for identity, new in sorted(new_by_id.items()):
        reader.begin_document(identity)
        old = old_by_id.get(identity)
        row = {"knowledge_base": identity[0], "document_id": identity[1]}
        evidence = {
            **row,
            "source_inputs": {"baseline": _source(old), "incoming": _source(new)},
            "old_units": [],
            "new_units": [],
            "changes": [],
            "recovery": [],
        }
        views, units, indexes = {}, {}, {}
        try:
            for side, doc in (("baseline", old), ("incoming", new)):
                units[side], indexes[side] = [], {}
                if doc is None:
                    continue
                original, repaired, repairs = _apply_recovery(
                    doc, (recovery or {}).get((*identity, side)), recovery_path, reader
                )
                evidence["recovery"].extend({"side": side, **item} for item in repairs)
                try:
                    units[side] = extract_document_units(doc, repaired.decode("utf-8"))
                    views[side] = _coordinate_map(original, repaired, repairs)
                except UnicodeDecodeError as exc:
                    raise InputError("TEXT_ENCODING_UNSUPPORTED") from exc
                evidence["old_units" if side == "baseline" else "new_units"] = [
                    _unit_evidence(unit, views[side]) for unit in units[side]
                ]
                indexes[side] = {id(unit): i for i, unit in enumerate(units[side])}
        except InputError as exc:
            row.update(
                semantic_status="UNAVAILABLE",
                action="DEFER",
                reason_codes=[exc.reason_code],
                extraction={"available": False},
                comparison_complete=False,
            )
            evidence["reason_codes"] = row["reason_codes"]
            results.append(row)
            diffs.append(save_diff(evidence))
            continue
        before = [u for u in units["baseline"] if not u.exporter_title]
        after = [u for u in units["incoming"] if not u.exporter_title]
        aligned = align_units(before, after, max_work=options.max_diff_work)
        exact_sequence = old is not None and [
            (u.kind, u.context, u.key) for u in before
        ] == [(u.kind, u.context, u.key) for u in after]
        same_subject = old is not None and all(
            old.metadata.get(key) == new.metadata.get(key)
            for key in ("name", "logical_path")
        )
        if old is None:
            status, action, reason = "NEW", "COMPILE", "NEW_DOCUMENT"
        elif exact_sequence and same_subject:
            status, action, reason = (
                "EQUIVALENT",
                "SKIP",
                "CONTEXTUAL_NORMALIZED_EQUALITY",
            )
        elif not aligned["complete"] or any(
            item["kind"] == "unresolved" for item in aligned["items"]
        ):
            status, action, reason = "UNCERTAIN", "COMPILE", "BODY_ALIGNMENT_UNCERTAIN"
        else:
            status, action, reason = "CHANGED", "COMPILE", "AUTHORED_BODY_CHANGED"
        row.update(
            semantic_status=status,
            action=action,
            reason_codes=[reason],
            extraction={
                "available": True,
                "old_units": len(before),
                "new_units": len(after),
                "incoming_body_status": "AVAILABLE" if after else "NO_TEXT",
            },
            comparison_complete=aligned["complete"],
            advisory={"images_and_ocr": "EXCLUDED"},
        )
        evidence["reason_codes"] = row["reason_codes"]
        evidence["changes"] = _changes(before, after, aligned, indexes)
        results.append(row)
        diffs.append(save_diff(evidence))
    # Immutable snapshots remain the contract; detect ordinary concurrent drift
    # across every dependency, including an old side and explicit repair witness.
    affected = reader.affected_documents(reader.changed_paths())
    for row, diff in zip(results, diffs):
        if (row["knowledge_base"], row["document_id"]) in affected:
            row.update(
                semantic_status="UNAVAILABLE",
                action="DEFER",
                comparison_complete=False,
                extraction={"available": False},
                reason_codes=["INPUT_CHANGED"],
            )
            diff["reason_codes"] = ["INPUT_CHANGED"]
    report = {
        "schema_version": "semantic-1.0",
        "scope": "authored_body",
        "results": results,
        "rules": {
            "version": "authored-body-v1",
            "equivalence": "paired_contextual_sequence_equality",
            "uncertainty": "COMPILE",
            "images_and_ocr": "ADVISORY_ONLY",
            "model": "NONE",
        },
        "summary": {
            "total": len(results),
            "statuses": dict(Counter(r["semantic_status"] for r in results)),
            "actions": dict(Counter(r["action"] for r in results)),
            "baseline_only_advisory": [
                {"knowledge_base": kb, "document_id": did}
                for kb, did in sorted(old_by_id.keys() - new_by_id.keys())
            ],
        },
    }
    return report, {
        "schema_version": "mdcompare-semantic-diff-1",
        "sources": sources,
        "evidence_complete": not any(row["action"] == "DEFER" for row in results),
        "coordinate_system": "original_source_lines",
        "documents": diffs,
    }
