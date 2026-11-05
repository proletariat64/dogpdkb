"""Run Yuque discovery, export/OCR, corpus, Graphify and Web as one resumable job."""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from apply_sync_updates import corpus_max_date

from mdocr.config import (
    SyncConfig,
    cli_path,
    config_arguments,
    configure,
    configured_book,
)


@dataclass
class PipelineOptions:
    input_dir: Path
    source_url: str
    staging: Path
    cutoff: str | None
    dry_run: bool
    resume: bool
    graph_dir: Path
    web_dir: Path
    graphify_python: Path
    skip_graph: bool
    skip_web: bool
    settings: SyncConfig | None = None
    book_name: str = ""


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def run_command(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def _config(options: PipelineOptions) -> dict:
    return {
        "input_dir": str(options.input_dir.resolve()),
        "source_url": options.source_url,
        "cutoff": options.cutoff,
        "graph_dir": str(options.graph_dir.resolve()),
        "web_dir": str(options.web_dir.resolve()),
        "graphify_python": str(options.graphify_python.resolve()),
        "settings": options.settings.snapshot() if options.settings else None,
        "book_name": options.book_name,
    }


def run_pipeline(
    options: PipelineOptions, runner: Callable[[list[str]], None] = run_command
) -> dict:
    input_dir = options.input_dir.resolve()
    staging = options.staging.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"input dir not found: {input_dir}")
    staging.mkdir(parents=True, exist_ok=True)
    state_path = staging / "pipeline-state.json"
    config = _config(options)
    if state_path.exists():
        if not options.resume:
            raise ValueError("staging already exists; use --resume or a new directory")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("config") != config:
            raise ValueError(
                "staging configuration changed; use a new staging directory"
            )
    else:
        state = {
            "version": 1,
            "config": config,
            "cutoff": options.cutoff,
            "stages": {},
            "outputs": {},
            "status": "running",
        }
        write_json(state_path, state)

    def with_config(command: list[str]) -> list[str]:
        if options.settings:
            command.extend(["--config", str(options.settings.file)])
        return command

    def execute(name: str, command: list[str], output: Path) -> None:
        if state["stages"].get(name) == "completed":
            if not output.is_file():
                raise RuntimeError(f"cannot resume {name}: missing {output}")
            print(f"[{name}] reuse {output}", flush=True)
            return
        state["current_stage"] = name
        state["status"] = "running"
        write_json(state_path, state)
        try:
            runner(with_config(command))
            if not output.is_file():
                raise RuntimeError(f"{name} did not produce {output}")
        except BaseException as error:
            state["status"] = "failed"
            state["error"] = str(error)
            write_json(state_path, state)
            raise
        state["stages"][name] = "completed"
        state["outputs"][name] = str(output)
        state["error"] = None
        write_json(state_path, state)

    plan_dir = staging / "plan"
    plan_output = Path(state["outputs"].get("plan", plan_dir / ".pending"))
    if state["stages"].get("plan") != "completed":
        plan_command = [
            sys.executable,
            str(SCRIPTS / "get_sync_updates.py"),
            "-i",
            str(input_dir),
            "-s",
            options.source_url,
            "-o",
            str(plan_dir),
            "--json",
            "--page-cache",
            str(staging / "plan-pages"),
        ]
        state["current_stage"] = "plan"
        write_json(state_path, state)
        try:
            runner(with_config(plan_command))
            plans = sorted(plan_dir.glob("sync-updates-*.json"))
            if not plans:
                raise RuntimeError("plan did not produce a sync JSON")
            plan_output = plans[-1]
        except BaseException as error:
            state["status"] = "failed"
            state["error"] = str(error)
            write_json(state_path, state)
            raise
        state["stages"]["plan"] = "completed"
        state["outputs"]["plan"] = str(plan_output)
        write_json(state_path, state)
    else:
        if not plan_output.is_file():
            raise RuntimeError(f"cannot resume plan: missing {plan_output}")
        print(f"[plan] reuse {plan_output}", flush=True)

    sync = json.loads(plan_output.read_text(encoding="utf-8"))
    counts = {
        kind: len(sync.get(kind) or [])
        for kind in ("new", "updated", "renamed", "moved", "deleted")
    }
    if options.dry_run:
        state["status"] = "dry_run"
        write_json(state_path, state)
        result = {
            "status": "dry_run",
            "staging": str(staging),
            "sync_json": str(plan_output),
            "changes": counts,
            "stages": state["stages"],
        }
        write_json(staging / "result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return result

    if not state.get("cutoff"):
        state["cutoff"] = corpus_max_date(input_dir).isoformat()
        write_json(state_path, state)
    manifest_path = staging / "manifest.json"
    apply_command = [
        sys.executable,
        str(SCRIPTS / "apply_sync_updates.py"),
        "-i",
        str(input_dir),
        "-s",
        options.source_url,
        "--sync-json",
        str(plan_output),
        "--staging",
        str(staging),
    ]
    apply_command.extend(["--cutoff", state["cutoff"]])
    execute("corpus", apply_command, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failed_exports = [
        row
        for row in manifest.get("skipped") or []
        if row.get("reason") != "empty document body"
    ]
    if failed_exports and state["stages"].get("corpus") == "completed":
        state["stages"]["corpus"] = "retry_required"
        state["status"] = "failed"
        state["error"] = f"{len(failed_exports)} documents failed to export"
        write_json(state_path, state)
        raise RuntimeError(state["error"])

    graph_result_path = staging / "graph-update" / "result.json"
    if options.skip_graph:
        state["stages"]["graph"] = "skipped"
        write_json(state_path, state)
    else:
        execute(
            "graph",
            [
                str(options.graphify_python),
                str(SCRIPTS / "update_graph.py"),
                "-i",
                str(input_dir),
                "--manifest",
                str(manifest_path),
                "--staging",
                str(staging / "graph-update"),
                "--graph-dir",
                str(options.graph_dir),
            ],
            graph_result_path,
        )

    catalog_path = options.web_dir / "catalog.json"
    if options.skip_web:
        state["stages"]["web"] = "skipped"
        write_json(state_path, state)
    else:
        book_name = (
            (sync.get("book") or {}).get("name")
            or options.book_name
            or options.source_url
        )
        execute(
            "web",
            [
                sys.executable,
                str(SCRIPTS / "publish_web.py"),
                "-i",
                str(input_dir),
                "--graph",
                str(options.graph_dir / "graph.json"),
                "-o",
                str(options.web_dir),
                "--book-url",
                options.source_url,
                "--book-name",
                book_name,
            ],
            catalog_path,
        )

    state["status"] = "completed"
    state["current_stage"] = None
    write_json(state_path, state)
    result = {
        "status": "completed",
        "staging": str(staging),
        "sync_json": str(plan_output),
        "changes": counts,
        "stages": state["stages"],
        "graph_result": str(graph_result_path) if graph_result_path.exists() else None,
        "catalog": str(catalog_path) if catalog_path.exists() else None,
    }
    write_json(staging / "result.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    config_arguments(parser, book=True)
    parser.add_argument("-i", "--input", type=cli_path)
    parser.add_argument("-s", "--skylark-url")
    parser.add_argument("--staging", type=cli_path)
    parser.add_argument("--cutoff")
    parser.add_argument(
        "--dry-run", action="store_true", help="stop after the sync plan"
    )
    parser.add_argument(
        "--resume", action="store_true", help="reuse completed steps in --staging"
    )
    parser.add_argument("--graph-dir", type=cli_path)
    parser.add_argument("--graphify-python", type=cli_path)
    parser.add_argument("--web-dir", type=cli_path)
    parser.add_argument("--skip-graph", action="store_true")
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args()
    config = configure(parser, args.config)
    source = configured_book(parser, config, args, args.skylark_url)
    input_dir = args.input or source.corpus
    graphify_python = (
        (args.graphify_python or config.graphify_python).expanduser().resolve()
    )
    if not args.skip_graph and not graphify_python.is_file():
        parser.error(f"Graphify Python not found: {graphify_python}")
    options = PipelineOptions(
        input_dir=input_dir,
        source_url=source.url,
        staging=args.staging
        or config.staging
        / source.key
        / f"sync-{datetime.now().astimezone():%Y%m%d-%H%M%S}",
        cutoff=args.cutoff,
        dry_run=args.dry_run,
        resume=args.resume,
        graph_dir=args.graph_dir or config.graph_dir or input_dir / "graphify-out",
        web_dir=args.web_dir or config.web / source.key,
        graphify_python=graphify_python.resolve(),
        skip_graph=args.skip_graph,
        skip_web=args.skip_web,
        settings=config,
        book_name=source.name,
    )
    if args.resume:
        print(f"Resuming completed stages in {options.staging}", flush=True)
    run_pipeline(options)


if __name__ == "__main__":
    main()
