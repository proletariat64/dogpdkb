"""Run Yuque discovery, export/OCR, corpus, Graphify and Web as one resumable job."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from apply_sync_updates import corpus_max_date
from get_sync_updates import safe_diagnostic

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
    rebase_graph: bool = False
    book_key: str | None = None


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"invalid pipeline JSON file: {path}") from error


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
        "graphify_python": str(options.graphify_python.expanduser().absolute()),
        "settings": options.settings.snapshot() if options.settings else None,
        "book_name": options.book_name,
    }


def manifest_failures(manifest: dict) -> list[dict]:
    """Retain compatibility with manifests that predate explicit failure records."""
    return manifest.get("failures") or [
        row
        for row in manifest.get("skipped") or []
        if row.get("reason") != "empty document body"
    ]


def recovery_command(options: PipelineOptions) -> str:
    """Reproduce the same task selection without exposing environment credentials."""
    command = [sys.executable, str(SCRIPTS / "sync_knowledge_base.py"), "--resume"]
    for flag, path in (
        ("--staging", options.staging),
        ("--input", options.input_dir),
        ("--graph-dir", options.graph_dir),
        ("--web-dir", options.web_dir),
        ("--graphify-python", options.graphify_python),
    ):
        command.extend([flag, str(path.expanduser().absolute())])
    if options.settings:
        command.extend(["--config", str(options.settings.file)])
    if options.book_key:
        command.extend(["--book", options.book_key])
    else:
        command.extend(["--skylark-url", options.source_url])
    if options.cutoff is not None:
        command.extend(["--cutoff", options.cutoff])
    for flag, enabled in (
        ("--dry-run", options.dry_run),
        ("--skip-graph", options.skip_graph),
        ("--skip-web", options.skip_web),
        ("--rebase-graph", options.rebase_graph),
    ):
        if enabled:
            command.append(flag)
    return shlex.join(command)


def run_pipeline(
    options: PipelineOptions, runner: Callable[[list[str]], None] = run_command
) -> dict:
    input_dir = options.input_dir.resolve()
    staging = options.staging.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"input dir not found: {input_dir}")
    staging.mkdir(parents=True, exist_ok=True)
    state_path = staging / "pipeline-state.json"
    if options.rebase_graph and (
        not options.resume
        or options.skip_graph
        or options.dry_run
        or not state_path.is_file()
    ):
        raise ValueError(
            "--rebase-graph requires --resume of an existing run with graph enabled"
        )
    config = _config(options)
    if state_path.exists():
        if not options.resume:
            raise ValueError("staging already exists; use --resume or a new directory")
        state = read_json(state_path)
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

    if options.rebase_graph:
        if state["stages"].get("corpus") != "completed":
            raise ValueError("--rebase-graph requires a completed corpus stage")
        for stage in ("graph", "web"):
            state["stages"].pop(stage, None)
            state["outputs"].pop(stage, None)
        write_json(state_path, state)

    def record_failure(name: str, error: BaseException) -> None:
        if isinstance(error, subprocess.CalledProcessError):
            reason = f"subprocess exited {error.returncode}"
        else:
            reason = safe_diagnostic(error) or "cancelled"
        state["stages"][name] = "retry_required"
        state["status"] = "failed"
        state["error"] = reason
        write_json(state_path, state)
        print(
            f"[{name}] failed: {reason}; completed_stages="
            f"{sum(value == 'completed' for value in state['stages'].values())}; "
            f"staging kept at {staging}",
            file=sys.stderr,
        )
        if name == "plan":
            print(
                "Plan incomplete: completed=0 failed=1; resume will refetch the entire listing; "
                "saved offset pages are diagnostic only (no remote snapshot token).",
                file=sys.stderr,
            )
        elif name == "corpus":
            try:
                manifest = read_json(staging / "manifest.json")
            except (OSError, ValueError):
                manifest = {}
            failures = manifest_failures(manifest)
            completed_exports = len(manifest.get("exported") or [])
            try:
                progress = read_json(staging / "export-progress.json")
            except (OSError, ValueError):
                pass
            else:
                completed_exports = sum(
                    row.get("status") == "exported"
                    and (staging / "raw" / row.get("file", "")).is_file()
                    for row in (progress.get("documents") or {}).values()
                )
            print(
                f"Corpus: completed_exports={completed_exports} "
                f"failed={len(failures) or 1}; valid export/OCR checkpoints will be reused; "
                "a completed plan will be reused. Publication may be partial if it had started.",
                file=sys.stderr,
            )
            for row in failures[:10]:
                unit = row.get("id", row.get("file", "unknown"))
                print(
                    f"  {safe_diagnostic(row.get('stage', 'export'), 30)} "
                    f"unit={safe_diagnostic(unit, 100)}: "
                    f"{safe_diagnostic(row.get('reason', 'failed'))}",
                    file=sys.stderr,
                )
        print("Retry with the same configuration and environment:", file=sys.stderr)
        print(f"Retry: {recovery_command(options)}", file=sys.stderr)

    def with_config(command: list[str]) -> list[str]:
        if options.settings:
            command.extend(["--config", str(options.settings.file)])
        return command

    def execute(name: str, command: list[str], output: Path) -> None:
        reuse = state["stages"].get(name) == "completed"
        state["current_stage"] = name
        state["status"] = "running"
        write_json(state_path, state)
        try:
            if reuse:
                print(f"[{name}] reuse {output}", flush=True)
            else:
                runner(with_config(command))
            if not output.is_file():
                raise RuntimeError(f"{name} did not produce {output}")
            if name == "corpus":
                manifest = read_json(output)
                failures = manifest_failures(manifest)
                if failures or manifest.get("status") not in (None, "completed"):
                    raise RuntimeError(
                        f"corpus incomplete: {len(failures)} failed units; "
                        f"manifest status={manifest.get('status', 'legacy')}"
                    )
        except BaseException as error:
            record_failure(name, error)
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
            record_failure("plan", error)
            raise
        state["stages"]["plan"] = "completed"
        state["outputs"]["plan"] = str(plan_output)
        write_json(state_path, state)
    else:
        if not plan_output.is_file():
            raise RuntimeError(f"cannot resume plan: missing {plan_output}")
        print(f"[plan] reuse {plan_output}", flush=True)

    sync = read_json(plan_output)
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
                *(["--rebase"] if options.rebase_graph else []),
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
    parser.add_argument(
        "--rebase-graph",
        action="store_true",
        help="with --resume, rebuild the retained corpus delta against the current graph",
    )
    args = parser.parse_args()
    config = configure(parser, args.config)
    source = configured_book(parser, config, args, args.skylark_url)
    input_dir = args.input or source.corpus
    graphify_python = (
        (args.graphify_python or config.graphify_python).expanduser().absolute()
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
        graphify_python=graphify_python,
        skip_graph=args.skip_graph,
        skip_web=args.skip_web,
        settings=config,
        book_name=source.name,
        rebase_graph=args.rebase_graph,
        book_key=source.key if args.skylark_url is None else None,
    )
    if args.resume:
        print(f"Resuming completed stages in {options.staging}", flush=True)
    try:
        run_pipeline(options)
    except KeyboardInterrupt:
        parser.exit(130, "sync cancelled; retained checkpoints can be resumed\n")
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"sync failed: {safe_diagnostic(error)}\n")


if __name__ == "__main__":
    main()
