"""One serial command chain shared with discover, apply and update-graph."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from dogpdkb.apply import apply_run, corpus_fact, parser_for, record_interruption
from dogpdkb.discover import DiscoverError
from dogpdkb.ocr_batch import file_digest
from dogpdkb.phases import (
    SYNC_PHASES,
    check_graph_manifest,
    graph_generation,
    read_checkpoint,
    seal_phase,
)
from dogpdkb.records import Run, Store
from dogpdkb.semantic import freeze_recovery
from dogpdkb.settings import (
    DEFAULT_DOWNLOAD_CONCURRENCY,
    load_config,
    select_book,
    select_inputs,
)


def freeze_policy(settings, store, run):
    """Persist the selected runtime choices, not provider credentials."""
    policy = run.data.get("sync_policy")
    if policy is None:
        source = freeze_recovery(
            settings.body_recovery, run.directory / "source-recovery"
        )
        policy = {
            "settings": {
                key: getattr(settings, key)
                for key in (
                    "semantic_score_request_budget",
                    "semantic_score_model",
                    "ocr_mode",
                    "ocr_request_budget",
                    "ocr_budget_seconds",
                    "ocr_concurrency",
                    "ocr_fix",
                    "timeout_seconds",
                    "mcp_url",
                    "proxy_bin",
                )
            },
            "tools": {
                name: settings.command(name)
                for name in ("mdcompare", "mdocr", "graphify", "skylark")
            },
            "ocr_cache": str(settings.ocr_cache),
            "body_recovery": str(source) if source else None,
        }
        policy["graphify_model"] = settings.graphify_model
        policy["graphify_token_budget"] = settings.graphify_token_budget
        policy["graphify_concurrency"] = settings.graphify_concurrency
        run.data["sync_policy"] = policy
        run.save(durable=True)
    frozen = replace(
        settings,
        **({"ocr_concurrency": 3, "ocr_fix": 0} | policy["settings"]),
        # Policies saved before this setting existed used the native default.
        graphify_model=policy.get("graphify_model"),
        graphify_token_budget=policy.get("graphify_token_budget"),
        # Legacy policies lacked concurrency; preserve historical CLI default.
        graphify_concurrency=policy.get("graphify_concurrency", 3),
        tools=policy["tools"],
        ocr_cache=Path(policy["ocr_cache"]),
        body_recovery=Path(policy["body_recovery"])
        if policy["body_recovery"]
        else None,
    )
    store.settings = frozen
    return frozen


def graph_handoff_current(book, store, run):
    manifest = read_checkpoint(run, "corpus", "manifest")
    pending = False
    for row in manifest["documents"]:
        identifier = row["document_id"]
        path = Path(row["path"])
        if (
            store.document(identifier).get("corpus", {}) != corpus_fact(row)
            or path.is_symlink()
            or not path.is_file()
            or file_digest(path) != row["input_copy_sha256"]
        ):
            return False
        if row["action"] == "compile":
            previous = store.step(identifier, "graph") or {}
            witness = Path(previous.get("input_copy", ""))
            if not (
                previous.get("status") == "completed"
                and previous.get("run_id") == run.id
                and previous.get("version") == row["version"]
                and previous.get("metadata") == row["metadata"]
                and witness.is_file()
                and file_digest(witness) == row["input_copy_sha256"]
            ):
                pending = True
    return not pending or run.data["graph_generation"] == graph_generation(store, book)


def _settle_graph(settings, book, store, run, manifest, retry_graph, *, replan=False):
    from dogpdkb.graph import run_graph

    if not retry_graph and "graph_exit_code" in run.data:
        return run.data["graph_exit_code"]
    if (
        not retry_graph
        and "graph_generation" in run.data
        and not graph_handoff_current(book, store, run)
    ):
        run.data["graph_publication"] = "retained"
        run.failure(step="graph", reason="STALE_GRAPH_HANDOFF", send_state="not_sent")
        return 2
    run.data["graph_publication"] = "pending"
    run.data.pop("graph_result", None)
    run.stage("graphify", phase="prepare")
    graph_code = run_graph(
        settings,
        book,
        store,
        run,
        manifest=manifest,
        retry_graph=retry_graph,
        replan=replan,
    )
    if run.data["graph_publication"] == "pending":
        run.data["graph_publication"] = "retained"
    if run.data.get("graph_result") == "unknown" and graph_code == 1:
        # An unproven tool result leaves unresolved work, not a fatal instance error.
        return 2
    return graph_code


def sync_run(
    settings,
    book,
    store,
    run,
    *,
    stop_after=None,
    resume=False,
    retry_graph=False,
    replan=False,
    concurrency=DEFAULT_DOWNLOAD_CONCURRENCY,
    sources=None,
):
    from dogpdkb.discover import discover

    retained = (
        {path for path in run.directory.rglob("*") if path.is_file()}
        if retry_graph and (run.data.get("cleanup") or replan)
        else set()
    )
    if retry_graph:
        manifest = run.data["artifacts"].get("manifest")
        if not manifest:
            raise ValueError("graph recovery requires a saved final compile manifest")
        check_graph_manifest(run, manifest)
        if replan:
            from dogpdkb.apply import replan_published

            manifest = str(replan_published(settings, book, store, run))
            retained.update(path for path in run.directory.rglob("*") if path.is_file())
        result = {
            "exit_code": 2
            if any(f.get("step") != "graph" for f in run.data["failures"])
            else 0,
            "paused": False,
            "manifest": manifest,
        }
    else:
        completed = run.data.get("completed_phase")
        if (
            stop_after
            and completed
            and SYNC_PHASES.index(stop_after) < SYNC_PHASES.index(completed)
        ):
            raise ValueError("cannot stop before the last completed phase")
        settings = freeze_policy(settings, store, run)
        differences = (
            None if resume else discover(settings, book, store, run, sources=sources)
        )
        result = apply_run(
            settings,
            book,
            store,
            run,
            differences,
            stop_after=stop_after,
            resume=resume,
            concurrency=concurrency,
        )
    if result["paused"]:
        return result
    graph_code = _settle_graph(
        settings,
        book,
        store,
        run,
        Path(result["manifest"]),
        retry_graph,
        replan=replan,
    )
    if graph_code in {130, 143}:
        record_interruption(store, run, graph_code)
        return {**result, "exit_code": graph_code}
    code = 1 if graph_code == 1 else 2 if 2 in {graph_code, result["exit_code"]} else 0
    if code != 1:
        run.data["graph_exit_code"] = graph_code
        seal_phase(run, "graph")
    run.finish("failed" if code == 1 else "partial" if code == 2 else "completed", code)
    from dogpdkb.cleanup import clean_run

    clean_run(store, run, preserve=retained)
    return {**result, "exit_code": code}


def _select_run(store, args, sources):
    if not args.resume:
        return Run.create(store, "sync", sources=sources)
    run = Run.load(store, args.resume)
    if not args.retry_graph:
        if run.data.get("command") != "sync":
            raise ValueError("ordinary resume requires a sync run")
        if run.data.get("cleanup"):
            raise ValueError("run has been cleaned; start a new run")
    else:
        manifest = run.data["artifacts"].get("manifest")
        if not manifest:
            raise ValueError("graph recovery requires a saved final compile manifest")
        check_graph_manifest(run, manifest)
    return run


def _execute_sync(settings, book, store, run, args, sources):
    run.data.update(
        status="running",
        pid=os.getpid(),
        command="sync",
        ended_at=None,
        exit_code=None,
        reason=None,
    )
    try:
        result = sync_run(
            settings,
            book,
            store,
            run,
            stop_after=args.stop_after,
            resume=bool(args.resume),
            retry_graph=args.retry_graph,
            replan=args.replan,
            concurrency=args.concurrency,
            sources=sources,
        )
    except InterruptedError as error:
        record_interruption(store, run, error.args[0])
        return error.args[0]
    except DiscoverError as error:
        code = error.exit_code
        run.finish("interrupted" if code in {130, 143} else "failed", code, str(error))
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        run.finish("failed", 1, str(error))
        raise
    print(
        json.dumps(
            {**result, "run": str(run.path), "status": run.data["status"]},
            ensure_ascii=False,
        )
    )
    return result["exit_code"]


def main(argv=None):
    parser = parser_for("sync")
    try:
        args = parser.parse_args(argv)
        if args.resume and (args.source is not None or args.dir is not None):
            parser.error("--source and --dir cannot be used with --resume")
        if args.replan and (not args.resume or not args.retry_graph or args.stop_after):
            parser.error(
                "--replan requires --resume and --retry-graph without --stop-after"
            )
        if args.retry_graph and (not args.resume or args.stop_after):
            parser.error("--retry-graph requires --resume and cannot use --stop-after")
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    try:
        settings = load_config(args.config)
        book = select_book(settings, args.book)
        sources = (
            None
            if args.resume
            else select_inputs(settings, book, args.source, args.dir)
        )
        store = Store(settings, book)
        with store.lock():
            run = _select_run(store, args, sources)
            return _execute_sync(settings, book, store, run, args, sources)
    except DiscoverError as error:
        print(f"dogpdkb sync: {error}", file=sys.stderr)
        return error.exit_code
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"dogpdkb sync: {error}", file=sys.stderr)
        return 1
