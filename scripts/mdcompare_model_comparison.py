"""Validate and compare three-model semantic judgments without body output."""

import argparse
import json
import os
from collections import Counter
from itertools import combinations
from pathlib import Path

LABELS = ("equivalent_body", "knowledge_update", "unresolved")
MODELS = ("terra", "luna", "kimi")


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid or unreadable JSON: {path.name}") from exc


def read_jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines()]
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid or unreadable JSONL: {path.name}") from exc


def real_judgments(root: Path, model: str, sample_ids: set[str]) -> dict[str, dict]:
    result = {}
    for shard in ("a", "b"):
        report = read_json(root / model / f"judgments-{shard}.json")
        for item in report["items"]:
            sample_id = item["sample_id"]
            if sample_id in result or sample_id not in sample_ids:
                raise ValueError(f"duplicate or unexpected sample ID: {sample_id}")
            if item["judgment"] not in LABELS:
                raise ValueError(f"invalid judgment: {sample_id}")
            result[sample_id] = item
    if set(result) != sample_ids:
        raise ValueError(f"{model}: incomplete real judgments")
    return result


def synthetic_judgments(root: Path, model: str, probe_ids: set[str]) -> dict:
    report = read_json(root / model / "synthetic.json")
    result = {}
    for item in report["items"]:
        probe_id = item["probe_id"]
        if probe_id in result or probe_id not in probe_ids:
            raise ValueError(f"duplicate or unexpected probe ID: {probe_id}")
        if item["judgment"] not in LABELS:
            raise ValueError(f"invalid synthetic judgment: {probe_id}")
        result[probe_id] = item
    if set(result) != probe_ids:
        raise ValueError(f"{model}: incomplete synthetic judgments")
    return result


def grouped_counts(items: list[dict], judgments: dict, key: str) -> dict:
    output = {}
    for value in sorted({item[key] for item in items}):
        subset = [item for item in items if item[key] == value]
        output[value] = dict(
            sorted(
                Counter(
                    judgments[item["sample_id"]]["judgment"] for item in subset
                ).items()
            )
        )
    return output


def compare_real(root: Path) -> dict:
    sample = read_json(root / "sample.json")["items"]
    sample_ids = {item["sample_id"] for item in sample}
    judgments = {model: real_judgments(root, model, sample_ids) for model in MODELS}
    evidence = {}
    for shard in ("a", "b"):
        for item in read_jsonl(root / f"evidence-{shard}.jsonl"):
            evidence[item["sample_id"]] = item
    models = {}
    for model in MODELS:
        counts = Counter(item["judgment"] for item in judgments[model].values())
        equivalent_ids = [
            item["sample_id"]
            for item in sample
            if judgments[model][item["sample_id"]]["judgment"] == "equivalent_body"
        ]
        unsafe_truncated = [
            sample_id
            for sample_id in equivalent_ids
            if evidence[sample_id]["new"]["text_truncated"]
        ]
        without_witness = [
            sample_id
            for sample_id in equivalent_ids
            if not evidence[sample_id]["old_witnesses"]
        ]
        valid_equivalent = (
            set(equivalent_ids) - set(unsafe_truncated) - set(without_witness)
        )
        models[model] = {
            "counts": {label: counts[label] for label in LABELS},
            "by_stratum": grouped_counts(sample, judgments[model], "stratum"),
            "by_type": grouped_counts(sample, judgments[model], "unit_type"),
            "equivalent_chars": sum(
                item["new_body_chars"]
                for item in sample
                if item["sample_id"] in valid_equivalent
            ),
            "truncated_equivalence_violations": unsafe_truncated,
            "equivalent_without_old_witness": without_witness,
        }
    pairwise = {}
    for left, right in combinations(MODELS, 2):
        same = [
            item["sample_id"]
            for item in sample
            if judgments[left][item["sample_id"]]["judgment"]
            == judgments[right][item["sample_id"]]["judgment"]
        ]
        pairwise[f"{left}:{right}"] = {
            "same": len(same),
            "percent": round(len(same) * 100 / len(sample), 2),
        }
    consensus = Counter()
    for item in sample:
        labels = [judgments[model][item["sample_id"]]["judgment"] for model in MODELS]
        if len(set(labels)) == 1:
            consensus[f"unanimous_{labels[0]}"] += 1
        if labels.count("equivalent_body") >= 2:
            consensus["majority_equivalent"] += 1
        if labels.count("knowledge_update") >= 2:
            consensus["majority_update"] += 1
        if "knowledge_update" in labels:
            consensus["any_update"] += 1
        if "unresolved" in labels:
            consensus["any_unresolved"] += 1
    return {
        "sample_units": len(sample),
        "sampled_documents": len({item["document_id"] for item in sample}),
        "models": models,
        "pairwise_agreement": pairwise,
        "consensus": dict(consensus),
        "not_population_accuracy": True,
    }


def compare_synthetic(root: Path) -> dict:
    source = read_jsonl(root / "synthetic-evidence.jsonl")
    expected_rows = read_jsonl(root.parents[1] / "dev_probes.jsonl")
    expected = {row["probe_id"]: row["expected"] for row in expected_rows}
    probe_ids = {row["probe_id"] for row in source}
    models = {}
    all_judgments = {}
    for model in MODELS:
        judgments = synthetic_judgments(root, model, probe_ids)
        all_judgments[model] = judgments
        confusion = Counter()
        false_equivalent = []
        for probe_id, expectation in expected.items():
            target = (
                "knowledge_update"
                if expectation == "knowledge_update"
                else "equivalent_body"
            )
            actual = judgments[probe_id]["judgment"]
            confusion[(target, actual)] += 1
            if target == "knowledge_update" and actual == "equivalent_body":
                false_equivalent.append(probe_id)
        models[model] = {
            "correct": sum(
                value
                for (target, actual), value in confusion.items()
                if target == actual
            ),
            "knowledge_updates_correct": confusion[
                ("knowledge_update", "knowledge_update")
            ],
            "knowledge_updates_false_equivalent": false_equivalent,
            "knowledge_updates_unresolved": confusion[
                ("knowledge_update", "unresolved")
            ],
            "equivalents_recovered": confusion[("equivalent_body", "equivalent_body")],
            "equivalents_false_update": confusion[
                ("equivalent_body", "knowledge_update")
            ],
            "confusion": [
                {"expected": target, "actual": actual, "count": count}
                for (target, actual), count in sorted(confusion.items())
            ],
        }
    majority = {}
    for probe_id, expectation in sorted(expected.items()):
        target = (
            "knowledge_update"
            if expectation == "knowledge_update"
            else "equivalent_body"
        )
        labels = [all_judgments[model][probe_id]["judgment"] for model in MODELS]
        majority_label = Counter(labels).most_common(1)[0][0]
        majority[probe_id] = {
            "expected": target,
            "majority": majority_label,
            "votes": dict(Counter(labels)),
        }
    return {
        "probes": len(probe_ids),
        "models": models,
        "majority": majority,
        "synthetic_development_only": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {
        "schema_version": "mdcompare-model-comparison-1",
        "real": compare_real(args.root),
        "synthetic": compare_synthetic(args.root),
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    os.chmod(args.output, 0o600)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
