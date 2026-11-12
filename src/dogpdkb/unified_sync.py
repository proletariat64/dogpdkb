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

from dogpdkb.sync_results import archive_run
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

#: Flags that belonged to the superseded executors. Ticket 08 converged the
#: sync entry onto the unified runner: these no longer select a second default
#: executor. They are recognised only to emit a deprecation notice and forward
#: the compatible subset (:func:`unified_arguments`) into the unified runner.
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
    notice = (
        f"dogpdkb sync: deprecated legacy flags ({listed}); the legacy sync "
        "executor is removed and this invocation is forwarded to the unified "
        "runner. The unified runner accepts --config, --book, --stop-after and "
        "--resume <run>; legacy-only flags are ignored."
    )
    if "--dry-run" in flags:
        notice += " --dry-run maps to --stop-after plan (no publication)."
    return notice


#: The unified runner's own value-taking flags. A legacy invocation keeps only
#: these (plus their values); every superseded executor flag is dropped.
_UNIFIED_VALUE_FLAGS = frozenset({"--config", "--book", "--stop-after", "--resume"})


def unified_arguments(arguments: list[str]) -> list[str]:
    """Compatible subset of a legacy invocation, forwarded to the unified runner.

    ``--config``, ``--book``, ``--stop-after`` and a valued ``--resume <run>``
    are kept; superseded executor flags (and a valueless boolean ``--resume``)
    are dropped. ``-h``/``--help`` is preserved so help is still printed, and the
    legacy ``--dry-run`` maps to ``--stop-after plan`` to keep its no-write intent.
    The unified runner is the single default executor, so a legacy invocation
    runs the same five-stage batch rather than a second pipeline.

    ``--dry-run`` caps the batch at plan regardless of argument order: argparse
    keeps the LAST ``--stop-after``, so an explicit later stage would otherwise
    win and publish despite the no-write flag. When ``--dry-run`` is present every
    explicit ``--stop-after`` is dropped and ``plan`` is appended exactly once.
    """
    dry_run = any(token.partition("=")[0] == "--dry-run" for token in arguments)
    kept: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        name, separator, _value = token.partition("=")
        if name in {"-h", "--help"}:
            # Help must survive forwarding so the unified parser prints it instead
            # of stripping it and running a real batch (H5).
            kept.append(token)
            index += 1
            continue
        if name == "--dry-run":
            # The legacy dry run meant "stop after the plan"; preserve that
            # no-write intent rather than silently publishing (H11). The cap is
            # appended once after the pass so argument order cannot override it.
            index += 1
            continue
        if name not in _UNIFIED_VALUE_FLAGS:
            index += 1
            continue
        if dry_run and name == "--stop-after":
            # Drop the explicit stage (both --stop-after=X and --stop-after X)
            # so the dry-run plan cap below is the only --stop-after forwarded.
            if not separator and index + 1 < len(arguments) and not arguments[
                index + 1
            ].startswith("-"):
                index += 2
            else:
                index += 1
            continue
        if separator:
            kept.append(token)
            index += 1
        elif index + 1 < len(arguments) and not arguments[index + 1].startswith("-"):
            kept.extend([token, arguments[index + 1]])
            index += 2
        else:
            # A valueless flag (e.g. the legacy boolean --resume) is dropped.
            index += 1
    if dry_run:
        kept.extend(["--stop-after", "plan"])
    return kept


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


def _validate_resume(
    layout: RunLayout, state: RunState, config: Any, book_key: str
) -> None:
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
    _validate_capture_raws(layout, config, book_key)
    _validate_frozen_inputs(layout, state)


def _validate_capture_raws(layout: RunLayout, config: Any, book_key: str) -> None:
    """Byte-check each captured raw against durable capture-time evidence.

    A capture-only pause has no frozen mdcompare inputs yet, so the freeze-time
    comparison cannot run. The durable copy kept at capture time (outside wash) is
    the source of truth: a raw MODIFIED while paused fails closed regardless of
    mdcompare's status. A raw that is missing, unreadable or OCR-enriched is left
    to the availability-first path (capture reuse / mdcompare archive
    CAPTURE_RAW_UNAVAILABLE), never a hard reject (spec:144). Direct bytes, no
    fingerprint (spec:82).
    """
    from dogpdkb.knowledge_capture import _raw_validity
    from dogpdkb.knowledge_plan import _resolve_raw
    from dogpdkb.stages.capture import capture_evidence_dir

    snapshot_path = layout.stage_dir("capture") / "snapshot.json"
    if not snapshot_path.is_file():
        return
    snapshot = read_json(snapshot_path)
    if not isinstance(snapshot, dict):
        return
    documents = snapshot.get("documents") or {}
    exports = snapshot.get("exports") or {}
    evidence_dir = capture_evidence_dir(config, book_key, layout.run_id)
    for doc_id, export in exports.items():
        if not isinstance(export, dict) or export.get("status") != "captured":
            continue
        capture_ref = (documents.get(doc_id) or {}).get("raw") or export.get("raw")
        capture_raw = _resolve_raw(capture_ref, snapshot_path)
        if capture_raw is None or not capture_raw.is_file():
            continue  # deleted -> availability path
        if _raw_validity(capture_raw) is not None:
            continue  # enriched/unreadable -> availability path ([0]/mdcompare)
        evidence = evidence_dir / capture_raw.name
        if not evidence.is_file():
            continue  # no durable capture-time evidence to compare against
        if capture_raw.read_bytes() != evidence.read_bytes():
            raise RunError(
                f"cannot resume {layout.run_id}: captured raw {capture_raw.name} "
                "no longer matches the capture-time evidence"
            )


def _validate_frozen_inputs(layout: RunLayout, state: RunState) -> None:
    """Byte-check frozen planning inputs against the capture evidence.

    mdcompare freezes each selected document's raw export verbatim and compile
    later publishes those bytes under the original source_version. Artifact
    existence alone cannot catch a frozen input that was altered after freezing,
    so a resumed batch re-runs the freeze-time byte comparison against the
    captured raw and fails closed on any mismatch.
    """
    from dogpdkb.knowledge_plan import _resolve_raw

    if state.stage("mdcompare")["status"] not in DONE_STAGE_STATUSES:
        return
    capture = read_envelope(layout, "capture") or {}
    mdcompare = read_envelope(layout, "mdcompare") or {}
    snapshot_ref = (capture.get("artifacts") or {}).get("snapshot")
    report_ref = (mdcompare.get("artifacts") or {}).get("report")
    if not snapshot_ref or not report_ref:
        return
    snapshot_path = layout.resolve(snapshot_ref)
    report_path = layout.resolve(report_ref)
    snapshot = read_json(snapshot_path)
    report = read_json(report_path)
    documents = snapshot.get("documents") or {}
    exports = snapshot.get("exports") or {}
    for row in report.get("documents") or []:
        frozen = _resolve_raw(row.get("raw"), report_path)
        if frozen is None:
            continue
        doc_id = str(row["document_id"])
        capture_ref = (documents.get(doc_id) or {}).get("raw") or (
            exports.get(doc_id) or {}
        ).get("raw")
        capture_raw = _resolve_raw(capture_ref, snapshot_path)
        if capture_raw is None or not capture_raw.is_file():
            raise RunError(
                f"cannot resume {layout.run_id}: frozen input {frozen.name} has "
                "no captured raw evidence to verify against"
            )
        if not frozen.is_file() or frozen.read_bytes() != capture_raw.read_bytes():
            raise RunError(
                f"cannot resume {layout.run_id}: frozen input {frozen.name} no "
                "longer matches the captured raw bytes"
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


def _archive_result(state: RunState, config: SyncConfig, book_key: str) -> None:
    """Persist the durable per-run result; a failure is recorded, never fatal."""
    try:
        archive_run(state, config, book_key)
    except (OSError, ValueError) as error:
        report_progress(f"result archiving failed: {safe_diagnostic(error)}")


def _clean_wash(
    state: RunState, config: SyncConfig, book_key: str, corpus: Path
) -> None:
    """Clean earlier finished work directories after the result is durable.

    Runs only for a completed/partial batch, after archiving. A cleanup failure
    is recorded in the durable cleanup record and retried at the next finished
    batch; it never breaks a valid corpus or changes the batch exit code.
    """
    from dogpdkb.sync_wash import cleanup_wash

    try:
        record = cleanup_wash(config, book_key, corpus=corpus, trigger=state.layout)
    except (OSError, ValueError) as error:
        report_progress(f"wash cleanup failed: {safe_diagnostic(error)}")
        return
    counts = record["counts"]
    report_progress(
        "wash cleanup: "
        f"removed={counts['removed']} kept={counts['kept']} "
        f"failed={counts['failed']} migrated={counts['migrated']}"
    )
    for row in record["failures"]:
        report_progress(f"wash cleanup deferred: {safe_diagnostic(row)}")


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
            layout = find_run(config.staging, args.resume, book.key)
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
            _validate_resume(layout, state, config, book.key)
            recorded, _ = observed_status(state.data)
            if recorded in FINISHED_STATUSES and recorded != "failed":
                report_progress(
                    f"run {layout.run_id} already {recorded}; nothing to do"
                )
                Progress(
                    corpus, book.key, url=book.url, name=book.name
                ).reconcile_finished_claim()
                print(json.dumps(state.summary(), ensure_ascii=False))
                return 0
            report_progress(f"resuming run {layout.run_id} ({recorded})")
            state.begin_resume()
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
            # Clear a claim orphaned by a crash after a prior run finished, so it
            # cannot block this batch; a live claim is left untouched.
            progress.reconcile_finished_claim()
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
            _archive_result(state, config, book.key)
            report_progress(f"run {layout.run_id} interrupted; resume it to continue")
            print(json.dumps(state.summary(), ensure_ascii=False))
        return 130
    except (RunError, StateError, OSError, ValueError) as error:
        parser.exit(1, f"dogpdkb sync: {safe_diagnostic(error)}\n")
    state.finish(status, stop_reason=reason, exit_code=code)
    if status in FINISHED_STATUSES:
        # Release the claim as soon as the result is terminal, before any archive
        # I/O: a crash while archiving must not orphan the claim and block cron.
        progress.finish()
    _archive_result(state, config, book.key)
    if status in ("completed", "partial"):
        # Only a successfully ended batch cleans earlier finished workspaces,
        # and only after its own result and necessary inputs are durable.
        _clean_wash(state, config, book.key, corpus)
    report_progress(f"run {layout.run_id} {status}" + (f": {reason}" if reason else ""))
    print(json.dumps(state.summary(), ensure_ascii=False))
    return code
