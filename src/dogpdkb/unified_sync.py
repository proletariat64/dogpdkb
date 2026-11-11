"""The unified ``dogpdkb sync`` batch runner.

One entry point executes the five batch stages in a fixed order. Each stage ends
before the next begins; a stage with no work is skipped and a stage with partial
item failures still hands its usable set to the next stage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dogpdkb.sync_run import (
    DONE_STAGE_STATUSES,
    FINISHED_STATUSES,
    STAGES,
    UNFINISHED_STATUSES,
    RunError,
    RunLayout,
    RunState,
    book_runs,
    find_run,
    new_run_layout,
    observed_status,
    read_json,
)
from dogpdkb.sync_stages import (
    StageContext,
    StageError,
    StageResult,
    handler_for,
    read_envelope,
    report_progress,
    stage_log,
    write_envelope,
)
from dogpdkb.sync_state import StateError
from dogpdkb.workflows.get_sync_updates import safe_diagnostic
from mdocr.config import Book, SyncConfig, config_arguments, configure, configured_book

#: Flags that still belong to the superseded executors. Ticket 08 removes them.
LEGACY_FLAGS = frozenset(
    {
        "--knowledge",
        "--snapshot",
        "--baseline",
        "--state",
        "--execute",
        "--approvals",
        "--quality-cases",
        "-i",
        "--input",
        "-s",
        "--skylark-url",
        "--staging",
        "--cutoff",
        "--dry-run",
        "--graph-dir",
        "--graphify-python",
        "--web-dir",
        "--skip-graph",
        "--skip-web",
        "--rebase-graph",
    }
)


def legacy_flags(arguments: list[str]) -> list[str]:
    """Legacy flags found in an invocation; empty means the unified runner.

    ``--resume`` is ambiguous: the legacy executor takes it as a boolean, the
    unified runner takes a run id. A value selects the unified runner.
    """
    found = []
    for index, token in enumerate(arguments):
        name, separator, _value = token.partition("=")
        if name in LEGACY_FLAGS:
            found.append(name)
        elif name == "--resume":
            following = arguments[index + 1] if index + 1 < len(arguments) else ""
            if not separator and (not following or following.startswith("-")):
                found.append(name)
    return found


def deprecation_notice(flags: list[str]) -> str:
    listed = ", ".join(sorted(set(flags)))
    return (
        f"dogpdkb sync: deprecated legacy flags ({listed}); forwarding to the "
        "legacy executor. The unified runner accepts --config, --book, "
        "--stop-after and --resume <run>."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dogpdkb sync",
        description="Run capture, mdcompare, plan, ocr and compile as one batch.",
    )
    config_arguments(parser, book=True)
    parser.add_argument(
        "--stop-after",
        choices=STAGES,
        metavar="STAGE",
        help="pause after this stage ends; resume the same run later",
    )
    parser.add_argument(
        "--resume",
        metavar="RUN",
        help="resume a recorded run id or run directory with its original inputs",
    )
    return parser


def config_identity(
    config: SyncConfig, book: Book, corpus: Path, graph_dir: Path
) -> dict[str, Any]:
    """Non-secret settings a resumed batch must still match."""
    return {
        "corpus": str(corpus),
        "staging": str(config.staging),
        "graph_dir": str(graph_dir),
        "web": str(config.web),
        "graphify_python": str(config.graphify_python),
        "book": {"key": book.key, "url": book.url, "name": book.name},
        "settings": config.snapshot(),
    }


def _unfinished(staging: Path, book_key: str) -> list[tuple[RunLayout, str]]:
    found = []
    for layout in book_runs(staging, book_key):
        try:
            payload = read_json(layout.state_path)
        except RunError as error:
            report_progress(f"ignoring unreadable run {layout.run_id}: {error}")
            continue
        status, _ = observed_status(payload)
        if status in UNFINISHED_STATUSES:
            found.append((layout, status))
    return found


def _validate_resume(layout: RunLayout, state: RunState) -> None:
    """A resumed batch keeps its own artifacts; missing evidence is fatal."""
    for stage in STAGES:
        row = state.stage(stage)
        if row["status"] not in DONE_STAGE_STATUSES:
            continue
        envelope = read_envelope(layout, stage)
        if envelope is None:
            raise RunError(
                f"cannot resume {layout.run_id}: {stage} is {row['status']} "
                "but its result envelope is missing"
            )
        if envelope["status"] != row["status"]:
            raise RunError(
                f"cannot resume {layout.run_id}: {stage} result disagrees with run state"
            )
        for name, reference in (envelope.get("artifacts") or {}).items():
            if not layout.resolve(reference).exists():
                raise RunError(
                    f"cannot resume {layout.run_id}: missing {stage} artifact "
                    f"{name} ({reference})"
                )


def _inputs(layout: RunLayout, stage: str) -> dict[str, Any]:
    earlier = STAGES[: STAGES.index(stage)]
    envelopes = {name: read_envelope(layout, name) for name in earlier}
    return {name: value for name, value in envelopes.items() if value is not None}


def _execute(
    state: RunState,
    stage: str,
    *,
    config: SyncConfig,
    book: Book,
    corpus: Path,
    graph_dir: Path,
    progress: Any,
) -> StageResult:
    layout = state.layout
    directory = layout.stage_dir(stage)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.start_stage(stage)
    started_at = state.stage(stage)["started_at"]
    report_progress(f"{stage}: started")
    with stage_log(layout.log_path(stage)) as log:
        try:
            context = StageContext(
                stage=stage,
                layout=layout,
                state=state,
                config=config,
                book=book,
                corpus=corpus,
                graph_dir=graph_dir,
                progress=progress,
                directory=directory,
                log=log,
                inputs=_inputs(layout, stage),
            )
            handler = handler_for(stage)
            with log.capture():
                result = handler(context)
        except (StageError, OSError, ValueError, RuntimeError) as error:
            reason = safe_diagnostic(error)
            log.write(f"{stage} failed: {reason}")
            result = StageResult(status="failed", reason=reason)
        write_envelope(layout, stage, result, started_at=started_at, log=log.path)
    state.end_stage(
        stage,
        {
            "status": result.status,
            "counts": result.counts,
            "reason": result.reason,
        },
    )
    counts = " ".join(f"{key}={value}" for key, value in sorted(result.counts.items()))
    report_progress(f"{stage}: {result.status} {counts}".rstrip())
    return result


def _run(
    state: RunState,
    *,
    config: SyncConfig,
    book: Book,
    corpus: Path,
    graph_dir: Path,
    progress: Any,
    stop_after: str | None,
) -> tuple[str, str | None, int]:
    layout = state.layout
    anomalies = 0
    for stage in STAGES:
        row = state.stage(stage)
        if row["status"] in DONE_STAGE_STATUSES:
            report_progress(
                f"{stage}: already {row['status']}; reusing recorded result"
            )
            envelope = read_envelope(layout, stage) or {}
            anomalies += len(envelope.get("anomalies") or [])
        else:
            result = _execute(
                state,
                stage,
                config=config,
                book=book,
                corpus=corpus,
                graph_dir=graph_dir,
                progress=progress,
            )
            anomalies += len(result.anomalies)
            if result.status == "failed":
                return "failed", result.reason or f"{stage} failed", 1
        if stop_after == stage:
            return "paused", f"stop-after {stage}", 0
    if anomalies:
        return "partial", f"{anomalies} archived anomalies", 0
    return "completed", None, 0


def main(argv: list[str] | None = None) -> int:
    from dogpdkb.sync_progress import Progress

    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(arguments)
    config = configure(parser, args.config)
    book = configured_book(parser, config, args)
    corpus = book.corpus
    if not corpus.is_dir():
        parser.exit(1, f"dogpdkb sync: corpus directory does not exist: {corpus}\n")
    graph_dir = (config.graph_dir or corpus / "graphify-out").expanduser().resolve()
    identity = config_identity(config, book, corpus, graph_dir)
    layout: RunLayout | None = None
    state: RunState | None = None
    progress: Any = None
    try:
        if args.resume:
            layout = find_run(config.staging, args.resume)
            state = RunState.load(layout)
            if state.data["book"]["key"] != book.key:
                raise RunError(
                    f"run {layout.run_id} belongs to book {state.data['book']['key']}, "
                    f"not {book.key}"
                )
            if state.data["config_identity"] != identity:
                raise RunError(
                    f"run {layout.run_id} was recorded with different configuration; "
                    "start a new run instead of resuming"
                )
            _validate_resume(layout, state)
            recorded, _ = observed_status(state.data)
            if recorded in FINISHED_STATUSES and recorded != "failed":
                report_progress(
                    f"run {layout.run_id} already {recorded}; nothing to do"
                )
                print(json.dumps(state.summary(), ensure_ascii=False))
                return 0
            report_progress(f"resuming run {layout.run_id} ({recorded})")
        else:
            pending = _unfinished(config.staging, book.key)
            if pending:
                layout, recorded = pending[-1]
                parser.exit(
                    1,
                    f"dogpdkb sync: unfinished run {layout.run_id} ({recorded}) for "
                    f"book {book.key}; resume it instead of starting a new batch:\n"
                    f"  dogpdkb sync --config {config.file} --book {book.key} "
                    f"--resume {layout.run_id}\n",
                )
            layout = new_run_layout(config.staging, book.key)
            state = RunState.create(
                layout,
                book={"key": book.key, "name": book.name, "url": book.url},
                corpus=corpus,
                config=config.file,
                identity=identity,
                stop_after=args.stop_after,
            )
        try:
            progress = Progress(corpus, book.key, url=book.url, name=book.name)
            # Bind the canonical claim to the frozen capture observation, exactly
            # as knowledge_capture does, so a resumed batch keeps its identity.
            snapshot_path = layout.stage_dir("capture") / "snapshot.json"
            progress.claim(layout.root, phase="capture", parent=snapshot_path)
        except (StateError, OSError, ValueError) as error:
            state.finish("failed", stop_reason=safe_diagnostic(error), exit_code=1)
            raise
        state.data["stop_after"] = args.stop_after
        state.save()
        report_progress(
            f"run {layout.run_id} started book={book.key} stages={','.join(STAGES)}"
        )
        status, reason, code = _run(
            state,
            config=config,
            book=book,
            corpus=corpus,
            graph_dir=graph_dir,
            progress=progress,
            stop_after=args.stop_after,
        )
    except KeyboardInterrupt:
        if state is not None:
            state.interrupt_stages()
            state.finish("paused", stop_reason="user interrupt", exit_code=130)
            report_progress(f"run {layout.run_id} interrupted; resume it to continue")
            print(json.dumps(state.summary(), ensure_ascii=False))
        return 130
    except (RunError, StateError, OSError, ValueError) as error:
        parser.exit(1, f"dogpdkb sync: {safe_diagnostic(error)}\n")
    state.finish(status, stop_reason=reason, exit_code=code)
    if status in FINISHED_STATUSES:
        # A finished batch never leaves a claim that blocks the next cron run.
        progress.finish()
    report_progress(f"run {layout.run_id} {status}" + (f": {reason}" if reason else ""))
    print(json.dumps(state.summary(), ensure_ascii=False))
    return code
