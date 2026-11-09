"""Compare private knowledge reports without emitting corpus body text."""

import argparse
import json
from collections import Counter
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid or unreadable JSON: {path.name}") from exc


def candidate_units(row: dict) -> list[dict]:
    return [
        row["units"][index] for index in row["graphify_plan"]["candidate_unit_indexes"]
    ]


def metrics(report: dict) -> dict:
    rows = report["results"]
    candidates = [
        row for row in rows if row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    ]
    units = [unit for row in candidates for unit in candidate_units(row)]
    return {
        "documents": len(rows),
        "candidate_documents": len(candidates),
        "candidate_units": len(units),
        "candidate_body_chars": sum(unit["new_body_chars"] for unit in units),
        "candidate_unit_types": dict(
            sorted(Counter(unit["unit_type"] for unit in units).items())
        ),
        "candidate_body_chars_by_type": {
            kind: sum(
                unit["new_body_chars"] for unit in units if unit["unit_type"] == kind
            )
            for kind in sorted({unit["unit_type"] for unit in units})
        },
        "candidate_kinds": dict(
            sorted(Counter(unit["kind"] for unit in units).items())
        ),
        "plan_actions": dict(
            sorted(Counter(row["graphify_plan"]["action"] for row in rows).items())
        ),
        "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
        "errors": len(report["errors"]),
        "incomplete_documents": sum(not row["comparison_complete"] for row in rows),
    }


def identities(report: dict) -> dict[tuple[str, str], dict]:
    return {
        (row["knowledge_base"], row["document_id"]): row for row in report["results"]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before = read_json(args.before)
    after = read_json(args.after)
    old = identities(before)
    new = identities(after)
    if old.keys() != new.keys():
        raise ValueError("reports contain different document identities")
    transitions = Counter()
    for identity in old:
        before_action = old[identity]["graphify_plan"]["action"]
        after_action = new[identity]["graphify_plan"]["action"]
        transitions[(before_action, after_action)] += 1
    result = {
        "before": metrics(before),
        "after": metrics(after),
        "plan_transitions": [
            {
                "before": before_action,
                "after": after_action,
                "documents": count,
            }
            for (before_action, after_action), count in sorted(transitions.items())
        ],
        "new_candidate_documents": sum(
            new[identity]["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
            and old[identity]["graphify_plan"]["action"] != "BODY_DELTA_CANDIDATE"
            for identity in old
        ),
    }
    if args.labels:
        labels = read_json(args.labels)
        inventory = read_json(args.labels.parent / "inventory.json")
        label_by_id = {str(item["id"]): labels[str(item["n"])][0] for item in inventory}
        by_label = {}
        for label in sorted(set(label_by_id.values())):
            ids = {
                identity for identity, value in label_by_id.items() if value == label
            }
            by_label[label] = {
                "documents": len(ids),
                "before_candidates": sum(
                    old[("global-fund-platform/gom7xx", identity)]["graphify_plan"][
                        "action"
                    ]
                    == "BODY_DELTA_CANDIDATE"
                    for identity in ids
                ),
                "after_candidates": sum(
                    new[("global-fund-platform/gom7xx", identity)]["graphify_plan"][
                        "action"
                    ]
                    == "BODY_DELTA_CANDIDATE"
                    for identity in ids
                ),
            }
        result["fixed98_weak_labels_not_gold"] = by_label
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    args.output.chmod(0o600)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
