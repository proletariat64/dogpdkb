"""Orchestrate one public mdocr execution; keep its originals outside wash."""

import hashlib
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dogpdkb.inputs import _local_destination, relocate_targets
from dogpdkb.planning import same_version, validate_inputs
from dogpdkb.records import atomic_json, read_json

# Public mdocr artifact schemas; never import the tool implementation.
_IMAGE_SCHEMA = "mdocr-image-content-1"
_LEGACY_SCHEMA = "mdocr-image-patches-1"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    return digest(Path(path).read_bytes())


def _path(root, value):
    path = Path(value)
    if not path.is_relative_to(root) or path.resolve() != path:
        raise ValueError("OCR path outside its durable directory or through a symlink")
    return path


def _targets(resource):
    key = resource["resource_id"]
    url = urlsplit(key)
    return {key, key.partition("#")[0], unquote(url.path)}


def _prepare(root, row):
    """Stage exact raw/image bytes and a separate, path-safe CLI input."""
    name = f"{row['slot']}.md"
    raw = _path(root, root / "raw" / name)
    source = _path(root, root / "input" / name)
    output = _path(root, root / "output" / name)
    body = Path(row["raw_path"]).read_bytes()
    text = body.decode("utf-8")
    mappings, assets = {}, {}
    for index, item in enumerate(row.get("resources") or []):
        if not item.get("path"):
            continue
        suffix = Path(item["path"]).suffix
        if not re.fullmatch(r"\.[A-Za-z0-9]+", suffix):
            suffix = ""
        relative = f"assets/{row['slot']}/{index}{suffix}"
        target = _path(root, source.parent / relative)
        data = Path(item["path"]).read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        for key in _targets(item):
            previous = mappings.setdefault(key, relative)
            if previous != relative and assets[previous]["sha256"] != digest(data):
                raise ValueError(f"ambiguous local OCR resource: {key}")
        assets[relative] = {
            "resource_id": item["resource_id"],
            "path": str(target),
            "sha256": digest(data),
        }
    replacements = {
        key: _local_destination(relative, key) for key, relative in mappings.items()
    }
    prepared = relocate_targets(text, replacements).encode("utf-8")
    for path, data in [(raw, body), (source, prepared)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    selection = row.get("ocr_resources")
    if selection is not None:
        selection = [mappings.get(key, key) for key in selection]
    return {
        "document_id": row["document_id"],
        "source_key": row["source_key"],
        "version": row["version"],
        "path": str(source),
        "output": str(output),
        "sha256": digest(prepared),
        "raw_copy": str(raw),
        "source_sha256": digest(body),
        "assets": assets,
        "selection": selection,
        "download": "directory_snapshot" not in row,
    }


def _index(rows, field="document_id"):
    indexed = {row[field]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("duplicate OCR identity")
    return indexed


def _load_plan(root, request):
    plan = read_json(root / "plan.json")
    docs = _index(plan["documents"])
    expected = _index(request["documents"], "path")
    if plan["schema_version"] != "mdocr-plan-2" or docs.keys() != expected.keys():
        raise ValueError("OCR plan set differs from requested documents")
    policy = request["policy"]
    settings = plan["settings"]
    if "llm_schema" in settings and settings["llm_schema"] != _IMAGE_SCHEMA:
        raise ValueError("OCR plan uses an unsupported LLM schema")
    for key in (
        "mode",
        "model",
        "effort",
        "concurrency",
        "request_budget",
        "max_seconds",
        "cache_dir",
    ):
        if plan["settings"][key] != policy[key]:
            raise ValueError(f"OCR plan did not preserve {key}")
    if settings.get("fix", 0) != policy.get("fix", 0):
        raise ValueError("OCR plan did not preserve fix")
    for key, entry in expected.items():
        doc = docs[key]
        if any(doc[field] != entry[field] for field in ("path", "output", "sha256")):
            raise ValueError("OCR plan doc does not bind its input and destination")
    return plan, docs


def _verify_input(root, entry, doc):
    for key, sha in [("path", "sha256"), ("raw_copy", "source_sha256")]:
        if file_digest(_path(root, entry[key])) != entry[sha]:
            raise ValueError("OCR frozen input bytes changed")
    for asset in entry["assets"].values():
        if file_digest(_path(root, asset["path"])) != asset["sha256"]:
            raise ValueError("OCR frozen resource bytes changed")
    for im in doc["images"]:
        if not im["selected"] or im["error"]:
            continue
        asset = entry["assets"].get(im["resource_id"])
        if asset:
            if (im["source_path"], im["sha256"]) != (asset["path"], asset["sha256"]):
                raise ValueError("OCR plan changed local resource binding")
        else:
            if urlsplit(im["resource_id"]).scheme not in {"http", "https"}:
                raise ValueError(
                    "OCR plan references a local image not frozen by collect"
                )
            copied = _path(root / "plan.json.images", im["source_path"])
            if file_digest(copied) != im["sha256"]:
                raise ValueError("OCR staged remote image bytes changed")


def _tool_result(root, path, plan_sha):
    report = read_json(_path(root, path))
    if (
        report["schema_version"] != "mdocr-result-2"
        or report["plan_sha256"] != plan_sha
        or report.get("result_path") != str(root / "result.json")
    ):
        raise ValueError(
            "OCR result/journal is not bound to the original plan and result path"
        )
    return report


def _verified_result(root, plan, doc, entry, row):
    _verify_input(root, entry, doc)
    if row["path"] != doc["path"]:
        raise ValueError("OCR row source path is not its planned source")
    occurrences = _index(doc["images"], "id")
    resources = _index(row["resources"], "id")
    if resources.keys() != occurrences.keys() or any(
        item["resource_id"] != occurrences[key]["resource_id"]
        for key, item in resources.items()
    ):
        raise ValueError("OCR row resource occurrences do not match plan")
    if row.get("status") != "completed":
        if row.get("output") is not None or row.get("ocr") is not None:
            raise ValueError("Incomplete OCR row claims a usable output or evidence")
        selected = [
            (item, occurrences[key])
            for key, item in resources.items()
            if occurrences[key]["selected"]
        ]
        if any(
            "send_state" in item
            and (
                item["send_state"] != "not_sent"
                or item["status"] != "failed"
                or item.get("reason") != "svg_profile_cleanup_failed"
                or im["error"]
            )
            for item, im in selected
        ):
            raise ValueError("Invalid OCR pre-send occurrence evidence")
        states = {item["status"] for item, _ in selected}
        reasons = sorted({item["reason"] for item, _ in selected if item.get("reason")})
        reason = reasons[0] if reasons else "OCR_PARTIAL_OR_UNKNOWN"
        if (
            row.get("status") != "partial"
            or not selected
            or states & {"admitted", "unknown"}
        ):
            return None, "unknown", "unknown", reason
        allowed = {"failed", "held", "reused", "success", "unattempted"}
        if not states <= allowed:
            raise ValueError("Unrecognized OCR occurrence state")
        if all(
            item["status"] == "unattempted" or item.get("send_state") == "not_sent"
            for item, _ in selected
        ):
            return None, "skipped", "not_sent", reason
        sent = any(
            item["status"] in {"failed", "held", "success"}
            and item.get("send_state") != "not_sent"
            and not im["error"]
            for item, im in selected
        )
        return None, "failed", "settled" if sent else "not_sent", reason

    if row["output"] != doc["output"]:
        raise ValueError("OCR output does not identify its planned destination")
    output = _path(root, row["output"]).read_bytes()
    output.decode("utf-8")
    if digest(output) != row["output_sha256"]:
        raise ValueError("OCR output bytes changed")
    evidence = row["ocr"]
    config = evidence["configuration"]
    settings = plan["settings"]
    if evidence["contract"] != "mdocr-ocr-2" or config["mode"] != settings["mode"]:
        raise ValueError("OCR evidence contract or mode changed")
    if config["mode"] == "llm" and (
        config["model"] != settings["model"]
        or config["effort"] != settings["effort"]
        or config["prompt_sha256"] != digest(settings["prompt"].encode())
        or config["schema"] != settings.get("llm_schema", _LEGACY_SCHEMA)
    ):
        raise ValueError("OCR evidence configuration differs from plan")
    selected = {}
    admitted = False
    for key, im in occurrences.items():
        state = resources[key]["status"]
        if im["selected"]:
            if im["error"] or state not in {"success", "reused"}:
                raise ValueError("OCR completed row has an unfinished image")
            selected.setdefault(im["resource_id"], im)
            admitted |= state == "success"
            prefix = (
                f"<!-- mdocr:ocr-begin v=2 id={im['id']} -->\n"
                f"<!-- mdocr:meta mode={config['mode']} sha256={im['sha256']} -->\n"
            ).encode()
            if prefix not in output:
                raise ValueError(
                    "OCR completed output does not contain its planned block"
                )
        elif state != "unselected":
            raise ValueError("OCR completed row changed planned image selection")
    images = _index(evidence["images"], "resource_id")
    if images.keys() != selected.keys():
        raise ValueError("OCR evidence does not cover the selected images")
    if selected and config["mode"] == "llm":
        context = [
            doc["sha256"],
            [[im["id"], im["sha256"]] for im in doc["images"] if im["selected"]],
        ]
        canonical = (
            json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        if evidence.get("context_sha256") != digest(canonical.encode("utf-8")):
            raise ValueError("OCR evidence does not bind the planned LLM context")
    for key, image in images.items():
        im = selected[key]
        if image["status"] != "success" or (
            image["source_path"],
            image["source_sha256"],
        ) != (im["source_path"], im["sha256"]):
            raise ValueError("OCR original image differs from the planned image")
        artifact = _path(root, image["artifact"]).read_bytes()
        marker = f"<!-- mdocr:ocr-begin v=2 id={im['id']} -->\n".encode()
        if (
            not artifact.startswith(marker)
            or not artifact.endswith(b"\n<!-- mdocr:ocr-end -->")
            or digest(artifact) != image["artifact_sha256"]
            or artifact not in output
        ):
            raise ValueError("OCR block witness differs from output or its digest")
    # This is a caller-owned projection. Never rewrite the original mdocr row.
    projected = {
        **evidence,
        "images": [
            {
                **image,
                "resource_id": entry["assets"].get(key, {}).get("resource_id", key),
            }
            for key, image in images.items()
        ],
    }
    return (
        {
            "output": row["output"],
            "output_sha256": row["output_sha256"],
            "status": "completed",
            "ocr": projected,
            "assets": entry["assets"],
        },
        "completed",
        "settled" if admitted else "not_sent",
        None,
    )


_INVALID = (OSError, ValueError, KeyError, TypeError, AttributeError)


def _settled_report(root, seal):
    for name, expected in seal["witnesses"].items():
        path = _path(root, root / name)
        if file_digest(path) != expected:
            raise ValueError(f"OCR witness changed: {name}")
    if seal["report"]:
        return _tool_result(root, root / seal["report"], seal["witnesses"]["plan.json"])
    return None


def _previous_ocr_evidence(previous, row):
    root = Path(previous["input"]).parent
    seal_path = root / "settled.json"
    if file_digest(seal_path) != previous["seal_sha256"]:
        raise ValueError("OCR checkpoint changed")
    seal = read_json(seal_path)
    payload = _settled_report(root, seal)
    request = read_json(root / "request.json")
    entry = _index(request["documents"])[row["document_id"]]
    if file_digest(row["raw_path"]) != entry["source_sha256"]:
        raise ValueError("Raw bytes changed at the same version")
    resources = _index(row.get("resources") or [], "resource_id")
    for asset in entry["assets"].values():
        if file_digest(resources[asset["resource_id"]]["path"]) != asset["sha256"]:
            raise ValueError("Raw resource changed at the same version")
    if not same_version(entry["version"], row["version"]):
        raise ValueError("OCR checkpoint version changed")
    if payload is None:
        if not seal["dispatched"]:
            return None, "skipped", "not_sent", seal["reason"]
        raise ValueError("Previous OCR report unavailable")
    plan, docs = _load_plan(root, request)
    return _verified_result(
        root,
        plan,
        docs[entry["path"]],
        entry,
        _index(payload["documents"])[entry["path"]],
    )


def _saved_ocr(previous, row):
    result, *_ = _previous_ocr_evidence(previous, row)
    if not result:
        raise ValueError("Previous OCR success no longer verifies")
    return result


def verify_completed_ocr(store, row, evidence):
    """Verify the original successful tool output at a paused publication boundary."""
    try:
        step = store.step(row["document_id"], "ocr")
        if (
            not step
            or step["status"] != "completed"
            or not same_version(step["version"], row["version"])
        ):
            raise ValueError("missing matching completed OCR attempt")
        if _saved_ocr(step, row)["ocr"] != evidence:
            raise ValueError("OCR publication evidence changed")
    except _INVALID as error:
        raise ValueError(
            f"OCR evidence changed for {row['document_id']}; generate a new plan"
        ) from error


def _start_args(root, request):
    policy = request["policy"]
    docs = request["documents"]
    atomic_json(root / "inputs.json", [doc["path"] for doc in docs])
    args = [
        "plan",
        "--mode",
        policy["mode"],
        "--list",
        root / "inputs.json",
        "--output-dir",
        root / "output",
        "--plan",
        root / "plan.json",
        "--cache-dir",
        policy["cache_dir"],
        "--concurrency",
        str(policy["concurrency"]),
        "--max-seconds",
        policy["max_seconds"],
    ]
    if policy.get("fix", 0):
        args += ["--fix", str(policy["fix"])]
    if policy["mode"] == "llm":
        args += ["--model", policy["model"], "--effort", policy["effort"]]
    if policy["request_budget"] is not None:
        args += ["--request-budget", policy["request_budget"]]
    if any(entry["selection"] is not None for entry in docs):
        # A missing selection means all occurrences, not an empty list. The
        # mdocr CLI only accepts a whole-batch selection when all rows specify it.
        if any(entry["selection"] is None for entry in docs):
            raise ValueError("OCR batch mixes explicit and implicit selections")
        atomic_json(
            root / "selection.json", {doc["path"]: doc["selection"] for doc in docs}
        )
        args += ["--selection", root / "selection.json"]
    downloads = [entry["path"] for entry in docs if entry["download"]]
    if downloads:
        args.append("--download")
        if len(downloads) < len(docs):
            scope = root / "download-list.json"
            atomic_json(scope, downloads)
            args += ["--download-list", scope]
    return args


# Validate historical admissions as well as those made by the current runner.
# Old plans allowed Paddle's generic rejection here; new admissions do not.
_FIXABLE_FAILURES = {
    "OCR_PROVIDER_FAILED",
    "OCR_RATE_LIMITED",
    "rate_limited",
    "response_incomplete",
    "response_json_invalid",
    "response_mermaid_fence_invalid",
    "response_output_limit",
    "response_schema_invalid",
    "response_table_invalid",
    "response_unexpected_stop",
    "svg_profile_cleanup_failed",
}


def _verify_running_journal(state, budget, mirror=None, *, fix=0, repair=0):
    attempts = state["requests"]
    count = state["budget"]["requests"]
    if (
        state["status"] != "running"
        or not isinstance(attempts, list)
        or type(count) is not int
        or count != len(attempts)
        or (budget is not None and count > budget)
        or any(
            item["status"] not in {"admitted", "success", "failed", "unknown"}
            for item in attempts
        )
    ):
        raise ValueError("OCR journal cannot prove prior admissions")
    # The same key can occur again only in a bounded fix or repair round.
    previous = {}
    for attempt in attempts:
        key = attempt["key"]
        last = previous.get(key)
        tag = "fix_round" if "fix_round" in attempt else "repair_round"
        if tag in attempt:
            number = attempt[tag]
            other = "repair_round" if tag == "fix_round" else "fix_round"
            if (
                last is None
                or other in attempt
                or type(number) is not int
                or not 1 <= number <= (fix if tag == "fix_round" else repair)
                or number != last.get(tag, 0) + 1
                or (
                    tag == "fix_round"
                    and (
                        "repair_round" in last
                        or not (
                            last["status"] == "failed"
                            and last.get("reason") in _FIXABLE_FAILURES
                            or last["status"] == "unknown"
                            and last.get("reason") == "connection_outcome_unknown"
                        )
                    )
                )
                or (tag == "repair_round" and last["status"] != "success")
            ):
                raise ValueError("OCR journal has an invalid retry round")
        elif last is not None:
            raise ValueError("OCR journal duplicates an initial admission")
        previous[key] = attempt
    if mirror is not None:
        prior = mirror["requests"]
        if len(prior) > count or any(
            any(old.get(k) != new.get(k) for k in ("key", "fix_round", "repair_round"))
            or (old["status"] != "admitted" and old != new)
            for old, new in zip(prior, attempts)
        ):
            raise ValueError("OCR journal lost observed admissions or outcomes")


def _execute(root, request, store, run, call_tool, *, allow_dispatch=True):
    """Seal final evidence; resume only through mdocr's original journal."""
    req_path, plan_path = root / "request.json", root / "plan.json"
    start_path, seal_path = root / "started.json", root / "settled.json"
    report_path, journal = root / "result.json", root / "plan.json.execution.json"
    if seal_path.exists():
        return read_json(seal_path)
    initial = {"request.json": file_digest(req_path)}
    dispatched = start_path.exists()
    report, code, reason = None, None, None
    docs = {}
    if not dispatched and not allow_dispatch:
        reason = "OCR_BATCH_SCOPE_STALE"
    if reason is None and not dispatched and not plan_path.exists():
        code = call_tool("mdocr", _start_args(root, request))
        if code != 0:
            reason = "OCR_PLAN_REJECTED"
    if reason is None:
        try:
            plan, docs = _load_plan(root, request)
            initial["plan.json"] = plan_sha = file_digest(plan_path)
            if dispatched and read_json(start_path) != initial:
                raise ValueError("OCR immutable plan or request changed")
            if dispatched:
                if journal.exists():
                    state = _tool_result(root, journal, plan_sha)
                    if state["status"] in {"completed", "partial"}:
                        if report_path.exists():
                            _tool_result(root, report_path, plan_sha)
                            if report_path.read_bytes() != journal.read_bytes():
                                # Native journal is written first. Never replace
                                # or pass off an older mirror as a final report.
                                report = journal
                            else:
                                report = report_path
                        else:
                            report = journal
                    else:
                        mirror = (
                            _tool_result(root, report_path, plan_sha)
                            if report_path.exists()
                            else None
                        )
                        _verify_running_journal(
                            state,
                            request["policy"]["request_budget"],
                            mirror,
                            fix=plan["settings"].get("fix", 0),
                            repair=plan["settings"].get("mermaid_repair_rounds", 0),
                        )
                elif report_path.exists():
                    # A terminal result is sufficient to inspect completed rows,
                    # but an absent journal never authorizes execution again.
                    report = report_path
                else:
                    raise ValueError("OCR execution journal is missing")
            elif report_path.exists() or journal.exists():
                raise ValueError("unowned OCR execution artifacts")
            if report is None:
                settings = plan["settings"]
                if (
                    settings["mode"] == "llm"
                    and settings.get("llm_schema") != _IMAGE_SCHEMA
                ):
                    # Terminal legacy reports are verifiable above; an
                    # unsettled document-level send cannot be split safely.
                    raise ValueError("OCR plan does not support single-image admission")
                # Native execution needs every staged input intact. A terminal
                # batch is no longer executed and can be checked per document.
                for entry in request["documents"]:
                    _verify_input(root, entry, docs[entry["path"]])
        except _INVALID:
            reason = (
                "OCR_EXECUTION_UNVERIFIABLE"
                if dispatched
                else "OCR_PLAN_BINDING_INVALID"
            )
    if reason is None and report is None and not allow_dispatch:
        reason = "OCR_BATCH_SCOPE_STALE"
    if reason is None and report is None:
        atomic_json(start_path, initial, durable=True)
        store.complete_batch(
            (
                {
                    "doc_id": entry["document_id"],
                    "name": "ocr",
                    "version": entry["version"],
                    "status": "running",
                    "run_id": run.id,
                    "input": str(req_path),
                    "report": str(report_path),
                    "send_state": "unknown",
                    "input_sha256": entry["source_sha256"],
                }
                for entry in request["documents"]
            ),
            durable=True,
        )
        dispatched = True
        code = call_tool("mdocr", ["run", "--plan", plan_path, "--result", report_path])
        if code in {0, 2} and report_path.exists():
            report = report_path
        elif journal.exists():
            report = journal
        else:
            reason = "OCR_RESULT_UNAVAILABLE"
    if report is not None:
        try:
            payload = _tool_result(root, report, initial["plan.json"])
            if (
                payload["status"] not in {"completed", "partial"}
                or _index(payload["documents"]).keys() != docs.keys()
            ):
                raise ValueError("OCR terminal result has the wrong identity set")
        except _INVALID:
            report, reason = None, "OCR_RESULT_BINDING_INVALID"
    seal = {
        "dispatched": dispatched,
        "exit_code": code,
        "reason": reason,
        "report": report.name if report else None,
        "witnesses": initial
        | {
            path.name: file_digest(path)
            for path in [report_path, journal]
            if path.is_file()
        },
    }
    atomic_json(seal_path, seal, durable=True)
    return seal


def ocr_inputs(settings, book, store, run, rows, *, stale=()):
    from dogpdkb.apply import call_tool, failure

    plan_path = run.data["artifacts"]["plan"]
    policy = read_json(Path(plan_path)).get("ocr_policy") or settings.ocr_policy()
    # Older run plans predate configurable OCR concurrency. Their frozen public
    # CLI behavior was always the mdocr default of three.
    policy.setdefault("concurrency", 3)
    batches = store.path.parent / "ocr"
    request_path = _path(
        batches,
        run.data["artifacts"].get("ocr_request", batches / run.id / "request.json"),
    )
    root = request_path.parent
    seal_path = root / "settled.json"
    candidates = [
        row for row in rows if row["action"] == "compile" or row.get("retry_ocr")
    ]
    if not candidates:
        run.stage("ocr", total=0, completed=0, failed=0)
        return {}
    run.stage("ocr", total=len(candidates))
    results, pending, observed = {}, [], {}
    existing = read_json(request_path) if request_path.exists() else None
    enrolled = _index(existing["documents"]) if existing else {}
    rejected = existing["rejected"] if existing else {}
    if existing is not None and (
        existing["schema"] != "dogpdkb-ocr-request-3"
        or existing["run_id"] != run.id
        or ({"fix": 0} | existing["policy"]) != ({"fix": 0} | policy)
    ):
        raise ValueError("Saved OCR batch does not match the frozen run policy")
    superseded = set()

    def note(row, status, send, reason, **details):
        identifier = row["document_id"]
        results.setdefault(
            identifier, {"status": "unattempted" if status == "skipped" else status}
        )
        if reason is None:
            return
        if not any(
            item.get("step") == "ocr"
            and item.get("document_id") == identifier
            and item.get("reason") == reason
            for item in run.data["failures"]
        ):
            failure(
                run,
                row,
                "ocr",
                reason,
                persist=False,
                status=results[identifier]["status"],
                source_key=row["source_key"],
                send_state=send,
                input_sha256=file_digest(row["raw_path"]),
                retry_scope="document" if send == "not_sent" else "none",
                **details,
            )

    for row in candidates:
        identifier, version = row["document_id"], row["version"]
        if identifier in stale:
            continue
        was_enrolled = identifier in enrolled
        if was_enrolled and enrolled[identifier]["version"] != version:
            raise ValueError(
                "OCR request version does not match the saved business plan"
            )
        if identifier in rejected:
            note(
                row,
                "skipped",
                "not_sent",
                "OCR_INPUT_PREPARATION_FAILED",
                report=str(request_path),
            )
            continue
        previous = store.step(identifier, "ocr")
        if (
            previous
            and previous["status"] == "completed"
            and same_version(previous["version"], version)
            and (not was_enrolled or previous.get("run_id") != run.id)
        ):
            if was_enrolled:
                superseded.add(identifier)
            try:
                results[identifier] = _saved_ocr(previous, row)
            except _INVALID:
                note(
                    row,
                    "unknown",
                    "unknown",
                    "OCR_PREVIOUS_EVIDENCE_INVALID",
                    report=previous.get("report"),
                )
            continue
        blocked = store.blocked_attempt(identifier, "ocr", version)
        if blocked and (not was_enrolled or blocked.get("run_id") != run.id):
            if was_enrolled:
                superseded.add(identifier)
            note(
                row,
                "unknown",
                "unknown",
                "VERSION_ATTEMPT_SUPPRESSED",
                report=(previous or {}).get("report"),
            )
            continue
        if (
            previous
            and previous.get("run_id") != run.id
            and previous.get("send_state") == "not_sent"
            and same_version(previous["version"], version)
        ):
            try:
                if _previous_ocr_evidence(previous, row)[1:3] != (
                    "skipped",
                    "not_sent",
                ):
                    raise ValueError("Previous OCR evidence does not prove unsent work")
            except _INVALID:
                if was_enrolled:
                    superseded.add(identifier)
                note(
                    row,
                    "unknown",
                    "unknown",
                    "OCR_PREVIOUS_EVIDENCE_INVALID",
                    report=previous.get("report"),
                )
                continue
        if was_enrolled:
            continue
        if existing:
            raise ValueError("Saved OCR batch does not cover the frozen candidates")
        pending.append(row)
    excluded = superseded | set(stale)
    if (
        enrolled.keys() & excluded
        and enrolled.keys() - excluded
        and not (root / "started.json").exists()
        and not seal_path.exists()
    ):
        # A prepared request (even with a local plan) proves no provider
        # admission yet. Keep it intact and plan only the unaffected rows.
        revision = tempfile.mkdtemp(prefix=f"{run.id}-", dir=batches)
        run.data["artifacts"]["ocr_request"] = str(Path(revision) / "request.json")
        run.save(durable=True)
        return ocr_inputs(settings, book, store, run, rows, stale=stale)
    if existing is None and pending:
        original_ids = [row["document_id"] for row in pending]
        prepared = []
        for row in pending:
            try:
                prepared.append(_prepare(root, row))
            except (OSError, ValueError) as error:
                rejected[row["document_id"]] = str(error)
                note(
                    row,
                    "skipped",
                    "not_sent",
                    "OCR_INPUT_PREPARATION_FAILED",
                    report=str(request_path),
                )
        existing = {
            "schema": "dogpdkb-ocr-request-3",
            "run_id": run.id,
            "policy": policy,
            "initial_ids": original_ids,
            "documents": prepared,
            "rejected": rejected,
        }
        atomic_json(request_path, existing, durable=True)
        enrolled = _index(prepared)
    if existing is not None:
        run.data["artifacts"]["ocr_request"] = str(request_path)
    current = [row for row in rows if row["document_id"] not in stale]
    validate_inputs(current)
    if enrolled and existing is not None:

        def invoke(name, args):
            return call_tool(settings, run, name, args, command=policy["command"])

        seal = _execute(
            root,
            existing,
            store,
            run,
            invoke,
            allow_dispatch=not (enrolled.keys() & excluded),
        )
        validate_inputs(current)
        report, plan, docs, records = None, {}, {}, {}
        batch_reason = seal["reason"]
        try:
            report = _settled_report(root, seal)
            if report is not None:
                plan, docs = _load_plan(root, existing)
                records = _index(report["documents"])
                reports = run.data["artifacts"]
                reports["ocr_report"] = str(root / seal["report"])
        except _INVALID:
            report, batch_reason = None, "OCR_RESULT_BINDING_INVALID"
        artifacts = run.data["artifacts"]
        for key, path in [
            ("ocr_plan", root / "plan.json"),
            ("ocr_journal", root / "plan.json.execution.json"),
            ("ocr_report", root / "result.json"),
        ]:
            if path.is_file():
                artifacts.setdefault(key, str(path))
        for row in candidates:
            identifier = row["document_id"]
            if identifier not in enrolled or identifier in excluded:
                continue
            entry = enrolled[identifier]
            result = None
            if report is None:
                status, send = (
                    ("unknown", "unknown")
                    if seal["dispatched"]
                    else ("skipped", "not_sent")
                )
                reason = batch_reason or "OCR_RESULT_UNAVAILABLE"
            else:
                try:
                    result, status, send, reason = _verified_result(
                        root, plan, docs[entry["path"]], entry, records[entry["path"]]
                    )
                except _INVALID:
                    status, send, reason = (
                        "unknown",
                        "unknown",
                        "OCR_ROW_BINDING_INVALID",
                    )
            if result:
                results[identifier] = result
            # Held/unattempted rows of a terminal batch never run again on
            # that plan, but a new batch can admit demonstrably unsent work.
            observed[identifier] = {
                "doc_id": identifier,
                "name": "ocr",
                "version": row["version"],
                "run_id": run.id,
                "status": status,
                "send_state": send,
                "exit_code": seal["exit_code"],
                "input": str(request_path),
                "report": artifacts.get("ocr_report"),
                "seal_sha256": file_digest(seal_path),
                "reason": reason,
                "input_sha256": entry["source_sha256"],
            }
            note(
                row,
                status,
                send,
                reason,
                report=artifacts.get("ocr_report"),
                reports=[
                    artifacts[key]
                    for key in ("ocr_request", "ocr_plan", "ocr_journal")
                    if key in artifacts
                ],
                exit_code=seal["exit_code"],
            )
        store.complete_batch(observed.values())
        run.data["failures"] = [
            item
            for item in run.data["failures"]
            if not (
                item.get("step") == "ocr"
                and item.get("reason") == "CALL_INTERRUPTED"
                and item.get("document_id") in observed
            )
        ]
    run.stage(
        "ocr",
        total=len(candidates),
        completed=sum(r["status"] == "completed" for r in results.values()),
        failed=sum(r["status"] in {"failed", "unknown"} for r in results.values()),
    )
    return results
