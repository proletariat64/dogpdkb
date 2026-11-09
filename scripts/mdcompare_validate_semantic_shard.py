"""Validate one semantic-review shard without printing private evidence."""

import argparse
import json
from pathlib import Path

LABELS = {"equivalent_body", "knowledge_update", "unresolved"}


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid or unreadable JSON: {path.name}") from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    args = parser.parse_args()
    evidence = {}
    try:
        for line in args.evidence.read_text().splitlines():
            item = json.loads(line)
            evidence[item["sample_id"]] = item
    except (OSError, ValueError) as exc:
        raise ValueError("invalid or unreadable evidence") from exc
    report = read_json(args.judgments)
    items = report["items"]
    ids = [item["sample_id"] for item in items]
    if len(ids) != len(set(ids)) or set(ids) != set(evidence):
        raise ValueError("judgment IDs are incomplete or duplicated")
    for item in items:
        if item["judgment"] not in LABELS:
            raise ValueError("invalid judgment label")
        evidence_item = evidence[item["sample_id"]]
        valid_witnesses = {
            witness["witness_id"] for witness in evidence_item["old_witnesses"]
        }
        if not set(item["old_witness_ids"]) <= valid_witnesses:
            raise ValueError("unknown old witness reference")
        if item["judgment"] == "equivalent_body":
            if evidence_item["new"]["text_truncated"]:
                raise ValueError("truncated new unit cannot be equivalent")
            if not valid_witnesses:
                raise ValueError("equivalence requires old evidence")
    print(f"validated {len(items)} semantic judgments")


if __name__ == "__main__":
    main()
