"""Validate immutable score bindings and publish a separate route report."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from .io import InputError, publish_reports, validate_outputs


def _identity(row):
    key = row["knowledge_base"], row["document_id"]
    if any(not isinstance(value, str) or not value for value in key):
        raise ValueError
    return key


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _generation(report, index, directory, inputs):
    name = index["evidence_directory"]
    if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError
    root = (directory / name).resolve(strict=True)
    if root.parent != directory or not root.is_dir():
        raise ValueError
    inputs.add(root)
    inputs.update(Path(value) for value in index["sources"].values())
    bindings = {}
    for row in index["documents"]:
        key = _identity(row)
        relative = Path(row["evidence"])
        path = (directory / relative).resolve(strict=True)
        if (
            key in bindings
            or relative.is_absolute()
            or path.parent != root
            or not path.is_file()
        ):
            raise ValueError
        inputs.add(path)
        data = path.read_bytes()
        digest = _digest(data)
        sidecar = json.loads(data)
        if _identity(sidecar) != key or row["sha256"] != digest:
            raise ValueError
        for source in sidecar["source_inputs"].values():
            if source is not None:
                inputs.add(Path(source["path"]))
        bindings[key] = row["evidence"], digest
    identities = []
    for row in report["results"]:
        identities.append(_identity(row))
        if (
            row["action"] not in ("COMPILE", "SKIP", "DEFER")
            or not isinstance(row["semantic_status"], str)
            or type(row["comparison_complete"]) is not bool
            or type(row["extraction"]["available"]) is not bool
        ):
            raise ValueError
    if len(identities) != len(bindings) or set(identities) != bindings.keys():
        raise ValueError
    return bindings


def publish_semantic_route(args):
    try:
        report_bytes = args.report.read_bytes()
        report = json.loads(report_bytes)
        index = json.loads(args.diff_index.read_bytes())
        manifest = json.loads(args.scores.read_bytes())
        if (
            report["schema_version"] != "semantic-1.0"
            or report["scope"] != "authored_body"
            or index["schema_version"] != "mdcompare-semantic-diff-1"
            or index["report_sha256"] != _digest(report_bytes)
            or manifest["schema_version"] != "mdcompare-semantic-scores-1"
            or (args.scores.parent / manifest["diff_index"]).resolve(strict=True)
            != args.diff_index.resolve(strict=True)
            or not isinstance(report["results"], list)
            or not isinstance(index["documents"], list)
            or not isinstance(manifest["scores"], list)
        ):
            raise ValueError
        inputs = {args.report, args.diff_index, args.scores}
        bindings = _generation(report, index, args.diff_index.parent.resolve(), inputs)
        scores = {}
        for row in manifest["scores"]:
            key = _identity(row)
            score, ambiguous = row["score"], row["ambiguous"]
            if (
                key in scores
                or bindings[key] != (row["evidence"], row["evidence_sha256"])
                or type(score) is not int
                or not 1 <= score <= 5
                or type(ambiguous) is not bool
            ):
                raise ValueError
            scores[key] = score, ambiguous
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        raise InputError("SCORE_BINDING_INVALID") from exc

    results = []
    for source in report["results"]:
        score, ambiguous = scores.get(_identity(source), (None, None))
        action = source["action"]
        if (
            source["semantic_status"] == "CHANGED"
            and source["comparison_complete"] is True
            and source["extraction"]["available"] is True
            and action == "COMPILE"
            and score is not None
        ):
            if score > args.compile_threshold:
                reason = "SCORE_ABOVE_THRESHOLD"
            elif ambiguous and args.skip_ambiguity_intercept == "yes":
                reason = "SCORE_SKIP_AMBIGUITY_INTERCEPTED"
            else:
                action, reason = "SKIP", "SCORE_AT_OR_BELOW_THRESHOLD"
        else:
            reason = "DETERMINISTIC_ROUTE"
        results.append(
            dict(
                source,
                action=action,
                score=score,
                ambiguous=ambiguous,
                routing_reason=reason,
            )
        )
    output = {
        "schema_version": "semantic-route-1.0",
        "compile_threshold": args.compile_threshold,
        "skip_ambiguity_intercept": args.skip_ambiguity_intercept,
        "results": results,
        "summary": {"actions": dict(Counter(row["action"] for row in results))},
    }
    validate_outputs([args.output], inputs)
    publish_reports(
        [(args.output, json.dumps(output, ensure_ascii=False, indent=2) + "\n")]
    )
