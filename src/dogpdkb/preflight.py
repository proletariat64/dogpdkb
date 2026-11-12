"""Read-only, offline checks of configured public CLI entry points."""

from __future__ import annotations

import json
from pathlib import Path

from dogpdkb.processes import run_command
from dogpdkb.settings import load_config, select_book


def diagnose(
    config: Path | None, book: str | None, package_only=False, require_ocr=False
) -> list[dict]:
    report = [{"name": "package", "status": "ok"}]
    if package_only:
        return report
    try:
        settings = load_config(config)
        selected = select_book(settings, book)
    except (OSError, ValueError) as error:
        return [
            *report,
            {"name": "configuration", "status": "fail", "reason": str(error)},
        ]
    report.append({"name": "configuration", "status": "ok", "book": selected.key})
    for name in ("graphify", "mdcompare", "mdocr", "skylark"):
        code = run_command(
            [*settings.command(name), "--help"], timeout=settings.timeout_seconds
        )
        report.append(
            {"name": name, "status": "ok" if code == 0 else "fail", "exit_code": code}
        )
        if code in {130, 143}:
            break
    return report


def print_report(report: list[dict], *, json_output=False) -> None:
    if json_output:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for row in report:
            print(
                f"{row['name']}: {row['status']}"
                + (f" ({row['reason']})" if "reason" in row else "")
            )
