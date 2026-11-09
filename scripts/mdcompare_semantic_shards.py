"""Build bounded private evidence shards for full candidate semantic review."""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from mdcompare_semantic_sample import (
    bounded_context,
    dice,
    find_unit,
    manifest,
    private_text,
    read_json,
    source_path,
)

from mdcompare.knowledge_units import extract_units

ROOT = Path(__file__).resolve().parents[1]
MAX_UNITS_PER_SHARD = 60
MAX_EVIDENCE_CHARS_PER_SHARD = 120_000


def write_private(path: Path, content: str) -> None:
    path.write_text(content)
    os.chmod(path, 0o600)


def build(args: argparse.Namespace) -> dict:
    report = read_json(args.report)
    baseline_path = args.baseline.resolve()
    incoming_path = args.incoming.resolve()
    baseline = manifest(baseline_path)
    incoming = manifest(incoming_path)
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "tmp"):
        raise ValueError("private evidence output must remain under repo tmp/")
    if output.exists() and any(output.iterdir()):
        raise ValueError("output directory must be empty; retain prior experiments")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    (output / "judgments").mkdir(mode=0o700)

    shards = []
    metadata = []
    current_items = []
    current_chars = 0
    units_cache = {}

    def flush() -> None:
        nonlocal current_items, current_chars
        if not current_items:
            return
        shard_id = len(shards) + 1
        name = f"shard-{shard_id:03d}"
        write_private(
            output / f"{name}.jsonl",
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n" for item in current_items
            ),
        )
        shards.append(
            {
                "shard": shard_id,
                "evidence": str(output / f"{name}.jsonl"),
                "judgments": str(output / "judgments" / f"{name}.json"),
                "units": len(current_items),
                "evidence_chars": current_chars,
            }
        )
        current_items = []
        current_chars = 0

    for row in report["results"]:
        indexes = row["graphify_plan"]["candidate_unit_indexes"]
        if not indexes:
            continue
        identity = (row["knowledge_base"], row["document_id"])
        old_units = []
        if identity in baseline:
            old_units = extract_units(
                source_path(baseline_path, baseline[identity]).read_text()
            )
        new_units = extract_units(
            source_path(incoming_path, incoming[identity]).read_text()
        )
        units_cache[identity] = len(old_units), len(new_units)
        for unit_index in indexes:
            item = row["units"][unit_index]
            current = find_unit(item["new"], new_units)
            if item["old"] is not None:
                witnesses = [find_unit(item["old"], old_units)]
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
                witnesses = [value[2] for value in ranked]
            unit_id = f"F{len(metadata) + 1:05d}"
            new_text, new_truncated = private_text(current.text, 8_000)
            metadata.append(
                {
                    "sample_id": unit_id,
                    "document_id": row["document_id"],
                    "unit_index": unit_index,
                    "unit_type": current.kind,
                    "stratum": "ambiguous"
                    if item["kind"] == "unresolved"
                    else item["kind"],
                    "new": item["new"],
                    "new_body_chars": item["new_body_chars"],
                    "new_text_truncated": new_truncated,
                    "has_old_witness": bool(witnesses),
                }
            )
            # Without any old evidence, equivalence is impossible under the
            # review contract. Preserve the unit without spending a model call.
            if not witnesses:
                continue
            witness_rows = []
            for witness_index, previous in enumerate(witnesses, 1):
                text, truncated = private_text(previous.text, 6_000)
                witness_rows.append(
                    {
                        "witness_id": f"{unit_id}-O{witness_index}",
                        "start_line": previous.start_line,
                        "end_line": previous.end_line,
                        "unit_type": previous.kind,
                        "context": bounded_context(previous.context),
                        "text": text,
                        "text_truncated": truncated,
                    }
                )
            evidence = {
                "sample_id": unit_id,
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
            evidence_chars = len(new_text) + sum(
                len(witness["text"]) for witness in witness_rows
            )
            if current_items and (
                len(current_items) >= MAX_UNITS_PER_SHARD
                or current_chars + evidence_chars > MAX_EVIDENCE_CHARS_PER_SHARD
            ):
                flush()
            current_items.append(evidence)
            current_chars += evidence_chars
    flush()
    summary = {
        "schema_version": "mdcompare-full-semantic-shards-1",
        "rules_version": report["rules"]["version"],
        "candidate_documents": sum(
            row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
            for row in report["results"]
        ),
        "candidate_units": len(metadata),
        "candidate_body_chars": sum(item["new_body_chars"] for item in metadata),
        "evidence_chars_after_bounds": sum(shard["evidence_chars"] for shard in shards),
        "new_units_truncated": sum(item["new_text_truncated"] for item in metadata),
        "units_without_old_witness": sum(
            not item["has_old_witness"] for item in metadata
        ),
        "units_requiring_model": sum(item["has_old_witness"] for item in metadata),
        "shards": len(shards),
        "max_units_per_shard": MAX_UNITS_PER_SHARD,
        "max_evidence_chars_per_shard": MAX_EVIDENCE_CHARS_PER_SHARD,
        "units_by_stratum": dict(Counter(item["stratum"] for item in metadata)),
        "units_by_type": dict(Counter(item["unit_type"] for item in metadata)),
    }
    write_private(
        output / "units.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
    )
    write_private(
        output / "shards.json", json.dumps(shards, ensure_ascii=False, indent=2) + "\n"
    )
    write_private(
        output / "summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
