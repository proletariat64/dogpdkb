"""Run the current CLI against retained frozen Markdown, without body output."""

import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(
    os.environ.get("MDCOMPARE_UAT_OUTPUT", str(ROOT / "tmp/mdcompare-knowledge-uat"))
)
OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
ENV = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
RUNS = [
    (
        "fixed98",
        ROOT / "tmp/mdcompare-uat-20261107/results",
        "baseline.json",
        "incoming.json",
    ),
    (
        "full-primary",
        ROOT / "tmp/mdcompare-uat-all-20261107-130000/results",
        "baseline-0.json",
        "incoming-0.json",
    ),
    (
        "full-alternate",
        ROOT / "tmp/mdcompare-uat-all-20261107-130000/results",
        "baseline-1.json",
        "incoming-1.json",
    ),
]


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"invalid or unreadable JSON: {path.name}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main():
    summary = {"scope": "body_only", "runs": {}}
    for name, source, old, new in RUNS:
        start = time.monotonic()
        destination = OUT / (name + ".json")
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "mdcompare",
                "knowledge",
                "--baseline",
                str(source / old),
                "--incoming",
                str(source / new),
                "--output",
                str(destination),
            ],
            env=ENV,
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode not in (0, 2) or not destination.exists():
            raise RuntimeError(name + ": CLI failed; inspect without dumping source")
        report = read_json(destination)
        require(report["scope"] == "body_only", name + ": wrong scope")
        expected = len(read_json(source / new)["documents"])
        require(report["summary"]["total"] == expected, name + ": wrong total")
        require(len(report["results"]) == expected, name + ": missing rows")
        require(
            sum(report["summary"]["statuses"].values()) == expected,
            name + ": status counts do not conserve",
        )
        require(
            not any(
                key in row
                for row in report["results"]
                for key in ("media_pending", "ocr", "resources")
            ),
            name + ": excluded channel leaked into result",
        )
        require(
            all(
                "evidence" not in unit
                for row in report["results"]
                for unit in row["units"]
            ),
            name + ": body evidence was not requested",
        )
        unit_counts = report["summary"]["units"]
        require(
            sum(unit_counts.values())
            == sum(len(row["units"]) for row in report["results"]),
            name + ": unit counts do not conserve",
        )
        with_reuse = sum(row["counts"]["reuse"] > 0 for row in report["results"])
        partial_reuse = sum(
            row["counts"]["reuse"] > 0 and row["status"] != "REUSE"
            for row in report["results"]
        )
        cross = sum(
            unit["kind"] == "reuse" and unit["old"]["document_id"] != row["document_id"]
            for row in report["results"]
            for unit in row["units"]
        )
        content_counts = Counter(
            unit["kind"]
            for row in report["results"]
            for unit in row["units"]
            if unit["unit_type"] != "heading"
        )
        summary["runs"][name] = {
            **report["summary"],
            "errors": len(report["errors"]),
            "exit_code": run.returncode,
            "seconds": round(time.monotonic() - start, 3),
            "documents_with_reused_units": with_reuse,
            "documents_with_partial_reuse": partial_reuse,
            "cross_document_reused_units": cross,
            "non_heading_units": dict(content_counts),
        }
        summary_path = OUT / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        )
        summary_path.chmod(0o600)
        print(name, json.dumps(summary["runs"][name], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
