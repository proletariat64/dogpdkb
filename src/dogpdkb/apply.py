"""Serial download, public comparison, fixed plan, OCR and corpus publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from dogpdkb.directory_snapshot import catalog_paths, copy_snapshot
from dogpdkb.help import HelpParser
from dogpdkb.ocr_batch import ocr_inputs
from dogpdkb.planning import (
    descriptor,
    load_plan,
    make_plan,
    reject_rollback,
    same_version,
    validate_inputs,
    validate_plan,
)
from dogpdkb.processes import diagnostic_argv, run_command, run_commands
from dogpdkb.records import Run, Store, atomic_json, read_json, version_key
from dogpdkb.semantic import (
    bind_score_artifacts,
    freeze_recovery,
    read_score_route,
    read_semantic_artifacts,
)
from dogpdkb.settings import (
    DEFAULT_DOWNLOAD_CONCURRENCY,
    document_identity,
    load_config,
    select_book,
)


def call_tool(settings, run, name, arguments, *, timeout=None, command=None):
    argv = (settings.command(name) if command is None else command) + [
        str(v) for v in arguments
    ]
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


def failure(run, row, step, reason, *, persist=True, **details):
    tool = run.data.get("tool", {})
    expected = {"export": "skylark", "ocr": "mdocr", "compare": "mdcompare"}
    if "exit_code" in details and tool.get("name") == expected.get(step):
        details.setdefault("argv", tool.get("argv"))
    run.failure(
        persist=persist,
        document_id=row["document_id"],
        version=row.get("version"),
        step=step,
        reason=reason,
        input=row.get("raw_path"),
        **details,
    )


def corpus_destination(book, identity, previous):
    old_path = Path(previous.get("path", ""))
    return (
        old_path
        if old_path.parent == book.corpus
        else book.corpus / f"source-[{identity}].md"
    )


def prepare_inputs(
    settings, book, store, run, differences, *, concurrency=DEFAULT_DOWNLOAD_CONCURRENCY
):
    if (
        differences.get("schema") != "dogpdkb-differences-2"
        or differences.get("book", {}).get("key") != book.key
    ):
        raise ValueError("difference schema or book mismatch")
    source_keys = differences.get("sources")
    if (
        not isinstance(source_keys, list)
        or not source_keys
        or any(not isinstance(key, str) for key in source_keys)
    ):
        raise ValueError("difference requires selected sources")
    if run.data["command"] == "sync":
        bindings = run.data["targets"]["sources"]
        if "sources" in run.data and source_keys != run.data["sources"]:
            raise ValueError("difference source selection mismatch")
    else:
        book.select_sources(source_keys)
        bindings = book.source_bindings()
    old_bindings = differences["book"].get("sources", {})
    if len(source_keys) != len(set(source_keys)) or any(
        key not in bindings or old_bindings.get(key) != bindings[key]
        for key in source_keys
    ):
        raise ValueError("difference source binding mismatch")
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
    for row in selected:
        key = row.get("source_key")
        source = bindings[key] if key in source_keys else None
        source_id = row.get("source_document_id")
        if (
            source is None
            or not isinstance(source_id, str)
            or not source_id
            or row["document_id"] != document_identity(key, source_id)
        ):
            raise ValueError("invalid difference document identity")
        scope = source.get("document_ids")
        if scope is not None and source_id not in scope:
            raise ValueError("difference source IDs outside subscription")
    run.data["sources"] = source_keys
    atomic_json(run.directory / "differences.json", differences)
    run.data["artifacts"]["differences"] = str(run.directory / "differences.json")
    prepared_path = run.directory / "prepared-inputs.json"
    prepared = read_json(prepared_path) if prepared_path.exists() else {}
    validate_inputs(prepared.values(), check_baseline=True)

    def save_prepared():
        atomic_json(prepared_path, prepared)
        run.data["artifacts"]["prepared_inputs"] = str(prepared_path)
        run.save()

    def freeze_input(row, *, persist=True):
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
            if persist:
                save_prepared()

    owners = {}
    for identity, document in store.data["documents"].items():
        if path := document.get("corpus", {}).get("path"):
            owners.setdefault(Path(path), set()).add(identity)
    for row in selected:
        identity = row["document_id"]
        previous = store.data["documents"].get(identity, {}).get("corpus", {})
        output = corpus_destination(book, identity, previous)
        if (output.exists() or output.is_symlink()) and output not in owners:
            owners[output] = {None}
        owners.setdefault(output, set()).add(identity)
    frozen = None
    if any(
        bindings[row["source_key"]]["type"] == "directory"
        and row["document_id"] not in prepared
        for row in selected
    ):
        discovery_id = differences.get("run_id")
        if not isinstance(discovery_id, str) or not re.fullmatch(
            r"run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}", discovery_id
        ):
            raise ValueError("directory differences require a saved discovery run")
        frozen = Run.load(store, discovery_id)
    rows, requests = [], []
    local_prepared = False
    for index, selected_row in enumerate(selected):
        row = dict(selected_row)
        row["slot"] = index
        identifier = row["document_id"]
        document = store.document(identifier)
        output = corpus_destination(book, identifier, document.get("corpus", {}))
        if owners[output] != {identifier} or output.is_symlink():
            if (
                frozen is not None
                and bindings[row["source_key"]]["type"] == "directory"
            ):
                row["raw_path"] = str(
                    frozen.directory
                    / "directory-inputs"
                    / identifier
                    / row["source_document_id"]
                )
            failure(run, row, "plan", "CORPUS_DESTINATION_CONFLICT", path=str(output))
            continue
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
        document.update(
            source_key=row["source_key"], source_document_id=row["source_document_id"]
        )
        previous = document.get("raw", {})
        graph_step = store.step(identifier, "graph") or {}
        graph_witness = graph_step.get("input_copy")
        if graph_step.get("status") == "completed" and graph_witness:
            baseline = {
                **document.get("graph_baseline", {}),
                "path": graph_witness,
                "version": graph_step["version"],
                "metadata": graph_step.get("metadata", {}),
            }
        elif document.get("graph_baseline"):
            baseline = document["graph_baseline"]
        elif document.get("baseline"):
            # Only explicit init-state facts may bootstrap an unknown graph.
            # Capturing/publishing an uncompiled new body is NOT a baseline.
            baseline = document.get("corpus", {})
        else:
            baseline = {}
        row["baseline"] = dict(baseline)
        row["previous_corpus"] = dict(document.get("corpus", {}))
        row["previous_graph"] = dict(document.get("graph", {}))
        existing = Path(previous.get("path", ""))
        desired_metadata = descriptor(book, row, existing)["metadata"]
        if bindings[row["source_key"]]["type"] == "directory":
            assert frozen is not None
            frozen_root = frozen.directory / "directory-inputs" / identifier
            if str(frozen_root / row["source_document_id"]) != row.get("frozen_input"):
                raise ValueError(f"invalid frozen directory input: {identifier}")
            snapshot = run.directory / "raw" / "directory" / identifier
            raw = copy_snapshot(frozen_root, snapshot, row)
            row.update(raw_path=str(raw), directory_snapshot=str(snapshot))
            document["raw"] = {
                "version": row["version"],
                "path": str(raw),
                "metadata": desired_metadata,
                "resources": catalog_paths(snapshot, row),
            }
            freeze_input(row, persist=False)
            local_prepared = True
        elif (
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
    if local_prepared:
        store.save()
        save_prepared()
    if requests:
        run.stage("download", total=len(requests))
        by_id = {row["document_id"]: row for row in rows}
        kept = {row["document_id"] for row in rows} - {
            item["document_id"] for item in requests
        }

        def commands():
            for item in requests:
                row = by_id[item["document_id"]]
                request = run.directory / f"export-{row['slot']}-request.json"
                report = run.directory / f"export-{row['slot']}-report.json"
                atomic_json(
                    request,
                    {
                        "schema": "skylark-export-request-1",
                        "documents": [
                            {**item, "document_id": row["source_document_id"]}
                        ],
                    },
                )
                store.attempt(
                    row["document_id"],
                    "export",
                    row["version"],
                    run.id,
                    input=str(request),
                    report=str(report),
                )
                argv = settings.command("skylark") + [
                    "export",
                    "--request",
                    str(request),
                    "--output",
                    str(report),
                ]
                run.data["tool"] = {"name": "skylark", "argv": diagnostic_argv(argv)}
                run.save()
                yield argv

        unavailable = []
        try:
            with closing(
                run_commands(
                    commands(),
                    concurrency=concurrency,
                    log=run.directory / "skylark.log",
                    env=settings.skylark_environment(),
                )
            ) as outcomes:
                for index, code in outcomes:
                    row = by_id[requests[index]["document_id"]]
                    report = run.directory / f"export-{row['slot']}-report.json"
                    argv = settings.command("skylark") + [
                        "export",
                        "--request",
                        str(run.directory / f"export-{row['slot']}-request.json"),
                        "--output",
                        str(report),
                    ]
                    run.data["tool"] = {
                        "name": "skylark",
                        "argv": diagnostic_argv(argv),
                    }
                    if code in {130, 143}:
                        raise InterruptedError(code)
                    result = {}
                    try:
                        payload = read_json(report)
                        if payload.get(
                            "schema"
                        ) != "skylark-export-result-1" or code not in {
                            0,
                            2,
                        }:
                            raise ValueError("unreliable export report")
                        documents = payload["documents"]
                        if (
                            len(documents) != 1
                            or str(documents[0]["document_id"])
                            != row["source_document_id"]
                        ):
                            raise ValueError("export source mismatch")
                        result = documents[0]
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
                    valid = (
                        result.get("status") == "completed"
                        and Path(row["raw_path"]).is_file()
                    )
                    try:
                        valid = valid and same_version(
                            result.get("version"), row["version"]
                        )
                    except ValueError:
                        valid = False
                    status = "completed" if valid else "failed" if result else "unknown"
                    if valid:
                        store.document(row["document_id"])["raw"] = {
                            "version": row["version"],
                            "path": row["raw_path"],
                            "metadata": descriptor(book, row, row["raw_path"])[
                                "metadata"
                            ],
                        }
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
                        unavailable.append((code, report))
        except InterruptedError:
            raise
        except Exception:
            record_interruption(
                store, run, 1, reason="EXPORT_BATCH_ABORTED", finish=False
            )
            raise
        if unavailable:
            code, report = unavailable[0]
            raise ValueError(f"Skylark export unavailable ({code}); see {report}")
        catalog_failed_exports(settings, book, run, by_id)
        rows = [row for row in rows if row["document_id"] in kept]
    return rows


def catalog_failed_exports(settings, book, run, rows):
    failures = [
        item
        for item in run.data["failures"]
        if item["step"] == "export" and Path(item.get("input") or "").is_file()
    ]
    if not failures:
        return
    source = run.directory / "failed-export-descriptors.json"
    report = run.directory / "failed-export-resources.json"
    try:
        atomic_json(
            source,
            {
                "schema_version": "1.0",
                "documents": [
                    descriptor(book, rows[item["document_id"]], item["input"])
                    for item in failures
                ],
            },
        )
        code = call_tool(
            settings,
            run,
            "mdcompare",
            ["catalog", "--input", source, "--output", report],
        )
        if code:
            raise ValueError("failed export resource catalog unavailable")
        payload = read_json(report)
        if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
            raise ValueError("invalid failed export resource catalog")
        documents = {item["document_id"]: item for item in payload["documents"]}
        for item in failures:
            document = documents[item["document_id"]]
            if document.get("resource_warnings"):
                raise ValueError("failed export resource catalog is uncertain")
            resources = document.get("resources", [])
            if not isinstance(resources, list) or not all(
                isinstance(resource, dict) for resource in resources
            ):
                raise ValueError("invalid failed export resource entries")
            item["resources"] = resources
            item.setdefault("reports", []).append(str(report))
    except InterruptedError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        for item in failures:
            item["resource_catalog_error"] = str(error)
    run.save()


def compare_inputs(settings, book, store, run, rows, *, workspace=None, replan=False):
    try:
        return _compare_inputs(
            settings, book, store, run, rows, workspace=workspace, replan=replan
        )
    except InterruptedError:
        raise
    except (OSError, ValueError):
        # A launched batch with unusable public evidence has unknown outcomes,
        # not permanently running work and not permission to retry silently.
        record_interruption(
            store, run, 1, reason="SEMANTIC_COMPARISON_UNAVAILABLE", finish=False
        )
        raise


def _compare_inputs(settings, book, store, run, rows, *, workspace=None, replan=False):
    workspace = workspace or run.directory
    run.stage("compare", total=len(rows), phase="prepare")
    active = []
    for row in rows:
        previous = store.document(row["document_id"]).get("raw", {})
        row["resources"] = previous.get("resources")
        if replan or not store.suppressed(
            row["document_id"], "compare", row["version"]
        ):
            active.append(row)
    if not active:
        return (
            {},
            {},
            {"schema_version": "semantic-1.0", "scope": "authored_body", "results": []},
        )
    baseline, incoming = [], []
    for row in active:
        old = row["baseline"]
        if old.get("path"):
            baseline.append(
                descriptor(
                    book,
                    {
                        **row,
                        "version": old.get("version", row.get("version")),
                        "metadata": old.get("metadata", row.get("metadata", {})),
                    },
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
    manifests, sources = [], []
    for name, documents in (("baseline", baseline), ("incoming", incoming)):
        source, target = (
            workspace / f"{name}-descriptors.json",
            workspace / f"{name}.json",
        )
        atomic_json(source, {"schema_version": "1.0", "documents": documents})
        sources.append(source)
        manifests.append(target)
    store.complete_batch(
        {
            "doc_id": row["document_id"],
            "name": "compare",
            "version": row["version"],
            "status": "running",
            "run_id": run.id,
        }
        for row in active
    )
    run.stage("compare", total=len(active), phase="tools")
    valid = True
    for source, target in zip(sources, manifests):
        code = call_tool(
            settings,
            run,
            "mdcompare",
            ["catalog", "--input", source, "--output", target],
        )
        valid = valid and code == 0 and target.is_file()
    if valid:
        try:
            catalog = read_json(manifests[1])
            resources = {
                str(r["document_id"]): r.get("resources", [])
                for r in catalog["documents"]
            }
            for row in active:
                row["resources"] = resources.get(row["document_id"], [])
                if not replan:
                    store.document(row["document_id"])["raw"]["resources"] = row[
                        "resources"
                    ]
            store.save()
        except (ValueError, KeyError, TypeError):
            valid = False
    semantic_root = store.path.parent / "semantic" / run.id
    semantic_root.mkdir(parents=True, exist_ok=True)
    recovery_path = freeze_recovery(settings.body_recovery, semantic_root / "recovery")
    semantic_dir = Path(tempfile.mkdtemp(prefix="comparison-", dir=semantic_root))
    semantic_report_path = semantic_dir / "semantic-report.json"
    semantic_diff_path = semantic_dir / "semantic-diff.json"
    semantic_args = [
        "semantic",
        "--baseline",
        sources[0],
        "--incoming",
        sources[1],
        "--output",
        semantic_report_path,
        "--diff-output",
        semantic_diff_path,
    ]
    if recovery_path is not None:
        semantic_args += ["--recovery", recovery_path]
        run.data["artifacts"]["body_recovery"] = str(recovery_path)
    semantic_code = call_tool(settings, run, "mdcompare", semantic_args)
    semantic = read_semantic_artifacts(
        semantic_code, semantic_report_path, semantic_diff_path, active, book.key
    )
    run.data["artifacts"]["semantic"] = str(semantic_report_path)
    run.data["artifacts"]["semantic_diff"] = str(semantic_diff_path)
    run.data["artifacts"].pop("semantic_scoring", None)
    if (
        settings.semantic_score_request_budget
        and not replan
        and not run.data.get("semantic_score_started")
    ):
        score_path = semantic_dir / "scores.json"
        route_path = semantic_dir / "score-route.json"
        run.data["semantic_score_started"] = str(score_path)
        run.save(durable=True)
        common = ["--report", semantic_report_path, "--diff-index", semantic_diff_path]
        score_args = [
            "semantic-score",
            *common,
            "--output",
            score_path,
            "--request-budget",
            str(settings.semantic_score_request_budget),
        ]
        if settings.semantic_score_model:
            score_args += ["--model", settings.semantic_score_model]
        score_code = call_tool(settings, run, "mdcompare", score_args)
        if score_code not in {0, 2} or not score_path.is_file():
            raise ValueError("semantic score execution did not publish its result")
        route_code = call_tool(
            settings,
            run,
            "mdcompare",
            ["semantic-route", *common, "--scores", score_path, "--output", route_path],
        )
        semantic = read_score_route(route_code, route_path, semantic)
        run.data["artifacts"]["semantic_scoring"] = bind_score_artifacts(
            {
                "semantic": semantic_report_path,
                "semantic_diff": semantic_diff_path,
                "scores": score_path,
                "route": route_path,
            }
        )
    reports = []
    for mode in ("knowledge", "compare"):
        output = workspace / f"{mode}-report.json"
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
    run.stage("compare", total=len(active), phase="record-results")
    outcomes = []
    semantic_by_id = {item["document_id"]: item for item in semantic["results"]}
    for row in active:
        identifier = row["document_id"]
        semantic_item = semantic_by_id.get(identifier)
        status = "completed" if semantic_item is not None else "failed"
        if (
            semantic_item is not None
            and semantic_item["semantic_status"] == "UNAVAILABLE"
        ):
            status = "unknown"
        if status == "failed":
            failure(
                run,
                row,
                "compare",
                "COMPARISON_REPORT_UNAVAILABLE",
                persist=False,
                exit_code=code,
                argv=run.data.get("tool", {}).get("argv"),
                reports=[
                    str(workspace / f"{name}-report.json")
                    for name in ("knowledge", "compare")
                ],
                inputs=[str(path) for path in manifests],
            )
        outcomes.append(
            {
                "doc_id": row["document_id"],
                "name": "compare",
                "version": row["version"],
                "status": status,
                "run_id": run.id,
                "knowledge": run.data["artifacts"]["knowledge"],
                "comparison": run.data["artifacts"]["compare"],
                "semantic": str(semantic_report_path),
                "semantic_diff": str(semantic_diff_path),
            }
        )
    store.complete_batch(outcomes)
    run.save()
    return (*reports, semantic)


def prepare_publication(book, store, run, rows, results):
    """Freeze the complete publication handoff before replacing any corpus file."""
    published = []
    baseline_saved = False
    for index, row in enumerate(rows):
        identifier = row["document_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("source ID cannot be represented as a local filename")
        result = results.get(identifier, {})
        candidate = Path(result.get("output") or row["raw_path"])
        if not candidate.is_file():
            candidate = Path(row["raw_path"])
        previous = row["previous_corpus"]
        output = corpus_destination(book, identifier, previous)
        owner = store.document(identifier).get("corpus", {}).get("path")
        if output.exists() and owner != str(output):
            raise ValueError(f"corpus destination changed or unowned: {output}")
        if output.is_symlink():
            raise ValueError("refusing corpus symlink replacement")
        inputs = store.input_directory(identifier)
        inputs.mkdir(parents=True, exist_ok=True)
        document = store.document(identifier)
        old_basis = row.get("baseline_input_copy")
        if old_basis and not document.get("graph_baseline"):
            basis = inputs / "graph-baseline.md"
            shutil.copyfile(old_basis, basis)
            source = row["previous_graph"].get("source_file")
            old_source = Path(row["previous_corpus"].get("path", ""))
            if source is None and old_source.is_relative_to(book.corpus):
                source = str(old_source.relative_to(book.corpus))
            document["graph_baseline"] = {
                **row["baseline"],
                "path": str(basis),
                "source_file": source,
            }
            baseline_saved = True
        witness = Path(tempfile.mkdtemp(prefix="corpus-", dir=inputs)) / "corpus.md"
        shutil.copyfile(candidate, witness)
        before_copy = None
        if output.is_file():
            before = run.directory / "publication-before" / f"{index}.md"
            before.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output, before)
            before_copy = str(before)
        resources = [dict(resource) for resource in (row.get("resources") or [])]
        evidence = result.get("ocr")
        if evidence:
            images = {r["resource_id"]: r for r in evidence.get("images", [])}
            for resource in resources:
                if resource.get("resource_id") in images:
                    resource["path"] = images[resource["resource_id"]]["source_path"]
        action = row["action"]
        reasons = row["reason_codes"]
        if row.get("retry_ocr") and evidence and evidence["images"]:
            action = "compile"
            reasons = [*reasons, "OCR_ENRICHMENT_ADDED"]
        metadata = descriptor(book, row, output)["metadata"]
        document = {
            "version": row["version"],
            "path": str(output),
            "metadata": metadata,
            "input_copy": str(witness),
            "input_copy_sha256": hashlib.sha256(witness.read_bytes()).hexdigest(),
            "resources": resources,
            "ocr_status": result.get("status", "not_selected"),
        }
        if evidence:
            document["ocr"] = evidence
        published.append(
            {
                **document,
                "before_copy": before_copy,
                "document_id": identifier,
                "action": action,
                "previous_graph": row["previous_graph"],
                "baseline_version": row["baseline"].get("version"),
                "pending_metadata": row.get("metadata", {}),
                "pending_steps": [],  # Image/OCR enrichment is advisory.
                "reason_codes": reasons,
            }
        )
    if baseline_saved:
        store.save()
    publication = {
        "schema": "dogpdkb-publication-1",
        "book": book.key,
        "run_id": run.id,
        "documents": published,
        "failures": list(run.data["failures"]),
    }
    path = run.directory / "publication.json"
    atomic_json(path, publication)
    run.data["artifacts"]["publication"] = str(path)
    run.save()
    return publication


def validate_publication_target(document):
    output = Path(document["path"])
    if output.is_symlink():
        raise ValueError("refusing corpus symlink replacement")
    expected = Path(document["input_copy"]).read_bytes()
    if document.get("input_copy_sha256") not in (
        None, hashlib.sha256(expected).hexdigest()
    ):
        raise ValueError("publication witness changed; generate a new plan")
    if output.is_file():
        current = output.read_bytes()
        before = document.get("before_copy")
        if current == expected or before and current == Path(before).read_bytes():
            return
    elif not output.exists() and not document.get("before_copy"):
        return
    raise ValueError("publication target changed; generate a new plan")


def acknowledge_comparison(record, document):
    """A handled body version does not need daily rediscovery or fake graph success."""
    pending = record.get("pending")
    if (
        document["action"] in {"retain", "reuse", "metadata"}
        and not document.get("pending_steps")
        and pending
        and same_version(pending["version"], document["version"])
        and pending.get("metadata")
        == document.get("pending_metadata", document["metadata"])
    ):
        record.pop("pending", None)


def publish_corpus(book, store, run, plan, publication):
    """Resume only frozen local publication work, including unrecorded replacements."""
    rows, documents = plan["documents"], publication["documents"]
    if (
        publication.get("schema") != "dogpdkb-publication-1"
        or publication.get("book") != book.key
        or publication.get("run_id") != run.id
        or [(r["document_id"], r["version"]) for r in rows]
        != [(d["document_id"], d["version"]) for d in documents]
    ):
        raise ValueError("publication handoff does not match saved plan")
    validate_plan(plan, book, store)
    for row, document in zip(rows, documents):
        expected = corpus_destination(book, row["document_id"], row["previous_corpus"])
        if Path(document["path"]) != expected:
            raise ValueError("publication target does not match saved plan")
        # A raw baseline remains immutable; a corpus baseline may be our own write.
        baseline = row.get("baseline_input_copy")
        if baseline and Path(row["baseline"]["path"]) != expected:
            validate_inputs([row], check_baseline=True)
        validate_publication_target(document)
    run.stage("corpus", total=len(documents))
    dirty = False
    try:
        for document in documents:
            validate_publication_target(document)
            output, witness = Path(document["path"]), Path(document["input_copy"])
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.is_file() or output.read_bytes() != witness.read_bytes():
                descriptor_fd, name = tempfile.mkstemp(
                    prefix=".dogpdkb-", suffix=".tmp", dir=output.parent
                )
                temporary = Path(name)
                try:
                    with (
                        os.fdopen(descriptor_fd, "wb") as stream,
                        witness.open("rb") as source,
                    ):
                        shutil.copyfileobj(source, stream)
                    shutil.copymode(witness, temporary)
                    temporary.replace(output)
                finally:
                    temporary.unlink(missing_ok=True)
            store.document(document["document_id"])["corpus"] = {
                key: document[key]
                for key in (
                    "version",
                    "path",
                    "metadata",
                    "input_copy",
                    "input_copy_sha256",
                    "ocr_status",
                    "resources",
                    "ocr",
                )
                if key in document
            }
            acknowledge_comparison(store.document(document["document_id"]), document)
            dirty = True
    finally:
        if dirty:
            store.save()
    manifest = {
        **publication,
        "schema": "dogpdkb-compile-1",
        "documents": [
            {key: value for key, value in d.items() if key != "before_copy"}
            for d in documents
        ],
    }
    path = run.directory / "manifest.json"
    atomic_json(path, manifest)
    run.data["artifacts"]["manifest"] = str(path)
    run.save()
    return path


def apply_run(
    settings,
    book,
    store,
    run,
    differences=None,
    *,
    stop_after=None,
    resume=False,
    concurrency=DEFAULT_DOWNLOAD_CONCURRENCY,
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
            return {
                "exit_code": 2 if run.data["failures"] else 0,
                "paused": False,
                "manifest": existing,
            }
    else:
        if isinstance(differences, (str, Path)):
            differences = read_json(Path(differences))
        rows = prepare_inputs(
            settings, book, store, run, differences, concurrency=concurrency
        )
        knowledge, comparison, semantic = compare_inputs(
            settings, book, store, run, rows
        )
        run.stage("plan", total=len(rows))
        plan = make_plan(book, run, rows, knowledge, comparison, semantic)
    if stop_after == "plan":
        run.finish("paused")
        return {"exit_code": 0, "paused": True, "manifest": None}
    checkpoint = run.data.get("artifacts", {}).get("publication")
    if checkpoint:
        publication = read_json(Path(checkpoint))
    else:
        validate_plan(plan, book, store, check_baseline=True)
        results = ocr_inputs(settings, book, store, run, plan["documents"])
        validate_plan(plan, book, store, check_baseline=True)
        from dogpdkb.inputs import retain_inputs

        retain_inputs(store, run, plan["documents"], results)
        publication = prepare_publication(book, store, run, plan["documents"], results)
    manifest = publish_corpus(book, store, run, plan, publication)
    partial = bool(run.data["failures"])
    if run.data["command"] != "sync":
        run.finish("partial" if partial else "completed", 2 if partial else 0)
    return {
        "exit_code": 2 if partial else 0,
        "paused": False,
        "manifest": str(manifest),
    }


def replan_published(settings, book, store, run):
    """Explicitly reselect a published batch using its original frozen bodies."""
    from copy import deepcopy

    from dogpdkb.graph import _manifest

    original = _manifest(settings, book, store, run, run.data["artifacts"]["manifest"])
    plan = load_plan(run, book, store)
    if plan.get("run_id") != run.id or not Path(
        run.data["artifacts"]["plan"]
    ).resolve().is_relative_to(run.directory.resolve()):
        raise ValueError("original comparison plan ownership mismatch")
    if [(r["document_id"], r["version"]) for r in plan["documents"]] != [
        (r["document_id"], r["version"]) for r in original["documents"]
    ]:
        raise ValueError("saved comparison scope differs from published batch")
    rows = deepcopy(plan["documents"])
    for row in rows:
        # Never compare the current corpus against itself after publication.
        witness = Path(row["input_copy"])
        if not witness.is_file() or not witness.resolve().is_relative_to(
            run.directory.resolve()
        ):
            raise ValueError(
                "original comparison input unavailable; generate a new plan"
            )
        row["raw_path"] = str(witness)
        baseline = row.get("baseline", {})
        if baseline.get("path"):
            old = row.get("baseline_input_copy")
            if (
                not old
                or not Path(old).is_file()
                or not Path(old).resolve().is_relative_to(run.directory.resolve())
            ):
                raise ValueError(
                    "original baseline snapshot unavailable; generate a new plan"
                )
            row["baseline"] = {**baseline, "path": old}
    workspace = Path(tempfile.mkdtemp(prefix="replan-", dir=run.directory))
    previous_artifacts = dict(run.data["artifacts"])
    frozen_inputs = []
    for index, row in enumerate(rows):
        source = Path(row["input_copy"])
        target = workspace / f"incoming-{index}.md"
        shutil.copyfile(source, target)
        frozen_inputs.append((source, target))
        row.update(raw_path=str(target), input_copy=str(target))
        if row.get("baseline_input_copy"):
            source = Path(row["baseline_input_copy"])
            target = workspace / f"baseline-{index}.md"
            shutil.copyfile(source, target)
            frozen_inputs.append((source, target))
            row["baseline_input_copy"] = str(target)
            row["baseline"] = {**row["baseline"], "path": str(target)}
    knowledge, comparison, semantic = compare_inputs(
        settings, book, store, run, rows, workspace=workspace, replan=True
    )
    if any(
        source.read_bytes() != target.read_bytes() for source, target in frozen_inputs
    ):
        raise ValueError("original comparison snapshots changed during replan")
    _manifest(settings, book, store, run, original)
    refreshed = make_plan(
        book, run, rows, knowledge, comparison, semantic, workspace=workspace
    )
    # A body comparison changes selection, not the published corpus/OCR payload.
    decisions = {r["document_id"]: r for r in refreshed["documents"]}
    manifest = deepcopy(original)
    for row in manifest["documents"]:
        decision = decisions[row["document_id"]]
        row.update(action=decision["action"], reason_codes=decision["reason_codes"])
        acknowledge_comparison(store.document(row["document_id"]), row)
        old = decision.get("baseline_input_copy")
        document = store.document(row["document_id"])
        if old and not document.get("graph_baseline"):
            target = store.input_directory(row["document_id"]) / "graph-baseline.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old, target)
            source = decision["previous_graph"].get("source_file")
            original_path = Path(decision["previous_corpus"].get("path", ""))
            if source is None and original_path.is_relative_to(book.corpus):
                source = str(original_path.relative_to(book.corpus))
            document["graph_baseline"] = {
                **decision["baseline"],
                "path": str(target),
                "source_file": source,
            }
    # Retain historical failures as evidence; recovery does not clear a graph block.
    manifest["failures"] = list(run.data["failures"])
    target = workspace / "manifest.json"
    atomic_json(target, manifest)
    run.data.setdefault("replans", []).append(
        {
            "workspace": str(workspace),
            "previous_artifacts": previous_artifacts,
            "manifest": str(target),
        }
    )
    run.data["artifacts"]["manifest"] = str(target)
    store.save()
    run.save()
    return target


def record_interruption(store, run, code, *, reason="CALL_INTERRUPTED", finish=True):
    outcomes = []
    for identifier, document in store.data["documents"].items():
        for step, record in list(document.get("steps", {}).items()):
            if record.get("run_id") == run.id and record.get("status") == "running":
                details = {
                    key: value
                    for key, value in record.items()
                    if key not in {"version", "status", "run_id", "updated_at"}
                }
                outcomes.append(
                    {
                        "doc_id": identifier,
                        "name": step,
                        "version": record["version"],
                        "status": "unknown",
                        "run_id": run.id,
                        **details,
                    }
                )
                run.data["failures"].append(
                    {
                        "document_id": identifier,
                        "version": record["version"],
                        "step": step,
                        "reason": reason,
                        "exit_code": code,
                        **details,
                    }
                )
    store.complete_batch(outcomes)
    if finish:
        run.finish("interrupted", code)


def parser_for(command="apply"):
    parser = HelpParser(prog=f"dogpdkb {command}")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--book")
    parser.add_argument(
        "--concurrency",
        type=int,
        choices=[1, 2, 3],
        default=DEFAULT_DOWNLOAD_CONCURRENCY,
    )
    selection = parser.add_mutually_exclusive_group(required=command == "apply")
    if command == "apply":
        selection.add_argument("--input", type=Path)
    selection.add_argument("--resume")
    parser.add_argument("--stop-after", choices=["plan"])
    if command == "sync":
        inputs = parser.add_mutually_exclusive_group()
        inputs.add_argument("--source", action="append", metavar="KEY")
        inputs.add_argument("--dir", type=Path, metavar="PATH")
        parser.add_argument(
            "--graphify-inputs", choices=["all", "text"], default="text"
        )
        parser.add_argument("--retry-graph", action="store_true")
        parser.add_argument("--replan", action="store_true")
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
                    concurrency=args.concurrency,
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
