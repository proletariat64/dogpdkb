"""Admit explicitly approved Skylark export artifacts as raw facts only."""

import json
import os
import re
import sys
from pathlib import Path

from dogpdkb.help import HelpParser
from dogpdkb.ocr_batch import digest
from dogpdkb.planning import descriptor, same_version
from dogpdkb.records import Run, Store, atomic_json, read_json
from dogpdkb.settings import document_identity, load_config, select_book


def _bytes(value, root=None):
    path = Path(value)
    if (
        not path.is_absolute()
        or path.resolve(strict=True) != path
        or not path.is_file()
    ):
        raise ValueError("witness must be a canonical regular file")
    if root is not None and not path.is_relative_to(root):
        raise ValueError("witness is outside its owning run")
    return path.read_bytes()


def _single(data, schema, remote):
    payload = json.loads(data)
    rows = payload["documents"]
    if payload["schema"] != schema or len(rows) != 1:
        raise ValueError("export request/report must contain one documented row")
    identifier = rows[0]["document_id"]
    if schema == "skylark-export-request-1" and type(identifier) is int:
        identifier = str(identifier)
    if identifier != remote:
        raise ValueError("export request/report must bind the expected remote ID")
    return payload, rows[0]


def _snapshot(store, source_id, scope):
    if not re.fullmatch(r"run-\d{8}-\d{6}-[0-9a-f]{8}", source_id):
        raise ValueError("original run ID required")
    root = store.runs / source_id
    old_bytes = _bytes(root / "run.json", store.runs)
    old = Run.load(store, root)
    selection_path = old.data["artifacts"]["differences"]
    selection_bytes = _bytes(selection_path, root)
    selection = json.loads(selection_bytes)
    if (
        selection["schema"] != "dogpdkb-differences-2"
        or selection["book"]["key"] != store.book.key
    ):
        raise ValueError("invalid original selection")
    rows, captures, seen = [], [], set()
    for spec in scope:
        remote, source_key = spec["remote_id"], spec["source_key"]
        [selected] = [
            r
            for r in selection["documents"]
            if (r["source_document_id"], r["source_key"]) == (remote, source_key)
        ]
        doc_id, version = selected["document_id"], selected["version"]
        if doc_id in seen or not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
            raise ValueError("duplicate or invalid canonical document ID")
        seen.add(doc_id)
        binding = old.data["targets"]["sources"][source_key]
        include = binding.get("document_ids")
        if (
            binding["type"] != "yuque"
            or doc_id != document_identity(source_key, remote)
            or (include is not None and remote not in include)
            or remote in binding.get("exclude_document_ids", ())
        ):
            raise ValueError("staged ID is outside the frozen source subscription")
        current = store.data["documents"][doc_id]
        step = current["steps"]["export"]
        blocked = store.blocked_attempt(doc_id, "export", version)
        if (
            current["source_key"] != source_key
            or current["source_document_id"] != remote
            or any(
                current.get(fact)
                for fact in ("raw", "corpus", "graph", "baseline", "graph_baseline")
            )
            or current.get("absence")
            or not same_version(current["observed"]["version"], version)
            or step["run_id"] != source_id
            or step["status"] != "failed"
            or not same_version(step["version"], version)
            or not blocked
            or blocked["status"] != "failed"
            or blocked["run_id"] != source_id
        ):
            raise ValueError(
                f"original export failure or frozen version no longer active for {doc_id}"
            )
        [failure] = [
            x
            for x in old.data["failures"]
            if x["document_id"] == doc_id
            and x["step"] == "export"
            and same_version(x["version"], version)
        ]
        if (
            failure["report"] != step["report"]
            or failure["report"] is None
            or failure["input"] is None
        ):
            raise ValueError("original failure paths are not bound")
        report_path = Path(failure["report"])
        slot = re.fullmatch(r"export-(\d+)-report\.json", report_path.name)
        if slot is None or failure["input"] != step["path"]:
            raise ValueError("original export slot does not match its recorded attempt")
        prior_report = _bytes(report_path, root)
        prior_request = _bytes(
            report_path.with_name(f"export-{slot[1]}-request.json"), root
        )
        request, report, body = (
            _bytes(spec[key]) for key in ("request", "report", "markdown")
        )
        _, prior = _single(prior_request, "skylark-export-request-1", remote)
        _, failed = _single(prior_report, "skylark-export-result-1", remote)
        _, requested = _single(request, "skylark-export-request-1", remote)
        ok, result = _single(report, "skylark-export-result-1", remote)
        metadata = result["metadata"]
        timestamp = metadata.get("content_updated_at")
        if timestamp is None:
            timestamp = metadata.get("updated_at")
        if (
            failed["status"] != "failed"
            or failed["path"] != prior["path"]
            or prior["path"] != failure["input"]
            or requested.get("format", "md") != prior.get("format", "md")
            or requested.get("format", "md") != selected.get("format", "md")
            or requested["path"] != result["path"]
            or result["path"] != spec["markdown"]
            or result["status"] != "completed"
            or ok["status"] != "completed"
            or ok["counts"] != {"total": 1, "completed": 1, "failed": 0}
            or str(metadata["id"]) != remote
            or not same_version(result["version"], version)
            or (timestamp is not None and not same_version(timestamp, version))
            or type(result["bytes"]) is not int
            or result["bytes"] != len(body)
            or not body.decode("utf-8").strip()
        ):
            raise ValueError(f"invalid staged export witnesses for {doc_id}")
        parts = dict(
            zip(
                (
                    "original-request.json",
                    "original-report.json",
                    "staged-request.json",
                    "staged-report.json",
                    "raw.md",
                ),
                (prior_request, prior_report, request, report, body),
            )
        )
        rows.append(
            {
                "input": spec,
                "selected": selected,
                "previous_failure": failure,
                "previous_state": current,
                "sha256": {key: digest(data) for key, data in parts.items()},
            }
        )
        captures.append(parts)
    if not rows:
        raise ValueError("explicit nonempty staged scope required")
    return {
        "schema": "dogpdkb-export-recovery-1",
        "targets": store.run_targets(),
        "original_run": source_id,
        "original_run_sha256": digest(old_bytes),
        "selection_sha256": digest(selection_bytes),
        "documents": rows,
    }, captures


def prepare(store, source_id, staged, output):
    data = read_json(staged)
    if data["schema"] != "dogpdkb-staged-export-1":
        raise ValueError("invalid staged export input schema")
    receipt, _ = _snapshot(store, source_id, data["documents"])
    output = output.absolute()
    if output.resolve() != output or any(
        output.is_relative_to(path.resolve())
        for path in (
            store.path.parent,
            store.runs,
            store.book.corpus,
            store.settings.graph_for(store.book),
        )
    ):
        raise ValueError(
            "prepare requires a new private directory outside instance write roots"
        )
    output.mkdir(mode=0o700)
    path = output / "receipt.json"
    atomic_json(path, receipt, durable=True)
    return {
        "receipt": str(path),
        "receipt_sha256": digest(path.read_bytes()),
        "documents": len(receipt["documents"]),
    }


def _copy(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != data:
        raise OSError("durable evidence copy did not match")


def publish(store, path, sha):
    payload = _bytes(path)
    if digest(payload) != sha:
        raise ValueError("receipt SHA-256 mismatch")
    receipt = json.loads(payload)
    current, captures = _snapshot(
        store, receipt["original_run"], [row["input"] for row in receipt["documents"]]
    )
    if current != receipt:
        raise ValueError("staged or original evidence changed after prepare")
    run = Run.create(store, "recover-export")
    durable = store.path.parent / "export-recovery" / run.id
    durable.mkdir(mode=0o700, parents=True)
    receipt_path = durable / "receipt.json"
    _copy(receipt_path, payload)
    records = []
    for row, parts in zip(receipt["documents"], captures, strict=True):
        selected = row["selected"]
        doc_id = selected["document_id"]
        prefix = durable / doc_id
        prefix.mkdir(mode=0o700)
        for name, data in parts.items():
            _copy(prefix / name, data)
        raw = prefix / "raw.md"
        records.append(
            {
                "doc_id": doc_id,
                "name": "export",
                "version": selected["version"],
                "status": "completed",
                "run_id": run.id,
                "path": str(raw),
                "report": str(prefix / "staged-report.json"),
                "recovery_receipt": str(receipt_path),
                "source_run_id": receipt["original_run"],
            }
        )
        row["admitted_raw"] = {
            "version": selected["version"],
            "path": str(raw),
            "sha256": row["sha256"]["raw.md"],
            "metadata": descriptor(store.book, selected, raw)["metadata"],
        }
    handoff = durable / "admitted.json"
    atomic_json(
        handoff,
        {
            "schema": "dogpdkb-admitted-export-1",
            "receipt_sha256": sha,
            "documents": [
                {"document_id": row["selected"]["document_id"], **row["admitted_raw"]}
                for row in receipt["documents"]
            ],
        },
        durable=True,
    )
    for directory in [
        *(durable / record["doc_id"] for record in records),
        durable,
        durable.parent,
    ]:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    run.data["artifacts"].update(receipt=str(receipt_path), admitted=str(handoff))
    run.data["admitted_sha256"] = digest(handoff.read_bytes())
    run.stage("download", total=len(records))
    run.save(durable=True)
    for row in receipt["documents"]:
        store.document(row["selected"]["document_id"])["raw"] = row["admitted_raw"]
    store.complete_batch(records, durable=True)
    run.stage("download", total=len(records), completed=len(records))
    run.finish("completed")
    return {
        "run": str(run.path),
        "admitted": str(handoff),
        "admitted_sha256": digest(handoff.read_bytes()),
        "documents": len(records),
    }


def verify_admission(store, data, body):
    """Revalidate an admitted-export handoff against committed state, read-only.

    Returns the recover-export run that committed the admission. Raises on any
    drift; never starts external tools and never enters the Skylark path.
    """
    run_ids = set()
    for row in data["documents"]:
        step = store.step(row["document_id"], "export") or {}
        if step.get("status") != "completed" or step.get("run_id") is None:
            raise ValueError(f"{row['document_id']} has no completed export admission")
        run_ids.add(step["run_id"])
    if len(run_ids) != 1:
        raise ValueError("admitted documents do not share one admission run")
    candidate = store.runs / run_ids.pop()
    resolved_runs = store.runs.resolve()
    if not candidate.resolve().is_relative_to(resolved_runs) or not (
        candidate / "run.json"
    ).resolve().is_relative_to(resolved_runs):
        raise ValueError("recovery run is outside the committed staging root")
    run = Run.load(store, candidate)
    if run.data["command"] != "recover-export":
        raise ValueError("resume requires a staged export recovery run")
    if not run.path.resolve().is_relative_to(store.runs.resolve()):
        raise ValueError("recovery run is outside the committed staging root")
    durable = store.path.parent / "export-recovery" / run.id
    handoff = Path(run.data["artifacts"]["admitted"])
    if _bytes(handoff, durable) != body:
        raise ValueError("admitted handoff differs from the recorded run")
    if digest(body) != run.data["admitted_sha256"]:
        raise ValueError("admitted handoff SHA-256 changed")
    receipt = Path(run.data["artifacts"]["receipt"])
    receipt_bytes = _bytes(receipt, durable)
    if digest(receipt_bytes) != data["receipt_sha256"]:
        raise ValueError("durable receipt SHA-256 changed")
    receipt_rows = {
        row["selected"]["document_id"]: row
        for row in json.loads(receipt_bytes)["documents"]
    }
    if set(receipt_rows) != {row["document_id"] for row in data["documents"]}:
        raise ValueError("admitted scope does not match its authenticated receipt")
    for row in data["documents"]:
        identifier = row["document_id"]
        receipt_row = receipt_rows[identifier]
        selected = receipt_row["selected"]
        spec = receipt_row["input"]
        current = store.data["documents"][identifier]
        if current.get("absence"):
            raise ValueError(
                f"{identifier} has a recorded absence; do not reuse this handoff"
            )
        if (
            current.get("source_key") != spec["source_key"]
            or current.get("source_document_id") != spec["remote_id"]
        ):
            raise ValueError(f"admitted identity drifted for {identifier}")
        try:
            observed = same_version(
                current.get("observed", {}).get("version"), selected["version"]
            )
        except (TypeError, ValueError):
            observed = False
        if not observed:
            raise ValueError(f"frozen version no longer observed for {identifier}")
        raw = {key: value for key, value in row.items() if key != "document_id"}
        step = store.step(identifier, "export") or {}
        staged_report = str(durable / identifier / "staged-report.json")
        if (
            current.get("raw") != raw
            or step.get("run_id") != run.id
            or step.get("source_run_id") != json.loads(receipt_bytes)["original_run"]
            or not same_version(step.get("version"), raw.get("version"))
            or not same_version(raw.get("version"), selected["version"])
            or step.get("path") != raw["path"]
            or step.get("report") != staged_report
            or step.get("recovery_receipt") != str(receipt)
            or digest(_bytes(raw["path"], durable)) != raw["sha256"]
        ):
            raise ValueError(
                "raw state was not committed or has drifted; do not reuse this handoff"
            )
        if "metadata" in selected:
            expected_metadata = descriptor(
                store.book,
                {**selected, "document_id": identifier},
                Path(raw["path"]),
            )["metadata"]
            if expected_metadata != raw["metadata"]:
                raise ValueError(f"admitted metadata drifted for {identifier}")
        evidence = durable / identifier
        for name, expected in receipt_row["sha256"].items():
            if digest(_bytes(evidence / name, durable)) != expected:
                raise ValueError(f"durable evidence changed for {identifier}")
    return run


def resume(store, reference):
    match = re.fullmatch(r"run-\d{8}-\d{6}-[0-9a-f]{8}", reference)
    candidate = (store.runs / reference if match else Path(reference)).resolve()
    resolved_runs = store.runs.resolve()
    if not candidate.is_relative_to(resolved_runs) or not (
        candidate / "run.json"
    ).resolve().is_relative_to(resolved_runs):
        raise ValueError("recovery run is outside the committed staging root")
    run = Run.load(store, candidate)
    handoff = Path(run.data["artifacts"]["admitted"])
    body = _bytes(handoff, store.path.parent / "export-recovery" / run.id)
    data = json.loads(body)
    verified = verify_admission(store, data, body)
    if verified.id != run.id:
        raise ValueError("admission was committed by a different recovery run")
    run = verified
    if run.data["status"] != "completed":
        run.stage(
            "download", total=len(data["documents"]), completed=len(data["documents"])
        )
        run.finish("completed")
    return {
        "run": str(run.path),
        "admitted": str(handoff),
        "admitted_sha256": digest(body),
        "documents": len(data["documents"]),
    }


def main(argv=None):
    parser = HelpParser(prog="dogpdkb recover-export")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--run", help="original run ID for preparation")
    modes.add_argument("--publish", type=Path, help="approved preparation receipt")
    modes.add_argument(
        "--resume", help="verify and finalize an already committed raw admission"
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--receipt-sha256")
    try:
        args = parser.parse_args(argv)
        if args.run and (not args.input or not args.output_dir or args.receipt_sha256):
            parser.error("--run requires --input and --output-dir")
        if args.publish and (args.input or args.output_dir or not args.receipt_sha256):
            parser.error("--publish requires --receipt-sha256")
        if args.resume and (args.input or args.output_dir or args.receipt_sha256):
            parser.error("--resume takes only the new admission run")
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    try:
        settings = load_config(args.config)
        store = Store(settings, select_book(settings, args.book))
        if args.run:
            result = prepare(store, args.run, args.input, args.output_dir)
        else:
            with store.lock():
                result = (
                    publish(store, args.publish.absolute(), args.receipt_sha256)
                    if args.publish
                    else resume(store, args.resume)
                )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"dogpdkb recover-export: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
