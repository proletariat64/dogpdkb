"""Public semantic artifacts and immutable repair evidence, not tool algorithms."""

import hashlib
import json
import shutil
from pathlib import Path

from dogpdkb.records import atomic_json, read_json


class SemanticArtifactError(ValueError):
    """The public semantic evidence cannot authorize a product decision."""


def freeze_recovery(source, directory):
    """Freeze the explicit contract once; resumed plans never read live config."""
    target = directory / "recovery.json"
    if target.exists():
        validate_recovery(target)
        return target
    if source is None:
        return None
    source = Path(source)
    payload = read_json(source)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "mdcompare-body-recovery-1"
        or not isinstance(payload.get("documents"), list)
    ):
        raise SemanticArtifactError("invalid body recovery manifest")
    directory.mkdir(parents=True, exist_ok=True)
    documents, dependencies = [], []
    for index, record in enumerate(payload["documents"]):
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("source_copy"), str)
            or not record["source_copy"]
        ):
            raise SemanticArtifactError("invalid body recovery source_copy")
        original = source.parent / record["source_copy"]
        frozen, witness = (
            directory / f"source-{index}.md",
            directory / f"source-{index}.witness.md",
        )
        available = original.is_file()
        if available:
            shutil.copyfile(original, frozen)
            shutil.copyfile(frozen, witness)
        documents.append({**record, "source_copy": frozen.name})
        dependencies.append(
            {"path": frozen.name, "witness": witness.name, "available": available}
        )
    atomic_json(target, {**payload, "documents": documents})
    shutil.copyfile(target, directory / "recovery.witness.json")
    atomic_json(directory / "dependencies.json", dependencies)
    validate_recovery(target)
    return target


def validate_recovery(path):
    """Compare frozen witnesses, not live external recovery declarations."""
    path = Path(path)
    root = path.parent.resolve()
    try:
        if path.read_bytes() != (root / "recovery.witness.json").read_bytes():
            raise SemanticArtifactError("manifest changed")
        dependencies = read_json(root / "dependencies.json")
        documents = read_json(path)["documents"]
        if not isinstance(dependencies, list) or len(dependencies) != len(documents):
            raise SemanticArtifactError("invalid dependency registry")
        for index, item in enumerate(dependencies):
            if (
                item["path"] != documents[index]["source_copy"]
                or item["witness"] != f"source-{index}.witness.md"
                or type(item["available"]) is not bool
            ):
                raise SemanticArtifactError("dependency does not match frozen manifest")
            source, witness = root / item["path"], root / item["witness"]
            if source.resolve().parent != root or witness.resolve().parent != root:
                raise SemanticArtifactError("dependency escaped snapshot")
            if item["available"]:
                if source.read_bytes() != witness.read_bytes():
                    raise SemanticArtifactError("source changed")
            elif source.exists() or witness.exists():
                raise SemanticArtifactError("missing dependency changed")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SemanticArtifactError(
            "frozen body recovery missing or changed; generate a new plan"
        ) from exc


def _identities(items):
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise SemanticArtifactError("semantic identity list missing")
    result = []
    for item in items:
        identity = item.get("knowledge_base"), item.get("document_id")
        if any(not isinstance(value, str) or not value for value in identity):
            raise SemanticArtifactError("invalid semantic identity")
        result.append(identity)
    if len(set(result)) != len(result):
        raise SemanticArtifactError("duplicate semantic identity")
    return set(result)


def _validate_result(item):
    legal = {
        "NEW": ("COMPILE", True),
        "CHANGED": ("COMPILE", True),
        "UNCERTAIN": ("COMPILE", True),
        "EQUIVALENT": ("SKIP", True),
        "UNAVAILABLE": ("DEFER", False),
    }
    status, extraction = item.get("semantic_status"), item.get("extraction")
    if (
        not isinstance(status, str)
        or status not in legal
        or not isinstance(extraction, dict)
        or item.get("action") != legal[status][0]
        or type(extraction.get("available")) is not bool
        or extraction["available"] != legal[status][1]
        or type(item.get("comparison_complete")) is not bool
        or not isinstance(item.get("reason_codes"), list)
        or not item["reason_codes"]
        or any(not isinstance(reason, str) for reason in item["reason_codes"])
    ):
        raise SemanticArtifactError("illegal semantic status/action/extraction")
    if status == "EQUIVALENT" and not item["comparison_complete"]:
        raise SemanticArtifactError("unproven semantic equality")


def _validate_evidence(payload, item, result, row):
    if not isinstance(payload, dict) or _identities([payload]) != _identities([item]):
        raise SemanticArtifactError("semantic evidence identity mismatch")
    source_inputs = payload.get("source_inputs")
    if not isinstance(source_inputs, dict):
        raise SemanticArtifactError("semantic source inputs missing")
    for side, field in (("baseline", "old_units"), ("incoming", "new_units")):
        units = payload.get(field)
        if not isinstance(units, list) or any(
            not isinstance(unit, dict) or not isinstance(unit.get("text"), str)
            for unit in units
        ):
            raise SemanticArtifactError("incomplete semantic unit evidence")
        source = source_inputs.get(side)
        expected = (
            row.get("baseline", {}).get("path")
            if side == "baseline"
            else row["raw_path"]
        )
        if expected:
            if (
                not isinstance(source, dict)
                or not isinstance(source.get("path"), str)
                or Path(source["path"]).resolve() != Path(expected).resolve()
            ):
                raise SemanticArtifactError("semantic evidence source mismatch")
        elif source is not None:
            raise SemanticArtifactError("semantic evidence invented baseline")
    changes = payload.get("changes")
    if not isinstance(changes, list):
        raise SemanticArtifactError("semantic operations missing")
    seen = {"old_ref": set(), "new_ref": set()}
    for change in changes:
        if (
            not isinstance(change, dict)
            or not {"old_ref", "new_ref"}.issubset(change)
            or not isinstance(change.get("operation"), str)
            or change["operation"]
            not in {
                "add",
                "delete",
                "modify",
                "move",
                "equal",
                "uncertain",
            }
        ):
            raise SemanticArtifactError("invalid semantic operation")
        for field, units in (
            ("old_ref", payload["old_units"]),
            ("new_ref", payload["new_units"]),
        ):
            index = change[field]
            if index is not None:
                if type(index) is not int or not 0 <= index < len(units):
                    raise SemanticArtifactError(
                        "semantic operation references unavailable evidence"
                    )
                if units[index].get("exporter_title"):
                    raise SemanticArtifactError(
                        "exporter title is not an authored operation target"
                    )
                if index in seen[field]:
                    raise SemanticArtifactError(
                        "duplicate semantic occurrence ownership"
                    )
                seen[field].add(index)
        operation = change["operation"]
        if (
            (change["new_ref"] is None) != (operation == "delete")
            or (
                change["old_ref"] is None
                and operation in {"delete", "modify", "move", "equal"}
            )
            or (change["old_ref"] is not None and operation == "add")
        ):
            raise SemanticArtifactError("semantic operation has invalid sides")

    if result["extraction"]["available"]:
        for ref, units in (("old_ref", "old_units"), ("new_ref", "new_units")):
            expected = {
                i
                for i, unit in enumerate(payload[units])
                if not unit.get("exporter_title")
            }
            if seen[ref] != expected or len(expected) != result["extraction"].get(
                units
            ):
                raise SemanticArtifactError("incomplete semantic occurrence ownership")


def read_semantic_artifacts(
    code, report_path, diff_path, rows, knowledge_base, *, native_artifacts=None
):
    if code not in {0, 2} or not report_path.is_file() or not diff_path.is_file():
        raise SemanticArtifactError("authoritative semantic comparison unavailable")
    report_bytes = report_path.read_bytes()
    diff_bytes = diff_path.read_bytes()
    native = native_artifacts is not None
    if native and any(
        native_artifacts.get(name) != hashlib.sha256(data).hexdigest()
        for name, data in (
            ("result.json", report_bytes),
            ("diff-index.json", diff_bytes),
        )
    ):
        raise SemanticArtifactError("semantic commit witness changed")
    report, diff = json.loads(report_bytes), json.loads(diff_bytes)
    if (
        not isinstance(report, dict)
        or report.get("schema_version")
        != ("mdcompare-semantic-result-1" if native else "semantic-1.0")
        or report.get("scope") != "authored_body"
    ):
        raise SemanticArtifactError("invalid semantic report schema or scope")
    if not isinstance(diff, dict) or diff.get("schema_version") != (
        "mdcompare-diff-index-1" if native else "mdcompare-semantic-diff-1"
    ):
        raise SemanticArtifactError("invalid semantic diff schema")
    if diff.get("report_sha256") != hashlib.sha256(report_bytes).hexdigest():
        raise SemanticArtifactError("semantic report digest mismatch")
    expected = {(knowledge_base, str(row["document_id"])) for row in rows}
    if (
        _identities(report.get("results")) != expected
        or _identities(diff.get("documents")) != expected
    ):
        raise SemanticArtifactError(
            "semantic result or diff identity coverage mismatch"
        )
    by_result = {}
    for item in report["results"]:
        _validate_result(item)
        by_result[item["document_id"]] = item
    evidence_dir = diff.get("evidence_directory")
    if (
        not isinstance(evidence_dir, str)
        or len(Path(evidence_dir).parts) != 1
        or evidence_dir in {".", ".."}
        or Path(evidence_dir).is_absolute()
    ):
        raise SemanticArtifactError("semantic evidence directory invalid")
    root = diff_path.parent / evidence_dir
    if root.resolve().parent != diff_path.parent.resolve():
        raise SemanticArtifactError("semantic evidence directory escaped ownership")
    by_id = {row["document_id"]: row for row in rows}
    for item in diff["documents"]:
        reference = item.get("evidence")
        if not isinstance(reference, str) or Path(reference).is_absolute():
            raise SemanticArtifactError("semantic evidence reference invalid")
        path = (diff_path.parent / reference).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise SemanticArtifactError("semantic evidence sidecar missing or invalid")
        data = path.read_bytes()
        if item.get("sha256") != hashlib.sha256(data).hexdigest():
            raise SemanticArtifactError("semantic evidence digest mismatch")
        identifier = item["document_id"]
        sidecar = json.loads(data)
        if native and (
            not isinstance(sidecar, dict)
            or sidecar.get("schema_version") != "mdcompare-diff-evidence-1"
        ):
            raise SemanticArtifactError("invalid semantic evidence schema")
        _validate_evidence(sidecar, item, by_result[identifier], by_id[identifier])
    return report


def score_eligible(row):
    return (
        row["semantic_status"] == "CHANGED"
        and row["action"] == "COMPILE"
        and row["comparison_complete"]
        and row["extraction"]["available"]
    )


def read_score_outcomes(path, report, diff_index, expected):
    """A bound, complete score report can prove which reserved rows were unsent."""
    report_data = read_json(path)
    bindings = {
        (row["knowledge_base"], row["document_id"]): (row["evidence"], row["sha256"])
        for row in read_json(diff_index)["documents"]
    }
    if not isinstance(report_data, dict):
        raise SemanticArtifactError("invalid score result")
    scored, unscored = report_data.get("scores"), report_data.get("unscored")
    execution = report_data.get("execution")
    if (
        report_data.get("schema_version") != "mdcompare-semantic-scores-1"
        or not isinstance(scored, list)
        or not isinstance(unscored, list)
        or not isinstance(execution, dict)
    ):
        raise SemanticArtifactError("invalid score result scope")
    scored_ids, unscored_ids = _identities(scored), _identities(unscored)
    if scored_ids & unscored_ids or scored_ids | unscored_ids != expected:
        raise SemanticArtifactError("invalid score result scope")
    configuration = execution.get("configuration")
    if not isinstance(configuration, dict) or any(
        configuration.get(key) != hashlib.sha256(source.read_bytes()).hexdigest()
        for key, source in (("report_sha256", report), ("index_sha256", diff_index))
    ):
        raise SemanticArtifactError("score result belongs to another generation")
    outcomes = {}
    for row in scored + unscored:
        identity = row["knowledge_base"], row["document_id"]
        if bindings[identity] != (row.get("evidence"), row.get("evidence_sha256")):
            raise SemanticArtifactError("score result evidence mismatch")
        status = row.get("status")
        if identity in scored_ids:
            if status != "scored":
                raise SemanticArtifactError("invalid scored admission outcome")
            outcome = "completed"
        elif status == "unscored" and isinstance(row.get("reason"), str):
            outcome = "unknown" if row["reason"] == "unknown_outcome" else "failed"
        elif "status" not in row and "packet_sha256" not in row:
            outcome = "skipped"
        else:
            raise SemanticArtifactError("invalid score admission outcome")
        outcomes[identity[1]] = outcome, row.get("reason")
    requests = execution.get("requests")
    if type(requests) is not int or requests != sum(
        status != "skipped" for status, _ in outcomes.values()
    ):
        raise SemanticArtifactError("score result admission count mismatch")
    return outcomes


def read_score_route(code, path, semantic):
    if code != 0 or not path.is_file():
        raise SemanticArtifactError("semantic score route unavailable")
    route = read_json(path)
    if (
        route.get("schema_version") != "semantic-route-1.0"
        or route.get("compile_threshold") != 3
        or route.get("skip_ambiguity_intercept") != "no"
        or _identities(route.get("results")) != _identities(semantic["results"])
    ):
        raise SemanticArtifactError("invalid score route schema or scope")
    expected = {(r["knowledge_base"], r["document_id"]): r for r in semantic["results"]}
    for row in route["results"]:
        source = expected[row["knowledge_base"], row["document_id"]]
        if any(row.get(k) != v for k, v in source.items() if k != "action"):
            raise SemanticArtifactError("score route changed deterministic evidence")
        score = row.get("score")
        if score is not None and (
            type(score) is not int
            or not 1 <= score <= 5
            or type(row.get("ambiguous")) is not bool
        ):
            raise SemanticArtifactError("invalid semantic score")
        eligible = score_eligible(source)
        action = (
            "SKIP"
            if eligible and score is not None and score <= 3
            else source["action"]
        )
        reason = (
            ("SCORE_AT_OR_BELOW_THRESHOLD" if score <= 3 else "SCORE_ABOVE_THRESHOLD")
            if eligible and score is not None
            else "DETERMINISTIC_ROUTE"
        )
        if row.get("action") != action or row.get("routing_reason") != reason:
            raise SemanticArtifactError("invalid semantic score decision")
    return {**semantic, "results": route["results"]}


def bind_score_artifacts(paths):
    return {
        name: {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in paths.items()
    }


def verify_score_artifacts(witnesses):
    paths = {name: Path(row["path"]) for name, row in witnesses.items()}
    if bind_score_artifacts(paths) != witnesses:
        raise SemanticArtifactError("frozen semantic scoring artifacts changed")
