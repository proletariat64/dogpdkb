"""Assemble accepted OCR patches offline without resuming any dispatch."""

import json
import re
from pathlib import Path
from typing import cast

from .plan import read_plan
from .runner import (
    _FIXABLE_FAILURES,
    Execution,
    NotCompleted,
    _patches,
    _sha,
    _validate,
)


def _validate_attempts(journal, settings):
    attempts = journal["requests"]
    count = journal["budget"]["requests"]
    limit = settings["request_budget"]
    if (
        journal["documents"]
        or type(count) is not int
        or count != len(attempts)
        or (limit is not None and count > limit)
    ):
        raise ValueError("journal is not an incomplete admission history")
    last_by_key, fixes = {}, {}
    for attempt in attempts:
        key, status = attempt["key"], attempt["status"]
        if status not in {"success", "failed", "unknown", "admitted"}:
            raise ValueError("unrecognized journal attempt status")
        last = last_by_key.get(key)
        fix, repair = attempt.get("fix_round"), attempt.get("repair_round")
        if "fix_round" in attempt:
            bounded = (
                "repair_round" not in attempt
                and last is not None
                and "repair_round" not in last
                and type(fix) is int
                and 1 <= fix <= settings.get("fix", 0)
                and fix == fixes.get(key, 0) + 1
                and (
                    (
                        last["status"] == "failed"
                        and last.get("reason") in _FIXABLE_FAILURES
                    )
                    or (
                        last["status"] == "failed"
                        and last.get("reason") == "OCR_PROVIDER_FAILED"
                    )
                    or (
                        last["status"] == "unknown"
                        and last.get("reason") == "connection_outcome_unknown"
                    )
                )
            )
        elif "repair_round" in attempt:
            bounded = (
                last is not None
                and last["status"] == "success"
                and last.get("warnings") == ["response_mermaid_syntax_invalid"]
                and last["patches"][0].get("warnings") == last["warnings"]
                and type(repair) is int
                and 1 <= repair <= settings.get("mermaid_repair_rounds", 0)
                and repair == last.get("repair_round", 0) + 1
            )
        else:
            bounded = last is None or (
                last["status"] == "failed" and "repair_round" not in last
            )
        if not bounded or (status != "success" and "patches" in attempt):
            raise ValueError("invalid journal attempt history")
        if status == "success":
            _patches(attempt["patches"], 1)
        if "fix_round" in attempt:
            fixes[key] = fix
        last_by_key[key] = attempt
    for key, entry in journal.get("reused", {}).items():
        if key != entry["key"] or entry["status"] != "reused":
            raise ValueError("invalid journal cache reuse")
        _patches(entry["patches"], 1)


def assemble_command(args):
    output = Path(args.output_dir).absolute()
    if output.resolve() != output or output.exists():
        raise ValueError("output directory must be new and not traverse a symlink")
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.execution_version):
        raise ValueError("execution version must be MAJOR.MINOR.PATCH")
    plan_path = Path(args.plan).resolve(strict=True)
    journal_path = Path(args.journal).resolve(strict=True)
    if journal_path != plan_path.with_name(plan_path.name + ".execution.json"):
        raise ValueError("journal is not the plan's native execution journal")
    plan_sha = _sha(plan_path.read_bytes())
    plan = read_plan(plan_path)
    raw_journal = journal_path.read_bytes()
    journal = json.loads(raw_journal)
    if (
        journal["schema_version"] != "mdocr-result-2"
        or journal["status"] != "running"
        or journal["plan_sha256"] != plan_sha
    ):
        raise ValueError("journal is not a running execution of the frozen plan")
    result_path = Path(journal["result_path"])
    if result_path.resolve() != result_path or result_path.parent != plan_path.parent:
        raise ValueError("original result must be a canonical sibling path")
    original_artifacts = plan_path.with_name(plan_path.name + ".evidence")
    locks = tuple(p.with_name(p.name + ".lock") for p in (plan_path, result_path))
    protected = [plan_path, journal_path, result_path, original_artifacts, *locks]
    cache = plan["settings"]["cache_dir"]
    if cache:
        protected.append(Path(cache))
    for doc in plan["documents"]:
        protected.extend(Path(doc[key]) for key in ("path", "output"))
        protected.extend(
            Path(im["source_path"]) for im in doc["images"] if im.get("source_path")
        )
    if any(
        output.is_relative_to(path) or path.is_relative_to(output)
        for path in map(Path.resolve, protected)
    ):
        raise ValueError("assembly destination overlaps original execution artifacts")
    texts = _validate(
        plan, plan_path, result_path, journal_path, original_artifacts, locks
    )
    try:
        _validate_attempts(journal, plan["settings"])
    except NotCompleted as error:
        raise ValueError(f"invalid journal patch: {error.reason}") from error
    execution = Execution(
        plan,
        plan_sha,
        journal_path,
        result_path,
        original_artifacts,
        False,
        version=args.execution_version,
    )
    _, _, layout = execution.plan_jobs()
    if (
        journal_path.read_bytes() != raw_journal
        or _sha(plan_path.read_bytes()) != plan_sha
    ):
        raise ValueError("original plan or journal changed during validation")
    output.mkdir(mode=0o700)
    execution.ledger = cast(Path | None, None)
    execution.result = output / "assembly.json"
    execution.artifacts = output / "evidence"
    execution.state["result_path"] = str(execution.result)
    execution.state["recovery"] = {
        "journal_sha256": _sha(raw_journal),
        "execution_version": args.execution_version,
    }
    for i, doc in enumerate(execution.docs):
        doc["output"] = str(output / "documents" / f"{i}.md")
    return execution.publish(texts, layout)
