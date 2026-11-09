"""Aggregate complete full-candidate semantic judgments without source bodies."""

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

LABELS = {"equivalent_body", "knowledge_update", "unresolved"}


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid or unreadable JSON: {path.name}") from exc


def write_private(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)


def aggregate(root: Path, fixed_labels: Path | None) -> tuple[dict, dict]:
    units = read_json(root / "units.json")
    shards = read_json(root / "shards.json")
    by_id = {item["sample_id"]: item for item in units}
    if len(by_id) != len(units):
        raise ValueError("duplicate full-run unit ID")
    judgments = {}
    for item in units:
        if not item["has_old_witness"]:
            judgments[item["sample_id"]] = {
                "sample_id": item["sample_id"],
                "judgment": "unresolved",
                "old_witness_ids": [],
                "reason": "NO_OLD_EVIDENCE",
            }
    for shard in shards:
        evidence = {}
        try:
            for line in Path(shard["evidence"]).read_text().splitlines():
                value = json.loads(line)
                evidence[value["sample_id"]] = value
        except (OSError, ValueError) as exc:
            raise ValueError("invalid evidence shard") from exc
        report = read_json(Path(shard["judgments"]))
        ids = [item["sample_id"] for item in report["items"]]
        if len(ids) != len(set(ids)) or set(ids) != set(evidence):
            raise ValueError(f"incomplete judgment shard {shard['shard']}")
        for item in report["items"]:
            sample_id = item["sample_id"]
            if item["judgment"] not in LABELS:
                raise ValueError("invalid full-run judgment")
            if sample_id in judgments:
                raise ValueError("duplicate model judgment")
            if (
                item["judgment"] == "equivalent_body"
                and evidence[sample_id]["new"]["text_truncated"]
            ):
                raise ValueError("truncated unit cannot be equivalent")
            judgments[sample_id] = item
    if set(judgments) != set(by_id):
        raise ValueError("full-run judgments do not cover every candidate unit")

    documents = defaultdict(list)
    for item in units:
        documents[item["document_id"]].append(item)
    document_rows = []
    for document_id, items in documents.items():
        labels = [judgments[item["sample_id"]]["judgment"] for item in items]
        equivalent = sum(label == "equivalent_body" for label in labels)
        update = sum(label == "knowledge_update" for label in labels)
        unresolved = sum(label == "unresolved" for label in labels)
        category = (
            "ALL_EQUIVALENT"
            if equivalent == len(items)
            else "HAS_UPDATE"
            if update
            else "UNRESOLVED_ONLY"
        )
        document_rows.append(
            {
                "document_id": document_id,
                "candidate_units": len(items),
                "equivalent": equivalent,
                "knowledge_update": update,
                "unresolved": unresolved,
                "category": category,
                "candidate_body_chars": sum(item["new_body_chars"] for item in items),
                "equivalent_chars": sum(
                    item["new_body_chars"]
                    for item in items
                    if judgments[item["sample_id"]]["judgment"] == "equivalent_body"
                ),
            }
        )
    counts = Counter(item["judgment"] for item in judgments.values())
    document_counts = Counter(row["category"] for row in document_rows)
    total_chars = sum(item["new_body_chars"] for item in units)
    equivalent_chars = sum(
        item["new_body_chars"]
        for item in units
        if judgments[item["sample_id"]]["judgment"] == "equivalent_body"
    )
    result = {
        "schema_version": "mdcompare-full-semantic-result-1",
        "model": "kimi-coding/kimi-code",
        "before": {
            "candidate_documents": len(documents),
            "candidate_units": len(units),
            "candidate_body_chars": total_chars,
        },
        "after": {
            "candidate_documents": document_counts["HAS_UPDATE"]
            + document_counts["UNRESOLVED_ONLY"],
            "candidate_units": counts["knowledge_update"] + counts["unresolved"],
            "candidate_body_chars": total_chars - equivalent_chars,
        },
        "filtered": {
            "documents_all_units_equivalent": document_counts["ALL_EQUIVALENT"],
            "units_equivalent": counts["equivalent_body"],
            "equivalent_chars": equivalent_chars,
        },
        "judgments": {label: counts[label] for label in sorted(LABELS)},
        "document_categories": dict(document_counts),
        "subagent_shards": len(shards),
        "no_old_evidence_preheld": sum(not item["has_old_witness"] for item in units),
        "new_units_truncated": sum(item["new_text_truncated"] for item in units),
        "by_stratum": {
            stratum: dict(
                sorted(
                    Counter(
                        judgments[item["sample_id"]]["judgment"]
                        for item in units
                        if item["stratum"] == stratum
                    ).items()
                )
            )
            for stratum in sorted({item["stratum"] for item in units})
        },
        "by_type": {
            unit_type: dict(
                sorted(
                    Counter(
                        judgments[item["sample_id"]]["judgment"]
                        for item in units
                        if item["unit_type"] == unit_type
                    ).items()
                )
            )
            for unit_type in sorted({item["unit_type"] for item in units})
        },
        "documents": document_rows,
        "not_business_accuracy_or_graphify_execution": True,
    }
    if fixed_labels:
        labels = read_json(fixed_labels)
        inventory = read_json(fixed_labels.parent / "inventory.json")
        label_by_id = {str(item["id"]): labels[str(item["n"])][0] for item in inventory}
        before_by_label = Counter()
        after_by_label = Counter()
        for row in document_rows:
            label = label_by_id.get(row["document_id"])
            if label:
                before_by_label[label] += 1
                if row["category"] != "ALL_EQUIVALENT":
                    after_by_label[label] += 1
        result["fixed98_weak_labels_not_gold"] = {
            label: {
                "before_candidates": before_by_label[label],
                "after_candidates": after_by_label[label],
            }
            for label in sorted(before_by_label)
        }
    judgment_report = {
        "model": "kimi-coding/kimi-code",
        "items": [judgments[item["sample_id"]] for item in units],
    }
    return result, judgment_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judgments-output", type=Path, required=True)
    parser.add_argument("--fixed-labels", type=Path)
    args = parser.parse_args()
    result, judgments = aggregate(args.root, args.fixed_labels)
    write_private(args.output, result)
    write_private(args.judgments_output, judgments)
    print(json.dumps({key: result[key] for key in ("before", "after", "filtered")}))


if __name__ == "__main__":
    main()
