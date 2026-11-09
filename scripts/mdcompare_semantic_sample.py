"""Build a fixed, private body-unit sample for blinded semantic model review."""

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from pathlib import Path

from mdcompare.knowledge_units import _grams, extract_units

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_SAMPLE = (
    ROOT
    / "tmp/mdcompare-knowledge-research-20261107/filter-exploration-20261108"
    / "semantic-audit/sample-blind.json"
)
NEW_LIMIT = 8_000
OLD_LIMIT = 6_000
CONTEXT_LIMIT = 1_000


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid or unreadable JSON: {path.name}") from exc


def manifest(path: Path) -> dict[tuple[str, str], dict]:
    documents = read_json(path)["documents"]
    result = {(row["knowledge_base"], row["document_id"]): row for row in documents}
    if len(result) != len(documents):
        raise ValueError("duplicate document identity")
    return result


def source_path(manifest_path: Path, document: dict) -> Path:
    path = Path(document["path"])
    return path if path.is_absolute() else manifest_path.parent / path


def private_text(value: str, limit: int) -> tuple[str, bool]:
    return value[:limit], len(value) > limit


def location(unit: dict) -> tuple[int, int]:
    reference = unit["new"]
    if reference is None:
        raise ValueError("candidate unit has no new source location")
    return (reference["start_line"], reference["end_line"])


def find_unit(reference: dict, units: list):
    for unit in units:
        if (unit.start_line, unit.end_line) == (
            reference["start_line"],
            reference["end_line"],
        ):
            return unit
    raise ValueError("report source location does not match extracted unit")


def bounded_context(context: tuple[str, ...]) -> dict:
    text = " / ".join(context)
    value, truncated = private_text(text, CONTEXT_LIMIT)
    return {"text": value, "truncated": truncated}


def dice(old: str, new: str) -> float:
    before, after = _grams(old), _grams(new)
    if not before and not after:
        return 1.0
    return 2 * len(before & after) / (len(before) + len(after))


def selected_candidates(report: dict, per_kind: int, seed: int) -> list[dict]:
    excluded = set()
    if PREVIOUS_SAMPLE.exists():
        previous = read_json(PREVIOUS_SAMPLE)["items"]
        excluded = {
            (item["document_id"], item["new"]["start_line"], item["new"]["end_line"])
            for item in previous
        }
    groups = defaultdict(lambda: defaultdict(list))
    for row in report["results"]:
        for index in row["graphify_plan"]["candidate_unit_indexes"]:
            unit = row["units"][index]
            key = (row["document_id"], *location(unit))
            if key in excluded:
                continue
            stratum = "ambiguous" if unit["kind"] == "unresolved" else unit["kind"]
            groups[stratum][unit["unit_type"]].append((row, index, unit))
    selected = []
    for stratum in ("add", "revise", "ambiguous"):
        typed = groups[stratum]
        for unit_type, values in typed.items():
            random.Random(f"{seed}:{stratum}:{unit_type}").shuffle(values)
        types = sorted(typed)
        while (
            len([item for item in selected if item[2]["_stratum"] == stratum])
            < per_kind
        ):
            progressed = False
            for unit_type in types:
                if not typed[unit_type]:
                    continue
                row, index, unit = typed[unit_type].pop()
                unit = dict(unit, _stratum=stratum)
                selected.append((row, index, unit))
                progressed = True
                if (
                    len([item for item in selected if item[2]["_stratum"] == stratum])
                    >= per_kind
                ):
                    break
            if not progressed:
                break
    if len(selected) != per_kind * 3:
        raise ValueError("insufficient candidates for requested strata")
    return selected


def build(args: argparse.Namespace) -> dict:
    report = read_json(args.report)
    baseline_path = args.baseline.resolve()
    incoming_path = args.incoming.resolve()
    baseline = manifest(baseline_path)
    incoming = manifest(incoming_path)
    selected = selected_candidates(report, args.per_kind, args.seed)
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "tmp"):
        raise ValueError("private evidence output must remain under repo tmp/")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)

    sample_rows = []
    evidence = {"a": [], "b": []}
    units_cache = {}
    for position, (row, unit_index, item) in enumerate(selected, 1):
        sample_id = f"M{position:03d}"
        identity = (row["knowledge_base"], row["document_id"])
        if identity not in units_cache:
            old_units = []
            if identity in baseline:
                old_document = baseline[identity]
                old_units = extract_units(
                    source_path(baseline_path, old_document).read_text()
                )
            new_units = extract_units(
                source_path(incoming_path, incoming[identity]).read_text()
            )
            units_cache[identity] = old_units, new_units
        old_units, new_units = units_cache[identity]
        current = find_unit(item["new"], new_units)
        witnesses = []
        if item["old"] is not None:
            witnesses.append(find_unit(item["old"], old_units))
        else:
            ranked = sorted(
                (
                    (dice(previous.key, current.key), previous.start_line, previous)
                    for previous in old_units
                    if previous.kind == current.kind and previous.kind != "heading"
                ),
                key=lambda value: (value[0], -value[1]),
                reverse=True,
            )[:2]
            witnesses.extend(value[2] for value in ranked)
        witness_rows = []
        for witness_index, previous in enumerate(witnesses, 1):
            text, truncated = private_text(previous.text, OLD_LIMIT)
            witness_rows.append(
                {
                    "witness_id": f"{sample_id}-O{witness_index}",
                    "start_line": previous.start_line,
                    "end_line": previous.end_line,
                    "unit_type": previous.kind,
                    "context": bounded_context(previous.context),
                    "text": text,
                    "text_truncated": truncated,
                }
            )
        new_text, new_truncated = private_text(current.text, NEW_LIMIT)
        shard = "a" if position % 2 else "b"
        evidence[shard].append(
            {
                "sample_id": sample_id,
                "unit_type": current.kind,
                "new_context": bounded_context(current.context),
                "new": {
                    "start_line": current.start_line,
                    "end_line": current.end_line,
                    "text": new_text,
                    "text_truncated": new_truncated,
                },
                "old_witnesses": witness_rows,
                "judgment_scope": "this local body unit only; images and OCR excluded",
            }
        )
        sample_rows.append(
            {
                "sample_id": sample_id,
                "shard": shard,
                "stratum": item["_stratum"],
                "unit_type": current.kind,
                "document_id": row["document_id"],
                "unit_index": unit_index,
                "new": item["new"],
                "new_body_chars": item["new_body_chars"],
                "new_text_truncated": new_truncated,
                "old_witness_lines": [
                    {"start_line": value.start_line, "end_line": value.end_line}
                    for value in witnesses
                ],
            }
        )
    for shard, rows in evidence.items():
        path = output / f"evidence-{shard}.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        )
        os.chmod(path, 0o600)
    metadata = {
        "schema_version": "mdcompare-semantic-sample-1",
        "seed": args.seed,
        "source_rules_version": report["rules"]["version"],
        "candidate_documents": report["summary"]["graphify_plan"]["actions"][
            "BODY_DELTA_CANDIDATE"
        ],
        "sample_units": len(sample_rows),
        "previous_sample_excluded": True,
        "new_character_limit": NEW_LIMIT,
        "old_character_limit_per_witness": OLD_LIMIT,
        "items": sample_rows,
    }
    summary = {
        "sample_units": len(sample_rows),
        "by_stratum": dict(Counter(row["stratum"] for row in sample_rows)),
        "by_type": dict(Counter(row["unit_type"] for row in sample_rows)),
        "by_shard": dict(Counter(row["shard"] for row in sample_rows)),
        "sampled_document_ids": len({row["document_id"] for row in sample_rows}),
        "new_units_truncated": sum(row["new_text_truncated"] for row in sample_rows),
        "new_body_chars": sum(row["new_body_chars"] for row in sample_rows),
        "body_text_in_metadata": False,
    }
    for name, value in (("sample.json", metadata), ("summary.json", summary)):
        path = output / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        os.chmod(path, 0o600)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261109)
    parser.add_argument("--per-kind", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
