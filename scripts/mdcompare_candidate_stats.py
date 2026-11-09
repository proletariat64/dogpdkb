"""Aggregate character statistics for candidate documents, without body output."""

import argparse
import json
import math
import os
import statistics
from collections import Counter
from pathlib import Path

from mdcompare.knowledge_units import extract_units

BINS: tuple[tuple[int, int | None, str], ...] = (
    (0, 499, "0–499"),
    (500, 999, "500–999"),
    (1_000, 1_999, "1,000–1,999"),
    (2_000, 4_999, "2,000–4,999"),
    (5_000, 9_999, "5,000–9,999"),
    (10_000, 19_999, "10,000–19,999"),
    (20_000, 49_999, "20,000–49,999"),
    (50_000, 99_999, "50,000–99,999"),
    (100_000, 199_999, "100,000–199,999"),
    (200_000, None, "≥200,000"),
)


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


def character_counts(path: Path, body_scope=None) -> dict[str, int]:
    text = path.read_text()
    units = extract_units(text, body_scope)
    return {
        "raw_chars": len(text),
        "cleaned_body_chars": sum(len(unit.text) for unit in units),
        "cleaned_non_heading_chars": sum(
            len(unit.text) for unit in units if unit.kind != "heading"
        ),
    }


def percentile(values: list[int], fraction: float) -> int:
    """Nearest-rank percentile over a nonempty sorted list."""
    return values[max(0, math.ceil(fraction * len(values)) - 1)]


def summarize(values: list[int]) -> dict:
    ordered = sorted(values)
    return {
        "docs": len(values),
        "total_chars": sum(values),
        "mean_chars": round(statistics.mean(values), 1) if values else 0,
        "min_chars": ordered[0] if ordered else 0,
        "p25_chars": percentile(ordered, 0.25) if ordered else 0,
        "p50_chars": percentile(ordered, 0.50) if ordered else 0,
        "p75_chars": percentile(ordered, 0.75) if ordered else 0,
        "p90_chars": percentile(ordered, 0.90) if ordered else 0,
        "p95_chars": percentile(ordered, 0.95) if ordered else 0,
        "p99_chars": percentile(ordered, 0.99) if ordered else 0,
        "max_chars": ordered[-1] if ordered else 0,
    }


def distribution(values: list[int]) -> list[dict]:
    counts = Counter()
    for value in values:
        for lower, upper, label in BINS:
            if value >= lower and (upper is None or value <= upper):
                counts[label] += 1
                break
    return [
        {
            "range": label,
            "docs": counts[label],
            "percent": round(counts[label] * 100 / len(values), 2) if values else 0,
        }
        for _, _, label in BINS
    ]


def markdown_report(result: dict) -> str:
    lines = [
        "# mdcompare v3 candidate character statistics",
        "",
        (
            "Scope: 792 BODY_DELTA_CANDIDATE documents. Cleaned body counts sum "
            "the extracted unit source text after image/marked-OCR exclusion; "
            "remaining Markdown/HTML syntax is included. Headings are included "
            "unless the metric says non-heading. Missing original documents are "
            "not zero-filled."
        ),
        "",
        "## Totals and nearest-rank percentiles",
        "",
        "| Metric | Docs | Total | Mean | P25 | P50 | P75 | P90 | P95 | P99 | Max |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, summary in result["metrics"].items():
        lines.append(
            f"| {name} | {summary['docs']} | {summary['total_chars']:,} "
            f"| {summary['mean_chars']:,.1f} | {summary['p25_chars']:,} "
            f"| {summary['p50_chars']:,} | {summary['p75_chars']:,} "
            f"| {summary['p90_chars']:,} | {summary['p95_chars']:,} "
            f"| {summary['p99_chars']:,} | {summary['max_chars']:,} |"
        )
    lines.extend(
        [
            "",
            (
                f"Candidate-unit payload: {result['candidate_unit_chars']:,} chars "
                f"across {result['candidate_documents']} documents. This is not "
                "whole-document text."
            ),
            "",
        ]
    )
    for name, values in result["distributions"].items():
        lines.extend(
            [
                f"## {name}",
                "",
                "| Characters | Docs | Percent |",
                "| --- | ---: | ---: |",
                *[
                    f"| {row['range']} | {row['docs']} | {row['percent']:.2f}% |"
                    for row in values
                ],
                "",
            ]
        )
    return "\n".join(lines)


def run(report_path: Path, baseline_path: Path, incoming_path: Path) -> dict:
    report = read_json(report_path)
    baseline = manifest(baseline_path)
    incoming = manifest(incoming_path)
    selected = [
        row
        for row in report["results"]
        if row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    ]
    values = {
        "original_raw": [],
        "updated_raw": [],
        "original_cleaned_body": [],
        "updated_cleaned_body": [],
        "original_cleaned_non_heading": [],
        "updated_cleaned_non_heading": [],
    }
    for row in selected:
        identity = (row["knowledge_base"], row["document_id"])
        updated = character_counts(
            source_path(incoming_path, incoming[identity]),
            incoming[identity].get("body_scope"),
        )
        values["updated_raw"].append(updated["raw_chars"])
        values["updated_cleaned_body"].append(updated["cleaned_body_chars"])
        values["updated_cleaned_non_heading"].append(
            updated["cleaned_non_heading_chars"]
        )
        if identity in baseline:
            original = character_counts(
                source_path(baseline_path, baseline[identity]),
                baseline[identity].get("body_scope"),
            )
            values["original_raw"].append(original["raw_chars"])
            values["original_cleaned_body"].append(original["cleaned_body_chars"])
            values["original_cleaned_non_heading"].append(
                original["cleaned_non_heading_chars"]
            )
    return {
        "schema_version": "mdcompare-candidate-chars-1",
        "rules_version": report["rules"]["version"],
        "candidate_documents": len(selected),
        "paired_original_documents": len(values["original_cleaned_body"]),
        "missing_original_documents": len(selected)
        - len(values["original_cleaned_body"]),
        "candidate_unit_chars": sum(
            row["graphify_plan"]["candidate_body_chars"] for row in selected
        ),
        "metrics": {name: summarize(items) for name, items in values.items()},
        "distributions": {
            "original_cleaned_body": distribution(values["original_cleaned_body"]),
            "updated_cleaned_body": distribution(values["updated_cleaned_body"]),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.report, args.baseline, args.incoming)
    for path, content in (
        (args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n"),
        (args.markdown, markdown_report(result)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        os.chmod(path, 0o600)
    print(json.dumps(result["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
