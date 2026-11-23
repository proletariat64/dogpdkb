"""Call the Rust work-plan CLI and validate its public artifact bindings."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dogpdkb.processes import diagnostic_argv, run_command
from dogpdkb.records import atomic_json, read_json, version_key
from dogpdkb.semantic import SemanticArtifactError
from dogpdkb.settings import tomllib


def selected(settings):
    return "mdcompare_rust" in settings.tools


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _object(value):
    if not isinstance(value, dict):
        raise SemanticArtifactError("invalid mdcompare JSON object")
    return value


def _bound_file(root, name):
    relative = Path(name)
    path = root / relative
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or path.resolve() != path
        or not path.is_file()
        or path.stat().st_nlink != 1
    ):
        raise ValueError("invalid mdcompare artifact path")
    return path.read_bytes()


def _artifact(summary, expected, key):
    if summary["artifacts"].get(key) != str(expected):
        raise ValueError("mdcompare summary artifact mismatch")
    return expected


def _invoke(settings, run, command, plan, *arguments, configured=True):
    argv = [*settings.command("mdcompare_rust"), command, "--plan", str(plan)]
    argv.extend(str(value) for value in arguments)
    if configured and settings.mdcompare_config is not None:
        argv.extend(("--config", str(settings.mdcompare_config)))
    run.data["tool"] = {"name": "mdcompare_rust", "argv": diagnostic_argv(argv)}
    run.save()
    with tempfile.TemporaryDirectory(dir=run.directory) as temporary:
        output = Path(temporary) / "summary.json"
        code = run_command(
            argv, log=run.directory / "mdcompare_rust.log", stdout=output
        )
        if code in {130, 143}:
            raise InterruptedError(code)
        if code not in {0, 2}:
            raise ValueError(f"mdcompare {command} returned exit {code}")
        summary = read_json(output)
    if (
        not isinstance(summary, dict)
        or summary.get("schema_version") != "mdcompare-summary-1"
        or summary.get("command") != command
        or summary.get("state") != ("partial" if code == 2 else "complete")
        or not isinstance(summary.get("artifacts"), dict)
    ):
        raise ValueError("invalid mdcompare final summary")
    return code, summary


def _snapshot(descriptor):
    snapshot = {
        key: value
        for key, value in descriptor.items()
        if key not in {"knowledge_base", "document_id"}
    }
    snapshot["path"] = str(Path(snapshot["path"]).absolute())
    kind = snapshot["content_kind"]
    if kind == "historical_enriched":
        snapshot["content_kind"] = "enriched"
    elif kind != "raw":
        raise ValueError("invalid captured content kind")
    if "provenance" in snapshot:
        snapshot["provenance"] = {
            "version": version_key(snapshot["provenance"]["version"])
        }
    return snapshot


@dataclass(frozen=True)
class Comparison:
    plan: Path
    commit: Path
    result: Path
    index: Path | None
    sha256: str
    artifacts: dict
    code: int
    payload: dict


def compare(settings, run, plan, operation, baseline, incoming, *, recovery=None):
    plan = plan.absolute()
    root = plan.with_suffix(".mdcompare")
    baseline_by_id = {item["document_id"]: item for item in baseline}
    documents = [
        {
            "knowledge_base": item["knowledge_base"],
            "document_id": item["document_id"],
            "baseline": _snapshot(baseline_by_id[item["document_id"]])
            if item["document_id"] in baseline_by_id
            else None,
            "incoming": _snapshot(item),
        }
        for item in incoming
    ]
    work = {
        "schema_version": "mdcompare-work-1",
        "operation": operation,
        "documents": documents,
    }
    if recovery is not None:
        work["body_recovery"] = str(recovery.absolute())
    atomic_json(plan, work)
    plan_bytes = plan.read_bytes()
    code, summary = _invoke(settings, run, "run", plan)
    commit_path = _artifact(summary, root / "commit.json", "commit")
    result_path = _artifact(summary, root / "result.json", "result")
    commit_bytes = _bound_file(root, "commit.json")
    commit = _object(json.loads(commit_bytes))
    digest = _digest(commit_bytes)
    frozen_bytes = _bound_file(root, "plan.json")
    frozen = _object(json.loads(frozen_bytes))
    if (
        digest != summary.get("comparison_sha256")
        or commit.get("schema_version") != "mdcompare-commit-1"
        or commit.get("plan_sha256") != _digest(frozen_bytes)
        or frozen.get("source") != str(plan)
        or frozen.get("source_sha256") != _digest(plan_bytes)
        or not isinstance(commit.get("artifacts"), dict)
    ):
        raise ValueError("invalid mdcompare comparison commit")

    hashes = commit["artifacts"]
    contents = _comparison_files(root, hashes, semantic=operation == "semantic")
    result_bytes = contents["result.json"]
    index = (
        _artifact(summary, root / "diff-index.json", "diff_index")
        if operation == "semantic"
        else None
    )
    payload = _object(json.loads(result_bytes))
    if (
        payload.get("schema_version") != f"mdcompare-{operation}-result-1"
        or payload.get("state") != summary["state"]
        or not isinstance(payload.get("results"), list)
        or [
            (_object(row).get("knowledge_base"), row.get("document_id"))
            for row in payload["results"]
        ]
        != [(item["knowledge_base"], item["document_id"]) for item in documents]
    ):
        raise ValueError("invalid mdcompare comparison result")
    return Comparison(
        plan, commit_path, result_path, index, digest, hashes, code, payload
    )


def _comparison_files(root, hashes, *, semantic):
    required = {"result.json", "diff-index.json"} if semantic else {"result.json"}
    if (
        not isinstance(hashes, dict)
        or not required <= hashes.keys()
        or not hashes.keys() <= required | {"review.md"}
    ):
        raise ValueError("unexpected mdcompare commit artifacts")
    contents = {}
    for name, expected_digest in hashes.items():
        data = _bound_file(root, name)
        if expected_digest != _digest(data):
            raise ValueError("mdcompare artifact digest mismatch")
        contents[name] = data
    return contents


def verify_semantic_commit(witness):
    """Check the exact persisted comparison generation before a plan resumes."""
    marker = Path(witness["path"])
    if not marker.is_absolute() or marker.name != "commit.json":
        raise ValueError("invalid frozen mdcompare commit path")
    root = marker.parent
    data = _bound_file(root, marker.name)
    commit = _object(json.loads(data))
    if (
        _digest(data) != witness["sha256"]
        or commit.get("schema_version") != "mdcompare-commit-1"
        or commit.get("plan_sha256") != _digest(_bound_file(root, "plan.json"))
    ):
        raise ValueError("frozen mdcompare comparison changed")
    contents = _comparison_files(root, commit.get("artifacts"), semantic=True)
    index = _object(json.loads(contents["diff-index.json"]))
    if (
        index.get("schema_version") != "mdcompare-diff-index-1"
        or index.get("report_sha256") != _digest(contents["result.json"])
        or index.get("evidence_directory") != "evidence"
        or not isinstance(index.get("documents"), list)
    ):
        raise ValueError("invalid frozen mdcompare index")
    for document in index["documents"]:
        relative = Path(_object(document)["evidence"])
        if not relative.is_relative_to("evidence") or len(relative.parts) != 2:
            raise ValueError("invalid frozen mdcompare evidence path")
        if _digest(_bound_file(root, relative)) != document.get("sha256"):
            raise ValueError("frozen mdcompare evidence changed")


def score_enabled(settings):
    if settings.mdcompare_config is None:
        return False
    with settings.mdcompare_config.open("rb") as stream:
        config = tomllib.load(stream)
    score = config.get("score", {})
    return (
        isinstance(score, dict)
        and type(score.get("request_budget")) is int
        and score["request_budget"] > 0
    )


def score_plan(path, comparison, book_key, identifiers):
    atomic_json(
        path,
        {
            "schema_version": "mdcompare-score-work-1",
            "comparison_plan": str(comparison.plan),
            "comparison_sha256": comparison.sha256,
            "documents": [
                {"knowledge_base": book_key, "document_id": key} for key in identifiers
            ],
        },
        durable=True,
    )


def score(settings, run, plan, comparison, book_key, identifiers):
    root = plan.with_suffix(".mdcompare")
    result = root / "result.json"
    code, summary = _invoke(settings, run, "score", plan)
    _artifact(summary, result, "result")
    result_bytes = _bound_file(root, "result.json")
    journal_bytes = _bound_file(root, "journal.json")
    frozen_bytes = _bound_file(root, "plan.json")
    payload, journal, frozen = (
        _object(json.loads(value))
        for value in (result_bytes, journal_bytes, frozen_bytes)
    )
    expected = [(book_key, key) for key in identifiers]
    if (
        summary.get("comparison_sha256") != comparison.sha256
        or payload.get("comparison_sha256") != comparison.sha256
        or payload.get("schema_version") != "mdcompare-score-result-1"
        or payload.get("state") != summary["state"]
        or journal.get("terminal") != payload["state"]
        or journal.get("schema_version") != "mdcompare-score-journal-1"
        or frozen.get("schema_version") != "mdcompare-score-plan-1"
        or frozen.get("comparison_sha256") != comparison.sha256
        or frozen.get("comparison_plan") != str(comparison.plan)
        or frozen.get("source") != str(plan)
        or frozen.get("source_sha256") != _digest(plan.read_bytes())
        or payload.get("score_plan_sha256") != _digest(frozen_bytes)
        or journal.get("score_plan_sha256") != _digest(frozen_bytes)
        or payload.get("journal_sha256") != _digest(journal_bytes)
        or not isinstance(payload.get("rows"), list)
        or not isinstance(frozen.get("documents"), list)
        or not isinstance(journal.get("attempts"), list)
    ):
        raise ValueError("invalid mdcompare score binding")

    def identities(values):
        return [
            (_object(row).get("knowledge_base"), row.get("document_id"))
            for row in values
        ]

    if (
        identities(payload["rows"]) != expected
        or identities(frozen["documents"]) != expected
    ):
        raise ValueError("mdcompare score selection mismatch")
    admissions = {}
    for admission, item in enumerate(journal["attempts"], 1):
        document = _object(item).get("document")
        if (
            type(document) is not int
            or not 0 <= document < len(expected)
            or document in admissions
        ):
            raise ValueError("invalid mdcompare admission")
        admissions[document] = admission, item

    outcomes, scores = {}, {}
    for index, row in enumerate(payload["rows"]):
        record = admissions.get(index)
        state = row.get("state")
        if "admission" not in row:
            raise ValueError("mdcompare score admission state missing")
        if record is None:
            if state != "not_sent" or row["admission"] is not None:
                raise ValueError("mdcompare score has no matching admission")
            status = "skipped"
        else:
            sequence, attempt = record
            if (
                type(row.get("admission")) is not int
                or row["admission"] != sequence
                or state != attempt.get("state")
            ):
                raise ValueError("mdcompare score admission mismatch")
            if state == "scored":
                binding = frozen["documents"][index]
                if (
                    any(
                        row.get(key) != binding.get(key)
                        for key in ("evidence", "evidence_sha256")
                    )
                    or any(
                        row.get(key) != attempt.get(key)
                        for key in ("score", "ambiguous")
                    )
                    or type(row.get("score")) is not int
                    or not 1 <= row["score"] <= 5
                    or type(row.get("ambiguous")) is not bool
                ):
                    raise ValueError("mdcompare score evidence mismatch")
                status = "completed"
                scores[row["document_id"]] = (row["score"], row["ambiguous"])
            elif state in {"unknown", "unscored"}:
                status = "unknown" if state == "unknown" else "failed"
            else:
                raise ValueError("unsettled mdcompare admission")
        outcomes[row["document_id"]] = status, row.get("reason_code")
    if code != (0 if len(scores) == len(expected) else 2):
        raise ValueError("mdcompare score completion mismatch")
    return result, outcomes, scores


def route(settings, run, comparison, score_result, expected_scores):
    config = settings.mdcompare_config
    if config is None:
        default = comparison.plan.parent / "default.toml"
        if default.is_file():
            config = default
    policy = {}
    if config is not None:
        with config.open("rb") as stream:
            policy = tomllib.load(stream).get("route", {})
    code, summary = _invoke(
        settings, run, "route", comparison.plan, "--scores", score_result
    )
    name = summary["artifacts"].get("result")
    if (
        code
        or summary.get("comparison_sha256") != comparison.sha256
        or not isinstance(name, str)
    ):
        raise ValueError("invalid mdcompare route summary")
    path = Path(name)
    root = comparison.plan.with_suffix(".mdcompare") / "routes"
    if path.parent.parent != root or path.name != "result.json":
        raise ValueError("invalid mdcompare route path")
    route = _object(json.loads(_bound_file(path.parent, path.name)))
    score_sha256 = _digest(_bound_file(score_result.parent, score_result.name))
    threshold = route.get("compile_above")
    intercept = route.get("compile_ambiguous")
    if (
        route.get("schema_version") != "mdcompare-route-result-1"
        or route.get("state") != "complete"
        or route.get("comparison_sha256") != comparison.sha256
        or route.get("scores_sha256") != score_sha256
        or type(threshold) is not int
        or not 1 <= threshold <= 5
        or type(intercept) is not bool
        or threshold != policy.get("compile_above", 3)
        or intercept != policy.get("compile_ambiguous", False)
        or not isinstance(route.get("results"), list)
        or len(route["results"]) != len(comparison.payload["results"])
    ):
        raise ValueError("invalid mdcompare route result")
    binding = json.dumps(
        [comparison.sha256, score_sha256, threshold, intercept], separators=(",", ":")
    ).encode()
    if path.parent.name != _digest(binding):
        raise ValueError("mdcompare route policy digest mismatch")
    for old, new in zip(comparison.payload["results"], route["results"]):
        score, ambiguous = expected_scores.get(old["document_id"], (None, None))
        action, reason = old["action"], "DETERMINISTIC_ROUTE"
        if score is not None:
            if score > threshold:
                reason = "SCORE_ABOVE_THRESHOLD"
            elif ambiguous and intercept:
                reason = "SCORE_SKIP_AMBIGUITY_INTERCEPTED"
            else:
                action, reason = "SKIP", "SCORE_AT_OR_BELOW_THRESHOLD"
        if new != {
            **old,
            "action": action,
            "original_action": old["action"],
            "routing_reason": reason,
            "score": score,
            "ambiguous": ambiguous,
        }:
            raise ValueError("mdcompare route changed deterministic evidence")
    return path, {**comparison.payload, "results": route["results"]}
