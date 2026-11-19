"""Read-only, offline checks of configured public CLI entry points."""

from __future__ import annotations

import json
from pathlib import Path

from dogpdkb.processes import run_command
from dogpdkb.settings import (
    DirectorySource,
    YuqueSource,
    load_config,
    select_book,
    select_inputs,
    validate_directory_source,
)


def diagnose(
    config: Path | None,
    book: str | None,
    package_only=False,
    *,
    keys: list[str] | None = None,
    directory: Path | None = None,
) -> list[dict]:
    report: list[dict] = [{"name": "package", "status": "ok"}]
    if package_only:
        return report
    try:
        settings = load_config(config)
        selected = select_book(settings, book)
        sources = select_inputs(settings, selected, keys, directory)
    except (OSError, ValueError) as error:
        return [
            *report,
            {"name": "configuration", "status": "fail", "reason": str(error)},
        ]
    report.append(
        {
            "name": "configuration",
            "status": "ok",
            "book": selected.key,
            "sources": [source.key for source in sources],
        }
    )
    for source in sources:
        if directory is None and isinstance(source, DirectorySource):
            try:
                validate_directory_source(settings, selected, source.path)
            except (OSError, ValueError) as error:
                report.append(
                    {"name": source.key, "status": "fail", "reason": str(error)}
                )
    for path in dict.fromkeys(
        (
            selected.corpus,
            settings.graph_for(selected),
            settings.staging,
            settings.state,
            settings.ocr_cache,
        )
    ):
        blocked = next(
            (p for p in (path, *path.parents) if p.exists() and not p.is_dir()), None
        )
        if blocked is not None:
            report.append(
                {
                    "name": "paths",
                    "status": "fail",
                    "reason": f"not a directory: {blocked}",
                }
            )
    if any(row["status"] == "fail" for row in report):
        return report
    tools = ["graphify", "mdcompare", "mdocr"]
    if any(isinstance(source, YuqueSource) for source in sources):
        tools.append("skylark")
    for name in tools:
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
