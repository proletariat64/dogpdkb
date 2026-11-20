"""Prepare an audited offline recovery of a sealed, interrupted OCR batch."""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from dogpdkb.apply import (
    corpus_destination,
    prepare_publication,
    publish_corpus,
    seal_corpus,
)
from dogpdkb.help import HelpParser
from dogpdkb.inputs import publication_body
from dogpdkb.ocr_batch import (
    _index,
    _load_plan,
    _path,
    _request_history,
    _settled_report,
    _tool_result,
    _verified_result,
    _verify_running_journal,
    digest,
    file_digest,
)
from dogpdkb.phases import (
    check_graph_manifest,
    graph_generation,
    read_checkpoint,
    seal_phase,
)
from dogpdkb.planning import same_version
from dogpdkb.processes import run_command
from dogpdkb.records import Run, Store, atomic_json, read_json
from dogpdkb.settings import load_config, select_book

_JOURNAL = "plan.json.execution.json"
_WITNESSES = {"request.json", "plan.json", _JOURNAL, "result.json"}


def _source(store, run_id):
    if not re.fullmatch(r"run-\d{8}-\d{6}-[0-9a-f]{8}", run_id):
        raise ValueError("invalid source run ID")
    run = Run.load(store, run_id)
    batches = store.path.parent / "ocr"
    request_path = _path(
        batches,
        run.data["artifacts"].get("ocr_request", batches / run_id / "request.json"),
    )
    root = request_path.parent
    if request_path.name != "request.json" or root.parent != batches:
        raise ValueError("original OCR request must be a direct book batch artifact")
    seal_path = _path(root, root / "settled.json")
    seal_hash = file_digest(seal_path)
    seal = read_json(seal_path)
    witnesses = seal["witnesses"]
    if (
        seal["dispatched"] is not True
        or seal["reason"] != "OCR_RESULT_BINDING_INVALID"
        or seal["report"] is not None
        or set(witnesses) != _WITNESSES
        or witnesses[_JOURNAL] != witnesses["result.json"]
    ):
        raise ValueError(
            "recovery requires a sealed invalid result and matching native journal"
        )
    _settled_report(
        root, seal
    )  # Validates every recorded witness, even when report is null.
    start = {key: witnesses[key] for key in ("request.json", "plan.json")}
    if read_json(_path(root, root / "started.json")) != start:
        raise ValueError(
            "original OCR admission does not bind the frozen plan and request"
        )
    request = read_json(root / "request.json")
    if request["schema"] != "dogpdkb-ocr-request-3" or request["run_id"] != run_id:
        raise ValueError("original OCR request does not belong to the selected run")
    plan, docs = _load_plan(root, request)
    state = _tool_result(root, root / _JOURNAL, witnesses["plan.json"])
    if state["documents"]:
        raise ValueError(
            "recovery is for nonterminal journals without document results"
        )
    _verify_running_journal(
        state,
        request["policy"]["request_budget"],
        fix=plan["settings"].get("fix", 0),
        repair=plan["settings"].get("mermaid_repair_rounds", 0),
    )
    return root, {"settled.json": seal_hash, **witnesses}, request, plan, docs, state


def _baseline(store, entry):
    identifier = entry["document_id"]
    current = store.data["documents"][identifier]
    raw, corpus = current["raw"], current["corpus"]
    if not all(same_version(entry["version"], row["version"]) for row in (raw, corpus)):
        raise ValueError(f"current raw or corpus version drifted for {identifier}")
    corpus_path = _path(store.book.corpus, corpus["path"])
    if corpus_destination(store.book, identifier, corpus) != corpus_path:
        raise ValueError(
            f"recovery would change canonical corpus path for {identifier}"
        )
    raw_sha = entry["source_sha256"]
    corpus_sha = file_digest(corpus_path)
    if (
        raw["sha256"] != raw_sha
        or file_digest(Path(raw["path"])) != raw_sha
        or file_digest(Path(corpus["input_copy"])) != corpus_sha
        or corpus.get("input_copy_sha256", corpus_sha) != corpus_sha
    ):
        raise ValueError(f"raw or published corpus witness changed for {identifier}")
    resources = _index(raw.get("resources", []), "resource_id")
    if any(
        file_digest(Path(resources[asset["resource_id"]]["path"])) != asset["sha256"]
        for asset in entry["assets"].values()
    ):
        raise ValueError(f"current raw image witness changed for {identifier}")
    return {
        "raw": raw,
        "corpus": corpus,
        "corpus_sha256": corpus_sha,
        "graph": current.get("graph", {}),
    }


def _read_assembly(source, hashes, request, plan, docs, state, directory, version):
    path = _path(directory, directory / "assembly.json")
    report = read_json(path)
    if (
        report["schema_version"] != "mdocr-result-2"
        or report["result_path"] != str(path)
        or report["plan_sha256"] != hashes["plan.json"]
        or report["recovery"]
        != {"execution_version": version, "journal_sha256": hashes[_JOURNAL]}
        or report["status"] not in {"completed", "partial"}
        or report["budget"] != state["budget"]
    ):
        raise ValueError("derived OCR result has invalid provenance")
    expected = [dict(item) for item in state["requests"]]
    for item in expected:
        if item["status"] == "admitted":
            item.update(status="unknown", reason="OCR_OUTCOME_UNKNOWN")
    if report["requests"] != expected:
        raise ValueError("derived OCR result changed original attempts or usage")
    rows = _index(report["documents"])
    if rows.keys() != docs.keys():
        raise ValueError("derived OCR result changed the planned document scope")
    positions = {doc["document_id"]: n for n, doc in enumerate(plan["documents"])}
    results = {}
    for entry in request["documents"]:
        doc = docs[entry["path"]]
        pos = positions[doc["document_id"]]
        row = rows[entry["path"]]
        result, _, _, _ = _verified_result(
            source,
            plan,
            {**doc, "output": str(directory / "documents" / f"{pos}.md")},
            entry,
            row,
            request_history=lambda: _request_history(report),
            output_root=directory,
        )
        results[entry["document_id"]] = result
    return path, results


def _snapshot(store, request, results):
    rows = {}
    for entry in request["documents"]:
        identifier = entry["document_id"]
        baseline = _baseline(store, entry)
        result = results[identifier]
        output_sha = (
            digest(
                publication_body(
                    Path(result["output"]), baseline["raw"]["resources"], result
                )
            )
            if result
            else None
        )
        if result is None:
            disposition = "partial"
        elif output_sha == baseline["corpus_sha256"]:
            disposition = "unchanged"
        elif result["ocr"]["images"]:
            if baseline["corpus"].get("ocr"):
                raise ValueError(
                    f"refusing to replace existing OCR evidence for {identifier}"
                )
            disposition = "replace"
        else:
            raise ValueError(
                f"image-less output differs from the published corpus for {identifier}"
            )
        rows[identifier] = {
            "disposition": disposition,
            "baseline": baseline,
            "output_sha256": output_sha,
        }
    return rows


def _assemble(settings, snapshot, version, directory):
    source = snapshot[0]
    code = run_command(
        [
            *settings.command("mdocr"),
            "assemble",
            "--plan",
            str(source / "plan.json"),
            "--journal",
            str(source / _JOURNAL),
            "--execution-version",
            version,
            "--output-dir",
            str(directory),
        ],
        log=directory.parent / "mdocr.log",
        timeout=settings.ocr_budget_seconds,
    )
    if code in {130, 143}:
        raise InterruptedError(code)
    if code not in {0, 2}:
        raise ValueError(
            f"mdocr offline assembly failed ({code}); see recovery workspace log"
        )
    return _read_assembly(*snapshot, directory, version)


def prepare(settings, store, run_id, version, output):
    source, hashes, request, plan, docs, state = _source(store, run_id)
    output = output.absolute()
    roots = (
        store.path.parent,
        store.runs,
        store.book.corpus,
        settings.graph_for(store.book),
    )
    if output.resolve() != output or any(
        output.is_relative_to(p.resolve()) for p in roots
    ):
        raise ValueError(
            "recovery preparation must use a fresh workspace outside production roots"
        )
    generation = graph_generation(store, store.book)
    output.mkdir(mode=0o700)
    directory = output / "assembled"
    assembly, results = _assemble(
        settings, (source, hashes, request, plan, docs, state), version, directory
    )
    rows = _snapshot(store, request, results)
    if (
        generation != graph_generation(store, store.book)
        or hashes != _source(store, run_id)[1]
    ):
        raise ValueError("original OCR or graph generation changed during preflight")
    receipt = {
        "schema": "dogpdkb-ocr-recovery-1",
        "book": store.book.key,
        "targets": store.run_targets(),
        "source_run_id": run_id,
        "execution_version": version,
        "source_witnesses": hashes,
        "assembly_sha256": file_digest(assembly),
        "graph_generation": generation,
        "documents": rows,
    }
    path = output / "recovery.json"
    atomic_json(path, receipt, durable=True)
    return {
        "receipt": str(path),
        "receipt_sha256": file_digest(path),
        "requests": dict(Counter(item["status"] for item in state["requests"])),
        "documents": dict(Counter(row["disposition"] for row in rows.values())),
    }


def _check_receipt(store, receipt):
    if (
        receipt["schema"] != "dogpdkb-ocr-recovery-1"
        or receipt["book"] != store.book.key
        or receipt["targets"] != store.run_targets()
        or receipt["graph_generation"] != graph_generation(store, store.book)
    ):
        raise ValueError("recovery receipt book, target, or graph generation changed")
    snapshot = _source(store, receipt["source_run_id"])
    if receipt["source_witnesses"] != snapshot[1]:
        raise ValueError("original OCR witnesses differ from recovery receipt")
    return snapshot


def _freeze_publication(store, run, receipt, snapshot, results):
    rows, replacements, retained = [], [], []
    for entry in snapshot[2]["documents"]:
        identifier = entry["document_id"]
        saved = receipt["documents"][identifier]
        current = saved["baseline"]
        corpus, raw, graph = (current[key] for key in ("corpus", "raw", "graph"))
        disposition = saved["disposition"]
        action = "compile" if disposition == "replace" else "defer"
        row = {
            "document_id": identifier,
            "source_key": entry["source_key"],
            "version": entry["version"],
            "raw_path": raw["path"],
            "input_copy": entry["raw_copy"],
            "raw_sha256": entry["source_sha256"],
            "resources": raw["resources"],
            "metadata": corpus["metadata"],
            "previous_corpus": corpus,
            "previous_corpus_sha256": current["corpus_sha256"],
            "previous_graph": graph,
            "baseline": graph,
            "action": action,
            "reason_codes": ["OCR_RECOVERY_" + disposition.upper()],
        }
        rows.append(row)
        if disposition == "replace":
            replacements.append(row)
            result = results[identifier]
            target = run.directory / "recovery-publication" / f"{len(replacements)}.md"
            body = publication_body(Path(result["output"]), raw["resources"], result)
            if digest(body) != saved["output_sha256"]:
                raise ValueError("publication-ready recovery body changed")
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(body)
            results[identifier] = {**result, "output": str(target)}
        else:
            # Unchanged/partial rows keep the exact published corpus facts;
            # deferred rows use existing native retention/omission rules.
            retained.append(
                {
                    **corpus,
                    "document_id": identifier,
                    "action": action,
                    "previous_graph": graph,
                    "baseline_version": graph.get("version"),
                    "reason_codes": row["reason_codes"],
                    "pending_steps": [],
                }
            )
    plan = {
        "schema": "dogpdkb-plan-1",
        "book": store.book.key,
        "run_id": run.id,
        "documents": rows,
    }
    path = run.directory / "plan.json"
    atomic_json(path, plan, durable=True)
    run.data["artifacts"]["plan"] = str(path)
    publication = prepare_publication(store.book, store, run, replacements, results)
    publication["documents"].extend(retained)
    atomic_json(Path(run.data["artifacts"]["publication"]), publication, durable=True)
    seal_phase(run, "ocr", "plan", "publication", "recovery", "assembly")


def _finish_publication(store, run, receipt):
    from dogpdkb.apply import corpus_fact, validate_publication_target

    plan = read_checkpoint(run, "ocr", "plan")
    publication = read_checkpoint(run, "ocr", "publication")
    by_id = _index(publication["documents"])
    # Validate every destination before any write. Frozen before/after states
    # allow recovery from a crash between replacing a file and recording it.
    for row in plan["documents"]:
        identifier = row["document_id"]
        baseline = receipt["documents"][identifier]["baseline"]
        live = store.data["documents"][identifier]
        after = by_id[identifier]
        if (
            live.get("raw") != baseline["raw"]
            or live.get("graph", {}) != baseline["graph"]
            or live.get("corpus") not in (baseline["corpus"], corpus_fact(after))
        ):
            raise ValueError(f"published recovery baseline drifted for {identifier}")
        validate_publication_target(after)
    manifest = publish_corpus(store.book, store, run, plan, publication)
    seal_corpus(run)
    partial = any(
        row["disposition"] == "partial" for row in receipt["documents"].values()
    )
    run.finish("partial" if partial else "completed", 2 if partial else 0)
    return {
        "run": str(run.path),
        "manifest": str(manifest),
        "manifest_sha256": file_digest(manifest),
        "status": run.data["status"],
    }


def publish(settings, store, path, sha):
    path = path.resolve(strict=True)
    if file_digest(path) != sha:
        raise ValueError("recovery receipt SHA-256 mismatch")
    receipt = read_json(path)
    snapshot = _check_receipt(store, receipt)
    directory = path.parent / "assembled"
    if (
        file_digest(_path(directory, directory / "assembly.json"))
        != receipt["assembly_sha256"]
    ):
        raise ValueError("offline assembly differs from recovery receipt")
    _, results = _read_assembly(*snapshot, directory, receipt["execution_version"])
    if _snapshot(store, snapshot[2], results) != receipt["documents"]:
        raise ValueError("recovery targets or prepared outputs changed")
    run = Run.create(store, "recover-ocr")
    durable = store.path.parent / "recovery" / run.id
    durable.mkdir(mode=0o700, parents=True)
    run.data["graph_generation"] = receipt["graph_generation"]
    run.data["sync_policy"] = {"graphify_inputs": "text"}
    try:
        assembly, results = _assemble(
            settings, snapshot, receipt["execution_version"], durable / "assembled"
        )
        if _snapshot(store, snapshot[2], results) != receipt["documents"]:
            raise ValueError("offline reassembly did not reproduce the approved output")
        _check_receipt(store, receipt)
        frozen = durable / "recovery.json"
        atomic_json(frozen, receipt, durable=True)
        run.data["artifacts"].update(recovery=str(frozen), assembly=str(assembly))
        _freeze_publication(store, run, receipt, snapshot, results)
        return _finish_publication(store, run, receipt)
    except InterruptedError as error:
        run.finish("interrupted", error.args[0])
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        run.finish("failed", 1, str(error))
        raise


def resume(store, run_id):
    run = Run.load(store, run_id)
    if run.data["command"] != "recover-ocr" or "ocr" not in run.data.get(
        "checkpoints", {}
    ):
        raise ValueError("run has no frozen OCR recovery publication")
    receipt = read_checkpoint(run, "ocr", "recovery")
    if "corpus" in run.data["checkpoints"]:
        read_checkpoint(run, "corpus", "manifest")
        manifest = Path(run.data["artifacts"]["manifest"])
        return {
            "run": str(run.path),
            "manifest": str(manifest),
            "manifest_sha256": file_digest(manifest),
            "status": "partial"
            if any(
                row["disposition"] == "partial" for row in receipt["documents"].values()
            )
            else "completed",
        }
    snapshot = _check_receipt(store, receipt)
    report = read_checkpoint(run, "ocr", "assembly")
    directory = Path(report["result_path"]).parent
    _read_assembly(*snapshot, directory, receipt["execution_version"])
    return _finish_publication(store, run, receipt)


def changed_graph_scope(store, run, data):
    """Prove a new input identity, not a retry of an interrupted same-version body."""
    receipt = read_checkpoint(run, "ocr", "recovery")
    _check_receipt(store, receipt)
    old = Run.load(store, receipt["source_run_id"])
    scope = set()
    for row in data["documents"]:
        doc_id = row["document_id"]
        prepared = receipt["documents"][doc_id]
        changed = prepared["disposition"] == "replace"
        if row["action"] != ("compile" if changed else "defer"):
            raise ValueError(
                "recovery graph scope is not the approved changed-body set"
            )
        if changed and row["input_copy_sha256"] != prepared["output_sha256"]:
            raise ValueError("recovery graph input differs from approved OCR output")
        step = store.step(doc_id, "graph") or {}
        if (
            not store.suppressed(doc_id, "graph", row["version"])
            or step.get("run_id") != old.id
        ):
            continue
        if (
            old.data.get("stage") != "graphify"
            or old.data.get("status") != "interrupted"
            or old.data.get("exit_code") not in {130, 143}
            or step.get("status") != "unknown"
            or not same_version(step["version"], row["version"])
        ):
            continue
        frozen = old.data.get("graph_retry", {})
        saved = frozen.get("documents", {}).get(doc_id, {})
        if not (
            saved.get("action") == "compile"
            and same_version(saved.get("version"), row["version"])
        ):
            continue
        if (
            saved.get("input_sha256") != prepared["baseline"]["corpus_sha256"]
            or file_digest(Path(saved["input_copy"])) != saved["input_sha256"]
            or changed != (row["input_copy_sha256"] != saved["input_sha256"])
        ):
            continue
        scope.add(doc_id)
    if scope:
        check_graph_manifest(old, old.data["graph_retry"]["manifest"], None)
        before = old.data.get("graph_generation") or {}
        if any(
            before.get(name) != receipt["graph_generation"][name]
            for name in ("graph.json", "manifest.json")
        ):
            raise ValueError(
                "public graph artifacts changed after the interrupted source run"
            )
    return scope


def main(argv=None):
    parser = HelpParser(prog="dogpdkb recover-ocr", description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--run", help="prepare from the original sealed OCR run ID")
    action.add_argument(
        "--publish", type=Path, help="publish from a verified recovery receipt"
    )
    action.add_argument("--resume", help="resume a frozen local recovery publication")
    parser.add_argument(
        "--execution-version", help="original mdocr version (preparation only)"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="fresh private preparation workspace"
    )
    parser.add_argument("--receipt-sha256", help="approved preparation receipt SHA-256")
    try:
        args = parser.parse_args(argv)
        if args.run and (
            not args.execution_version or not args.output_dir or args.receipt_sha256
        ):
            parser.error("--run requires --execution-version and --output-dir only")
        if not args.run and (
            args.execution_version
            or args.output_dir
            or bool(args.publish) != bool(args.receipt_sha256)
        ):
            parser.error(
                "--publish requires --receipt-sha256; --resume takes only a run ID"
            )
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    try:
        settings = load_config(args.config)
        store = Store(settings, select_book(settings, args.book))
        if args.run:
            result = prepare(
                settings, store, args.run, args.execution_version, args.output_dir
            )
        else:
            with store.lock():
                result = (
                    publish(settings, store, args.publish, args.receipt_sha256)
                    if args.publish
                    else resume(store, args.resume)
                )
    except InterruptedError as error:
        return error.args[0]
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"dogpdkb recover-ocr: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result.get("status") == "partial" else 0
