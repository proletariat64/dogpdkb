"""Validate immutable score bindings and publish a separate route report."""
import hashlib
import json
from pathlib import Path

from .io import InputError, publish_reports, validate_outputs


def _read(path: Path):
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InputError("SCORE_BINDING_INVALID") from exc


def publish_semantic_route(args):
    report_bytes, index_bytes, scores_bytes = map(_read, (args.report, args.diff_index, args.scores))
    try:
        report, index, manifest = (json.loads(item) for item in (report_bytes, index_bytes, scores_bytes))
        if report["schema_version"] != "semantic-1.0" or index["schema_version"] != "mdcompare-semantic-diff-1":
            raise ValueError
        if index["report_sha256"] != hashlib.sha256(report_bytes).hexdigest():
            raise ValueError
        if manifest["schema_version"] != "mdcompare-semantic-scores-1":
            raise ValueError
        declared_index = (args.scores.parent / manifest["diff_index"]).resolve(strict=True)
        if declared_index != args.diff_index.resolve(strict=True):
            raise ValueError
        evidence_root = (args.diff_index.parent / index["evidence_directory"]).resolve(strict=True)
        if not evidence_root.is_dir():
            raise ValueError
        indexed = {}
        inputs = {args.report, args.diff_index, args.scores}
        for item in index["documents"]:
            key = (item["knowledge_base"], item["document_id"])
            if key in indexed:
                raise ValueError
            path = (args.diff_index.parent / item["evidence"]).resolve(strict=True)
            if not path.is_relative_to(evidence_root) or not path.is_file():
                raise ValueError
            payload = _read(path)
            sidecar = json.loads(payload)
            if (sidecar["knowledge_base"], sidecar["document_id"]) != key:
                raise ValueError
            indexed[key] = (item, path, payload)
            inputs.add(path)
        scores = {}
        for row in manifest["scores"]:
            key = (row["knowledge_base"], row["document_id"])
            if key in scores or key not in indexed:
                raise ValueError
            item, path, payload = indexed[key]
            expected = (args.diff_index.parent / row["evidence"]).resolve(strict=True)
            if expected != path or not expected.is_relative_to(evidence_root):
                raise ValueError
            if row["evidence_sha256"] != hashlib.sha256(payload).hexdigest():
                raise ValueError
            score = row["score"]
            if type(score) is not int or not 1 <= score <= 5 or type(row["ambiguous"]) is not bool:
                raise ValueError
            scores[key] = row
        results = []
        for source in report["results"]:
            key = (source["knowledge_base"], source["document_id"])
            row = scores.get(key)
            action = source["action"]
            route = action
            if source["semantic_status"] == "CHANGED" and action == "COMPILE":
                if row is None or row["score"] > args.compile_threshold or args.skip_ambiguity_intercept == "yes" and row["ambiguous"]:
                    route = "COMPILE"
                else:
                    route = "SKIP"
            result = dict(source)
            result.update({"action": route, "score": row["score"] if row else None,
                           "ambiguous": row["ambiguous"] if row else None})
            results.append(result)
        output = {"schema_version":"semantic-route-1.0", "compile_threshold":args.compile_threshold,
                  "skip_ambiguity_intercept":args.skip_ambiguity_intercept, "results":results}
        validate_outputs([args.output], inputs)
        publish_reports([(args.output, json.dumps(output, ensure_ascii=False, indent=2) + "\n")])
        return output
    except InputError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise InputError("SCORE_BINDING_INVALID") from exc
