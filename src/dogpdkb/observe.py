"""Read-only status snapshots for the CLI orchestrator."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from dogpdkb.help import HelpParser
from dogpdkb.phases import SYNC_PHASES
from dogpdkb.records import (
    RUN_SCHEMA,
    Store,
    describe_handoff,
    lock_held,
    now_iso,
    process_alive,
    read_json,
    version_key,
)
from dogpdkb.settings import load_config, select_book


def view_run(path: Path) -> dict:
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != RUN_SCHEMA:
        raise ValueError(f"unsupported run record: {path}")
    recorded = payload["status"]
    alive = bool(
        recorded == "running"
        and process_alive(payload.get("pid"))
        and payload.get("lock")
        and lock_held(Path(payload["lock"]), payload["pid"])
    )
    status = "interrupted" if recorded == "running" and not alive else recorded
    end = payload.get("ended_at") or (now_iso() if alive else payload.get("updated_at"))
    try:
        elapsed = max(
            0.0,
            (
                datetime.fromisoformat(str(end))
                - datetime.fromisoformat(payload["started_at"])
            ).total_seconds(),
        )
    except (TypeError, ValueError, KeyError):
        elapsed = None
    return {
        **payload,
        "schema": "dogpdkb-status-1",
        "status": status,
        "recorded_status": recorded,
        "alive": alive,
        "elapsed_seconds": elapsed,
        "run_directory": str(path.parent.resolve()),
    }


def document_facts(store, run):
    """Project the selected sources' recorded facts, not hashes of live files."""
    sources = set(run.get("sources", run.get("targets", {}).get("sources", {})))
    facts = {}
    for key, doc in store.data["documents"].items():
        if doc.get("source_key") not in sources:
            continue
        row = {
            "source_key": doc["source_key"],
            "source_document_id": doc.get("source_document_id"),
        }
        for name, digest_key in (
            ("observed", "directory_body_sha256"),
            ("raw", "sha256"),
            ("corpus", "input_copy_sha256"),
            ("graph", "sha256"),
        ):
            fact = doc.get(name)
            row[name] = (
                None
                if not fact
                else {
                    **{
                        field: fact[field]
                        for field in (
                            "version",
                            "path",
                            "status",
                            "ocr_status",
                            "input_copy",
                            "source_file",
                            "report",
                            "run_id",
                        )
                        if field in fact
                    },
                    "sha256": fact.get(digest_key),
                }
            )
        corpus = (row["corpus"] or {}).get("sha256")
        graph = (row["graph"] or {}).get("sha256")
        row["corpus_vs_graph"] = (
            ("same_bytes" if corpus == graph else "different_bytes")
            if corpus and graph
            else "unknown"
        )
        facts[key] = row
    return facts


def progress(store, run):
    stage = run.get("stage")
    phase = (
        SYNC_PHASES[0]
        if stage in {"discover", "download"}
        else {
            "compare": "plan",
            "graphify": "graph",
        }.get(stage, stage)
    )
    counts = run.get("counts", {})
    completed, failed, total = (
        counts.get(key) for key in ("completed", "failed", "total")
    )
    in_flight = None if run["alive"] else 0
    if stage == "download" and "progress_export_ids" in run:
        states = [
            step["status"]
            for key in run["progress_export_ids"]
            if (step := store.step(key, "export"))
            and step.get("run_id") == run["run_id"]
        ]
        completed = states.count("completed")
        failed = states.count("failed") + states.count("unknown")
        # This is the scheduler's durable admission count, not provider traffic.
        if run["alive"]:
            in_flight = states.count("running")
        else:
            failed += states.count(
                "running"
            )  # Abandoned admissions are unknown, not queued.
    if stage == "discover" and "observed" in counts:
        completed, total = counts["observed"], counts["observed"]
    pending = None
    if total is not None and completed is not None and in_flight is not None:
        pending = max(0, total - completed - (failed or 0) - in_flight)
    return {
        "phase": phase,
        "last_completed_phase": run.get("completed_phase"),
        "total": total,
        "completed": completed,
        "in_flight": in_flight,
        "pending": pending,
        "failed": failed,
        "last_progress_at": run.get("updated_at"),
    }


def current_handoff(store, handoff):
    """Show stale or settled authorizations without rewriting historical evidence."""
    if handoff is None:
        return None
    documents = store.data["documents"]

    def current_version(identifier, phase):
        row = documents.get(identifier, {})
        expected = "corpus" if phase == "graph" else "observed"
        return row.get(expected, {}).get("version")

    def outdated(identifier, version, phase):
        current = current_version(identifier, phase)
        return current is None or version_key(current) != version_key(version)

    scope = (handoff.get("graph_retry") or {}).get("documents", {})
    stale_batch = any(
        outdated(key, row["version"], "graph")
        or documents.get(key, {}).get("corpus", {}).get("input_copy_sha256")
        != row["input_sha256"]
        for key, row in scope.items()
    )
    items = []
    for item in handoff["items"]:
        eligibility = "none"
        identifier = item["document_id"]
        if item["retry_scope"] == "graph_batch":
            eligibility = "stale" if stale_batch else "revalidate"
        elif item["retry_scope"] == "document_next_batch":
            if outdated(identifier, item["version"], "ocr"):
                eligibility = "stale"
            else:
                step = store.step(identifier, "ocr") or {}
                eligibility = (
                    "revalidate"
                    if step.get("run_id") == item["run_id"]
                    and step.get("send_state") == "not_sent"
                    else "superseded"
                )
        items.append({**item, "eligibility": eligibility})
    return {**handoff, "items": items}


def operator_view(store, run):
    state = run["status"]
    sealed = run.get("checkpoints", {})
    handoff = run.get("handoff")
    if (
        run.get("command") == "sync"
        and run["recorded_status"] == "running"
        and state == "interrupted"
    ):
        handoff = describe_handoff(
            store, run, Path(run["run_directory"]) / "run.json", interrupted=True
        )
    kind, message = {
        "running": ("watch", "Keep watching; Ctrl-C exits only the observer."),
        "paused": ("resume", "Continue this frozen run with sync --resume RUN."),
        "partial": (
            "inspect_handoff",
            "Review unresolved items and their evidence; do not replay unknown sends.",
        ),
        "failed": (
            "diagnose",
            "Inspect the failure before starting or recovering work.",
        ),
        "completed": ("none", "No operator action required."),
    }.get(state, ("inspect", "Inspect the saved run before recovery."))
    if (
        state == "interrupted"
        and "differences" in run.get("artifacts", {})
        and not run.get("cleanup")
    ):
        kind, message = (
            "resume",
            "Inspect evidence, then use sync --resume RUN to reconcile the frozen run.",
        )
    graph = run.get(
        "graph_publication", "unknown" if "graph" in sealed else "not_started"
    )
    if graph == "pending" and state in {"interrupted", "failed"}:
        graph = "unknown"
    return {
        **run,
        "progress": progress(store, run),
        "publication": {
            "corpus": run.get(
                "corpus_publication", "unknown" if "corpus" in sealed else "not_started"
            ),
            "graph": graph,
        },
        "next_action": {"kind": kind, "message": message},
        "facts": document_facts(store, run),
        "facts_scope": "current_selected_sources",
        "handoff": current_handoff(store, handoff),
    }


def snapshot(store: Store, reference: str | None = None) -> dict:
    if reference:
        path = Path(reference).expanduser()
        if path.name != "run.json":
            path = (
                path / "run.json" if path.is_dir() else store.runs / path / "run.json"
            )
        data = view_run(path)
        if data.get("book", {}).get("key") != store.book.key:
            raise ValueError("run does not belong to the selected book")
        return operator_view(store, data)
    views = [view_run(path) for path in store.runs.glob("run-*/run.json")]
    if not views:
        return {"schema": "dogpdkb-status-1", "status": "no-runs", "runs": 0}
    running = [view for view in views if view["alive"]]
    newest = max(
        running or views,
        key=lambda view: (
            view.get("started_at", ""),
            (Path(view["run_directory"]) / "run.json").stat().st_mtime_ns,
        ),
    )
    return operator_view(store, newest)


def report(data: dict) -> str:
    if data["status"] == "no-runs":
        return "no sync runs recorded"
    progress = data["progress"]
    counts = " ".join(
        f"{key}={progress[key] if progress[key] is not None else 'unknown'}"
        for key in ("completed", "in_flight", "pending", "failed")
    )
    publication = data["publication"]
    different = sum(
        fact["corpus_vs_graph"] == "different_bytes" for fact in data["facts"].values()
    )
    lines = [
        (
            f"run {data['run_id']} status={data['status']} phase={progress['phase'] or 'none'}"
            f" stage={data.get('stage') or 'none'} last_completed={progress['last_completed_phase'] or 'none'}"
        ),
        f"{counts} updated={progress['last_progress_at']} elapsed={data['elapsed_seconds']}s",
        f"publication: corpus={publication['corpus']} graph={publication['graph']}; corpus/graph byte differences={different}",
        f"next: {data['next_action']['message']}",
    ]
    if (detail := data.get("phase")) and detail != progress["phase"]:
        lines.insert(1, f"tool phase={detail}")
    if data.get("reason"):
        lines.append(f"reason: {data['reason']}")
    if handoff := data.get("handoff"):
        lines.append(
            f"handoff: {handoff['unresolved']} unresolved (see --json for details)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = HelpParser(prog="dogpdkb status")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    parser.add_argument("--run")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--watch", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return (
            64
            if error.code == 2
            else 0
            if error.code is None
            else 1
            if isinstance(error.code, str)
            else error.code
        )
    try:
        config = load_config(args.config)
        book = select_book(config, args.book)
        while True:
            data = snapshot(Store(config, book), args.run)
            print(
                json.dumps(data, ensure_ascii=False) if args.json else report(data),
                flush=True,
            )
            if not args.watch:
                return 0
            time.sleep(1)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"dogpdkb status: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
