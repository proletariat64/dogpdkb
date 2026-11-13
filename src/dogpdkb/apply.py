"""Serial download, public comparison, fixed plan, OCR and corpus publication."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from dogpdkb.planning import (
    descriptor,
    load_plan,
    make_plan,
    reject_rollback,
    same_version,
    validate_inputs,
    validate_plan,
)
from dogpdkb.processes import diagnostic_argv, run_command
from dogpdkb.records import Run, Store, atomic_json, read_json, version_key
from dogpdkb.settings import load_config, select_book


def call_tool(settings, run, name, arguments, *, timeout=None):
    argv = settings.command(name) + [str(v) for v in arguments]
    run.data["tool"] = {"name": name, "argv": diagnostic_argv(argv)}
    run.save()
    code = run_command(
        argv,
        log=run.directory / f"{name}.log",
        timeout=timeout,
        env=settings.skylark_environment() if name == "skylark" else None,
    )
    if code in {130, 143}:
        raise InterruptedError(code)
    return code


def failure(run, row, step, reason, **details):
    tool = run.data.get("tool", {})
    expected = {"export": "skylark", "ocr": "mdocr", "compare": "mdcompare"}
    if "exit_code" in details and tool.get("name") == expected.get(step):
        details.setdefault("argv", tool.get("argv"))
    run.failure(
        document_id=row["document_id"],
        version=row.get("version"),
        step=step,
        reason=reason,
        input=row.get("raw_path"),
        **details,
    )


def prepare_inputs(settings, book, store, run, differences):
    if (
        differences.get("schema") != "dogpdkb-differences-1"
        or differences.get("book", {}).get("key") != book.key
        or differences.get("book", {}).get("url") != book.url
    ):
        raise ValueError("difference schema or book mismatch")
    selected = differences.get("documents")
    if not isinstance(selected, list):
        raise TypeError("difference documents missing")
    identifiers = [
        str(row.get("document_id", "")) for row in selected if isinstance(row, dict)
    ]
    if (
        len(identifiers) != len(selected)
        or any(not value for value in identifiers)
        or len(set(identifiers)) != len(identifiers)
    ):
        raise ValueError("difference source IDs must be unique and nonempty")
    if book.document_ids is not None and not set(identifiers).issubset(
        book.document_ids
    ):
        raise ValueError("difference source IDs outside subscription")
    atomic_json(run.directory / "differences.json", differences)
    run.data["artifacts"]["differences"] = str(run.directory / "differences.json")
    prepared_path = run.directory / "prepared-inputs.json"
    prepared = read_json(prepared_path) if prepared_path.exists() else {}
    validate_inputs(prepared.values(), check_baseline=True)

    def freeze_input(row):
        identifier = row["document_id"]
        if identifier not in prepared:
            witness = run.directory / "plan-inputs" / f"{row['slot']}.md"
            witness.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(row["raw_path"], witness)
            row["input_copy"] = str(witness)
            baseline = row["baseline"].get("path")
            if baseline and Path(baseline).is_file():
                expected = run.directory / "baseline-inputs" / f"{row['slot']}.md"
                expected.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(baseline, expected)
                row["baseline_input_copy"] = str(expected)
            prepared[identifier] = row
            atomic_json(prepared_path, prepared)
            run.data["artifacts"]["prepared_inputs"] = str(prepared_path)
            run.save()

    rows, requests = [], []
    for index, selected_row in enumerate(selected):
        row = dict(selected_row)
        row["slot"] = index
        identifier = row["document_id"] = str(row["document_id"])
        if identifier in prepared:
            reject_rollback(store, [prepared[identifier]])
            rows.append(prepared[identifier])
            continue
        try:
            version_key(row.get("version"))
        except ValueError:
            failure(run, row, "export", "SOURCE_VERSION_UNKNOWN")
            continue
        reject_rollback(store, [row])
        document = store.document(identifier)
        previous = document.get("raw", {})
        baseline = (
            previous
            if Path(previous.get("path", "")).is_file()
            else document.get("corpus", {})
        )
        row["baseline"] = dict(baseline)
        row["previous_corpus"] = dict(document.get("corpus", {}))
        row["previous_graph"] = dict(document.get("graph", {}))
        existing = Path(previous.get("path", ""))
        desired_metadata = descriptor(book, row, existing)["metadata"]
        if (
            previous.get("version")
            and same_version(previous["version"], row["version"])
            and existing.is_file()
            and previous.get("metadata") == desired_metadata
        ):
            row["raw_path"] = str(existing.resolve())
            freeze_input(row)
        elif store.suppressed(identifier, "export", row["version"]):
            failure(run, row, "export", "VERSION_ATTEMPT_SUPPRESSED")
            continue
        else:
            raw = run.directory / "raw" / f"{index}.md"
            row["raw_path"] = str(raw)
            requests.append(
                {
                    "document_id": identifier,
                    "path": str(raw),
                    "format": row.get("format", "md"),
                }
            )
        rows.append(row)
    if requests:
        run.stage("download", total=len(requests))
        by_id = {row["document_id"]: row for row in rows}
        kept = {row["document_id"] for row in rows} - {
            item["document_id"] for item in requests
        }
        for index, item in enumerate(requests):
            row = by_id[item["document_id"]]
            request = run.directory / f"export-{row['slot']}-request.json"
            report = run.directory / f"export-{row['slot']}-report.json"
            atomic_json(
                request, {"schema": "skylark-export-request-1", "documents": [item]}
            )
            store.attempt(
                row["document_id"],
                "export",
                row["version"],
                run.id,
                input=str(request),
                report=str(report),
            )
            code = call_tool(
                settings,
                run,
                "skylark",
                ["export", "--request", request, "--output", report],
            )
            result = {}
            try:
                payload = read_json(report)
                if payload.get("schema") != "skylark-export-result-1" or code not in {
                    0,
                    2,
                }:
                    raise ValueError("unreliable export report")
                results = payload["documents"]
                if (
                    len(results) != 1
                    or str(results[0]["document_id"]) != row["document_id"]
                ):
                    raise ValueError("export source mismatch")
                result = results[0]
            except (ValueError, KeyError, TypeError):
                pass
            valid = (
                result.get("status") == "completed" and Path(row["raw_path"]).is_file()
            )
            try:
                valid = valid and same_version(result.get("version"), row["version"])
            except ValueError:
                valid = False
            status = "completed" if valid else "failed" if result else "unknown"
            store.complete(
                row["document_id"],
                "export",
                row["version"],
                status,
                run.id,
                report=str(report),
                exit_code=code,
                path=row["raw_path"],
            )
            if valid:
                store.document(row["document_id"])["raw"] = {
                    "version": row["version"],
                    "path": row["raw_path"],
                    "metadata": descriptor(book, row, row["raw_path"])["metadata"],
                }
                store.save()
                freeze_input(row)
                kept.add(row["document_id"])
            else:
                failure(
                    run,
                    row,
                    "export",
                    "EXPORT_FAILED_OR_VERSION_MISMATCH",
                    exit_code=code,
                    report=str(report),
                )
            if code not in {0, 2}:
                raise ValueError(f"Skylark export unavailable ({code}); see {report}")
        rows = [row for row in rows if row["document_id"] in kept]
    return rows


def compare_inputs(settings, book, store, run, rows):
    run.stage("compare", total=len(rows))
    active = []
    for row in rows:
        previous = store.document(row["document_id"]).get("raw", {})
        row["resources"] = previous.get("resources")
        if not store.suppressed(row["document_id"], "compare", row["version"]):
            active.append(row)
            store.attempt(row["document_id"], "compare", row["version"], run.id)
    if not active:
        return {}, {}
    baseline, incoming = [], []
    for row in active:
        old = row["baseline"]
        if Path(old.get("path", "")).is_file():
            baseline.append(
                descriptor(
                    book,
                    {**row, "metadata": old.get("metadata", row.get("metadata", {}))},
                    old["path"],
                    historical="ocr" in old,
                    evidence=old,
                )
            )
        incoming.append(
            descriptor(
                book,
                row,
                row["raw_path"],
                evidence=store.document(row["document_id"]).get("raw"),
            )
        )
    manifests, valid = [], True
    for name, documents in (("baseline", baseline), ("incoming", incoming)):
        source, target = (
            run.directory / f"{name}-descriptors.json",
            run.directory / f"{name}.json",
        )
        atomic_json(source, {"schema_version": "1.0", "documents": documents})
        code = call_tool(
            settings,
            run,
            "mdcompare",
            ["catalog", "--input", source, "--output", target],
        )
        valid = valid and code == 0 and target.is_file()
        manifests.append(target)
    if valid:
        try:
            catalog = read_json(manifests[1])
            resources = {
                str(r["document_id"]): r.get("resources", [])
                for r in catalog["documents"]
            }
            for row in active:
                row["resources"] = resources.get(row["document_id"], [])
                store.document(row["document_id"])["raw"]["resources"] = row[
                    "resources"
                ]
            store.save()
        except (ValueError, KeyError, TypeError):
            valid = False
    reports = []
    for mode in ("knowledge", "compare"):
        output = run.directory / f"{mode}-report.json"
        code = (
            call_tool(
                settings,
                run,
                "mdcompare",
                [
                    mode,
                    "--baseline",
                    manifests[0],
                    "--incoming",
                    manifests[1],
                    "--output",
                    output,
                ],
            )
            if valid
            else 1
        )
        try:
            report = read_json(output) if code in {0, 2} else {}
        except ValueError:
            report = {}
        if (
            not isinstance(report, dict)
            or report.get("schema_version")
            != ("knowledge-1.0" if mode == "knowledge" else "1.0")
            or not isinstance(report.get("results"), list)
        ):
            report = {}
        reports.append(report)
        run.data["artifacts"][mode] = str(output)
    for row in active:
        status = (
            "completed"
            if all(report.get("results") for report in reports)
            else "failed"
        )
        if status == "failed":
            failure(
                run,
                row,
                "compare",
                "COMPARISON_REPORT_UNAVAILABLE",
                exit_code=code,
                argv=run.data.get("tool", {}).get("argv"),
                reports=[
                    str(run.directory / f"{name}-report.json")
                    for name in ("knowledge", "compare")
                ],
                inputs=[str(path) for path in manifests],
            )
        store.complete(
            row["document_id"],
            "compare",
            row["version"],
            status,
            run.id,
            knowledge=run.data["artifacts"]["knowledge"],
            comparison=run.data["artifacts"]["compare"],
        )
    return reports


def ocr_inputs(settings, book, store, run, rows):
    run.stage("ocr", total=len(rows))
    results, stop = {}, None
    deadline = time.monotonic() + settings.ocr_budget_seconds
    for index, row in enumerate(rows):
        validate_inputs([row])
        identifier, version = row["document_id"], row["version"]
        previous = store.step(identifier, "ocr")
        if (
            previous
            and previous.get("status") in {"completed", "failed"}
            and previous.get("exit_code") in {0, 2}
            and same_version(previous["version"], version)
        ):
            try:
                request = read_json(Path(previous["input"]))
                old_input = next(
                    r["path"]
                    for r in request["documents"]
                    if str(r["document_id"]) == identifier
                )
                if Path(old_input).read_bytes() != Path(row["raw_path"]).read_bytes():
                    raise ValueError("OCR input changed")
                report = read_json(Path(previous["report"]))
                if report.get("schema_version") != "mdocr-batch-1.0":
                    raise ValueError("unreliable historical OCR report")
                if len(report["documents"]) != 1:
                    raise ValueError("historical OCR source mismatch")
                match = next(
                    r
                    for r in report["documents"]
                    if str(r["document_id"]) == identifier
                )
                if Path(match["output"]).is_file():
                    results[identifier] = match
                    if match.get("status") != "completed":
                        failure(
                            run,
                            row,
                            "ocr",
                            "OCR_PARTIAL_OR_UNKNOWN",
                            report=previous["report"],
                        )
                    continue
            except (OSError, ValueError, KeyError, TypeError, StopIteration):
                pass
        if store.suppressed(identifier, "ocr", version):
            failure(run, row, "ocr", "VERSION_ATTEMPT_SUPPRESSED")
            continue
        remaining = deadline - time.monotonic()
        if stop or remaining <= 0:
            results[identifier] = {"document_id": identifier, "status": "unattempted"}
            failure(
                run, row, "ocr", stop or "BATCH_DEADLINE_EXCEEDED", status="unattempted"
            )
            continue
        from dogpdkb.inputs import ocr_request_input

        tool_input, selected_resources, identities = ocr_request_input(row, run)
        request = run.directory / f"ocr-{index}-request.json"
        report = run.directory / f"ocr-{index}-report.json"
        atomic_json(
            request,
            {
                "schema_version": "mdocr-batch-input-1.0",
                "documents": [
                    {
                        "document_id": identifier,
                        "path": tool_input,
                        "resources": selected_resources,
                    }
                ],
            },
        )
        store.attempt(
            identifier, "ocr", version, run.id, input=str(request), report=str(report)
        )
        code = call_tool(
            settings,
            run,
            "mdocr",
            [
                "batch",
                "--manifest",
                request,
                "--output-dir",
                run.directory / "ocr" / str(index),
                "--report",
                report,
                "--cache-dir",
                settings.ocr_cache,
                "--download",
                "--max-seconds",
                remaining,
            ],
        )
        try:
            validate_inputs([row])
        except ValueError:
            store.complete(
                identifier,
                "ocr",
                version,
                "unknown",
                run.id,
                report=str(report),
                input=str(request),
                exit_code=code,
            )
            failure(run, row, "ocr", "PLAN_INPUT_CHANGED", report=str(report))
            raise
        result = {}
        try:
            payload = read_json(report)
            if payload.get("schema_version") != "mdocr-batch-1.0" or code not in {0, 2}:
                raise ValueError("unreliable OCR report")
            records = payload["documents"]
            if len(records) != 1 or str(records[0]["document_id"]) != identifier:
                raise ValueError("OCR source mismatch")
            result = records[0]
            for resource in [
                *result.get("resources", []),
                *result.get("ocr", {}).get("images", []),
            ]:
                key = resource.get("resource_id")
                if key in identities:
                    resource["resource_id"] = identities[key]
            if payload.get("causes"):
                stop = payload["causes"][0]
        except (ValueError, KeyError, TypeError):
            pass
        run.data["artifacts"]["ocr_report"] = str(report)
        run.save()
        if result:
            results[identifier] = result
        complete = (
            result.get("status") == "completed"
            and Path(result.get("output") or "").is_file()
        )
        store.complete(
            identifier,
            "ocr",
            version,
            "completed" if complete else "failed" if result else "unknown",
            run.id,
            report=str(report),
            input=str(request),
            exit_code=code,
        )
        if not complete:
            failure(
                run,
                row,
                "ocr",
                "OCR_PARTIAL_OR_UNKNOWN",
                report=str(report),
                exit_code=code,
            )
    return results


def publish_corpus(book, store, run, rows, results):
    run.stage("corpus", total=len(rows))
    published = []
    for index, row in enumerate(rows):
        identifier = row["document_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("source ID cannot be represented as a local filename")
        result = results.get(identifier, {})
        candidate = Path(result.get("output") or row["raw_path"])
        if not candidate.is_file():
            candidate = Path(row["raw_path"])
        previous = row["previous_corpus"]
        old_path = Path(previous.get("path", ""))
        output = (
            old_path
            if old_path.parent == book.corpus
            else book.corpus
            / f"source-{version_key(row['version'])[:10]}-[{identifier}].md"
        )
        if output.is_symlink():
            raise ValueError("refusing corpus symlink replacement")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{run.id}.tmp")
        try:
            shutil.copyfile(candidate, temporary)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        witness = store.input_directory(identifier) / "corpus.md"
        witness.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output, witness)
        resources = [dict(resource) for resource in (row.get("resources") or [])]
        evidence = result.get("ocr")
        if evidence:
            images = {r["resource_id"]: r for r in evidence.get("images", [])}
            for resource in resources:
                if resource.get("resource_id") in images:
                    resource["path"] = images[resource["resource_id"]]["source_path"]
        action = row["action"]
        before = {
            r["resource_id"]: r.get("artifact")
            for r in previous.get("ocr", {}).get("images", [])
        }
        if any(
            r.get("status") == "success"
            or r.get("status") == "reused"
            and before.get(r["resource_id"]) != r.get("artifact")
            for r in result.get("resources", [])
        ):
            action = "compile"
        metadata = descriptor(book, row, output)["metadata"]
        document = {
            "version": row["version"],
            "path": str(output),
            "metadata": metadata,
            "input_copy": str(witness),
            "resources": resources,
        }
        if evidence:
            document["ocr"] = evidence
        store.document(identifier)["corpus"] = document
        store.save()
        published.append(
            {
                **document,
                "document_id": identifier,
                "action": action,
                "previous_graph": row["previous_graph"],
                "baseline_version": row["baseline"].get("version"),
                "pending_metadata": row.get("metadata", {}),
                "pending_steps": ["ocr"]
                if result.get("status") == "unattempted"
                else [],
                "reason_codes": row["reason_codes"],
            }
        )
    manifest = {
        "schema": "dogpdkb-compile-1",
        "book": book.key,
        "run_id": run.id,
        "documents": published,
        "failures": list(run.data["failures"]),
    }
    path = run.directory / "manifest.json"
    atomic_json(path, manifest)
    run.data["artifacts"]["manifest"] = str(path)
    run.save()
    return path


def apply_run(
    settings, book, store, run, differences=None, *, stop_after=None, resume=False
):
    if resume and not run.data.get("artifacts", {}).get("plan"):
        frozen = run.data.get("artifacts", {}).get("differences")
        if not frozen:
            raise ValueError(
                "run has no frozen difference scope; start a new discovery"
            )
        differences = read_json(Path(frozen))
        resume = False
    if resume:
        plan = load_plan(run, book, store)
        existing = run.data.get("artifacts", {}).get("manifest")
        if existing:
            manifest = read_json(Path(existing))
            for document in manifest["documents"]:
                if (
                    Path(document["path"]).read_bytes()
                    != Path(document["input_copy"]).read_bytes()
                ):
                    raise ValueError(
                        "published plan input changed; generate a new plan"
                    )
            if not any(
                document.get("pending_steps") for document in manifest["documents"]
            ):
                return {
                    "exit_code": 2 if run.data["failures"] else 0,
                    "paused": False,
                    "manifest": existing,
                }
    else:
        if isinstance(differences, (str, Path)):
            differences = read_json(Path(differences))
        rows = prepare_inputs(settings, book, store, run, differences)
        knowledge, comparison = compare_inputs(settings, book, store, run, rows)
        run.stage("plan", total=len(rows))
        plan = make_plan(book, run, rows, knowledge, comparison)
    if stop_after == "plan":
        run.finish("paused")
        return {"exit_code": 0, "paused": True, "manifest": None}
    validate_plan(
        plan,
        book,
        store,
        check_baseline=not run.data.get("artifacts", {}).get("manifest"),
    )
    results = ocr_inputs(settings, book, store, run, plan["documents"])
    validate_plan(
        plan,
        book,
        store,
        check_baseline=not run.data.get("artifacts", {}).get("manifest"),
    )
    from dogpdkb.inputs import retain_inputs

    retain_inputs(store, run, plan["documents"], results)
    manifest = publish_corpus(book, store, run, plan["documents"], results)
    partial = bool(run.data["failures"])
    if run.data["command"] != "sync":
        run.finish("partial" if partial else "completed", 2 if partial else 0)
    return {
        "exit_code": 2 if partial else 0,
        "paused": False,
        "manifest": str(manifest),
    }


def record_interruption(store, run, code):
    for identifier, document in store.data["documents"].items():
        for step, record in list(document.get("steps", {}).items()):
            if record.get("run_id") == run.id and record.get("status") == "running":
                details = {
                    key: value
                    for key, value in record.items()
                    if key not in {"version", "status", "run_id", "updated_at"}
                }
                store.complete(
                    identifier, step, record["version"], "unknown", run.id, **details
                )
                run.failure(
                    document_id=identifier,
                    version=record["version"],
                    step=step,
                    reason="CALL_INTERRUPTED",
                    exit_code=code,
                    **details,
                )
    run.finish("interrupted", code)


def parser_for(command="apply"):
    parser = argparse.ArgumentParser(prog=f"dogpdkb {command}")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    selection = parser.add_mutually_exclusive_group(required=command == "apply")
    if command == "apply":
        selection.add_argument("--input", type=Path)
    selection.add_argument("--resume")
    parser.add_argument("--stop-after", choices=["plan"])
    return parser


def main(argv=None):
    parser = parser_for()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return 64 if error.code == 2 else error.code
    run = None
    try:
        settings = load_config(args.config)
        book = select_book(settings, args.book)
        store = Store(settings, book)
        with store.lock():
            run = (
                Run.load(store, args.resume)
                if args.resume
                else Run.create(store, "apply")
            )
            run.data.update(
                status="running",
                pid=os.getpid(),
                command="apply",
                ended_at=None,
                exit_code=None,
                reason=None,
            )
            try:
                result = apply_run(
                    settings,
                    book,
                    store,
                    run,
                    args.input,
                    stop_after=args.stop_after,
                    resume=bool(args.resume),
                )
            except InterruptedError as error:
                record_interruption(store, run, error.args[0])
                return error.args[0]
            if not result["paused"]:
                run.finish(
                    "partial" if result["exit_code"] == 2 else "completed",
                    result["exit_code"],
                )
            print(json.dumps({**result, "run": str(run.path)}, ensure_ascii=False))
            return result["exit_code"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        if run:
            run.finish("failed", 1, str(error))
        print(f"dogpdkb apply: {error}", file=sys.stderr)
        return 1
