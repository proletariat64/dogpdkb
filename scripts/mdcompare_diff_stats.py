"""Count bounded textual edits for frozen body-only document pairs."""

import argparse
import difflib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path

from mdcompare_candidate_stats import manifest, read_json, source_path

from mdcompare.knowledge_units import _body_view, _prose_key

BINS = (
    (0, 0, "0"),
    (1, 99, "1–99"),
    (100, 499, "100–499"),
    (500, 999, "500–999"),
    (1000, 1999, "1,000–1,999"),
    (2000, 4999, "2,000–4,999"),
    (5000, 9999, "5,000–9,999"),
    (10000, 19999, "10,000–19,999"),
    (20000, 49999, "20,000–49,999"),
    (50000, 99999, "50,000–99,999"),
    (100000, None, "≥100,000"),
)
MAX_LINE_BLOCK_CHARS = 20_000


def body(path: Path, *, transform=None) -> str:
    # Statistics-only view: remove explicit generated blocks even when an
    # exporter placed them inside a Markdown code fence. Do not infer provenance
    # of unmarked text. This is deliberately not the product comparison view.
    text = path.read_text()
    text = re.sub(
        r"<!--\s*mdocr:ocr-begin\b.*?-->.*?(?:<!--\s*mdocr:ocr-end\s*-->|\Z)",
        lambda match: re.sub(r"[^\n]", " ", match.group()),
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Disable code-protection solely for this body-size estimate so image
    # references and marked generated text are excluded in all structures.
    text = "\n".join(
        "" if re.match(r"^\s*(?:`{3,}|~{3,})", line) else line.lstrip()
        for line in text.splitlines()
    ).replace("`", "")
    text, _ = _body_view(text)
    # An unclosed legacy marker has no reliable end; never guess a boundary or
    # report its contents as comparable body. Leave that pair unscorable.
    if re.search(r"这是一张图片[，,]\s*ocr\s*内容为", text, re.IGNORECASE):
        raise ValueError("UNTERMINATED_EXCLUDED_REGION")
    if transform is not None:
        text = transform(text)
    # Normalize whitelisted cosmetic HTML and quantities per line. Unknown
    # markup, link targets and nonempty table bindings remain visible.
    return "\n".join(
        normalized for line in text.splitlines() if (normalized := _prose_key(line))
    )


def text_diff(old: str, new: str) -> tuple[int, int, int]:
    before = old.splitlines(keepends=True)
    after = new.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    inserted = deleted = coarse = 0
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == "equal":
            continue
        left = "".join(before[a:b])
        right = "".join(after[c:d])
        if not left or not right:
            deleted += len(left)
            inserted += len(right)
        elif len(left) + len(right) > MAX_LINE_BLOCK_CHARS:
            # Conservative changed-region upper bound, not a minimal edit count.
            deleted += len(left)
            inserted += len(right)
            coarse += 1
        else:
            char_matcher = difflib.SequenceMatcher(None, left, right, autojunk=True)
            for opcode, x, y, u, v in char_matcher.get_opcodes():
                if opcode != "equal":
                    deleted += y - x
                    inserted += v - u
    return inserted, deleted, coarse


def quantile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = read_json(args.report)
    old = manifest(args.baseline)
    new = manifest(args.incoming)
    rows = []
    for row in report["results"]:
        if row["graphify_plan"]["action"] != "BODY_DELTA_CANDIDATE":
            continue
        identity = (row["knowledge_base"], row["document_id"])
        if identity not in old:
            rows.append({"document_id": row["document_id"], "paired": False})
            continue
        try:
            before = body(source_path(args.baseline, old[identity]))
            after = body(source_path(args.incoming, new[identity]))
        except ValueError as exc:
            if str(exc) != "UNTERMINATED_EXCLUDED_REGION":
                raise
            rows.append(
                {
                    "document_id": row["document_id"],
                    "paired": True,
                    "unscorable": str(exc),
                }
            )
            continue
        inserted, deleted, coarse = text_diff(before, after)
        rows.append(
            {
                "document_id": row["document_id"],
                "paired": True,
                "added_chars": inserted,
                "removed_chars": deleted,
                "diff_chars": inserted + deleted,
                "coarse_blocks": coarse,
            }
        )
    paired = [row for row in rows if row["paired"] and "unscorable" not in row]
    counts = Counter()
    for row in paired:
        value = row["diff_chars"]
        for lower, upper, label in BINS:
            if value >= lower and (upper is None or value <= upper):
                counts[label] += 1
                break
    sizes = [row["diff_chars"] for row in paired]
    result = {
        "scope": "v3 candidate docs; paired body-view textual diff",
        "method": "line SequenceMatcher autojunk=false; replacement blocks <=20000 chars use character SequenceMatcher autojunk=true; larger blocks conservatively count whole old and new regions",
        "paired_docs": sum(row["paired"] for row in rows),
        "scored_paired_docs": len(paired),
        "unscorable_paired_docs": sum("unscorable" in row for row in rows),
        "no_original_docs": sum(not row["paired"] for row in rows),
        "added_chars": sum(row["added_chars"] for row in paired),
        "removed_chars": sum(row["removed_chars"] for row in paired),
        "diff_chars": sum(sizes),
        "coarse_docs": sum(row["coarse_blocks"] > 0 for row in paired),
        "coarse_blocks": sum(row["coarse_blocks"] for row in paired),
        "p25": quantile(sizes, 0.25),
        "median": quantile(sizes, 0.5),
        "p75": quantile(sizes, 0.75),
        "p90": quantile(sizes, 0.9),
        "p95": quantile(sizes, 0.95),
        "p99": quantile(sizes, 0.99),
        "max": max(sizes, default=0),
        "distribution": [
            {"range": label, "docs": counts[label]} for _, _, label in BINS
        ],
        "documents": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    os.chmod(args.output, 0o600)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "documents"},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
