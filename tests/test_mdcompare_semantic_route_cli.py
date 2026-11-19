"""Public CLI contract for offline score-based semantic routing."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def invoke(tmp_path, command, *args):
    return subprocess.run(
        [sys.executable, "-m", "mdcompare", command, *map(str, args)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src")),
    )


def prepared(tmp_path, *, relative=False, recovery=False):
    old, new = tmp_path / "old.md", tmp_path / "new.md"
    old.write_text("old rule\n")
    new.write_text("new rule\n")
    manifests = []
    for name, body in (("old", old), ("new", new)):
        path = tmp_path / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "documents": [
                        {
                            "knowledge_base": "book",
                            "document_id": "1",
                            "content_kind": "raw",
                            "path": body.name if relative else str(body),
                            "metadata": {
                                "name": "Rule",
                                "logical_path": "",
                                "source_ref": "book:1",
                            },
                            "resources": [],
                        }
                    ],
                }
            )
        )
        manifests.append(path)
    report, index = tmp_path / "semantic.json", tmp_path / "diff.json"
    extra = ()
    if recovery:
        witness = tmp_path / "witness.md"
        witness.write_bytes(old.read_bytes())
        (tmp_path / "recovery.json").write_text(
            json.dumps(
                {
                    "schema_version": "mdcompare-body-recovery-1",
                    "documents": [
                        {
                            "knowledge_base": "book",
                            "document_id": "1",
                            "side": "baseline",
                            "source_copy": witness.name,
                            "insertions": [{"byte_offset": 0, "text": "\n"}],
                        }
                    ],
                }
            )
        )
        extra = ("--recovery", "recovery.json")
    result = invoke(
        tmp_path,
        "semantic",
        "--baseline",
        manifests[0].name if relative else manifests[0],
        "--incoming",
        manifests[1].name if relative else manifests[1],
        "--output",
        report,
        "--diff-output",
        index,
        *extra,
    )
    assert result.returncode == 0, result.stderr
    index_doc = json.loads(index.read_text())
    sidecar = tmp_path / index_doc["documents"][0]["evidence"]
    digest = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    return report, index, sidecar, digest


def scorefile(tmp_path, sidecar, digest, score, ambiguous=False):
    path = tmp_path / "scores.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "mdcompare-semantic-scores-1",
                "diff_index": "diff.json",
                "scores": [
                    {
                        "knowledge_base": "book",
                        "document_id": "1",
                        "evidence": "" + str(sidecar.relative_to(tmp_path)),
                        "evidence_sha256": digest,
                        "score": score,
                        "ambiguous": ambiguous,
                    }
                ],
            }
        )
    )
    return path


def route(tmp_path, report, index, scores, *extra):
    return invoke(
        tmp_path,
        "semantic-route",
        "--report",
        report,
        "--diff-index",
        index,
        "--scores",
        scores,
        "--output",
        tmp_path / "route.json",
        *extra,
    )


def test_strict_threshold_and_ambiguity_veto_are_public_cli_policy(tmp_path):
    report, index, sidecar, digest = prepared(tmp_path)
    for score, expected in ((3, "SKIP"), (4, "COMPILE"), (1, "SKIP"), (5, "COMPILE")):
        scores = scorefile(tmp_path, sidecar, digest, score)
        result = route(tmp_path, report, index, scores)
        assert result.returncode == 0, result.stderr
        assert (
            json.loads((tmp_path / "route.json").read_text())["results"][0]["action"]
            == expected
        )
    scores = scorefile(tmp_path, sidecar, digest, 3, True)
    assert (
        route(
            tmp_path, report, index, scores, "--skip-ambiguity-intercept", "no"
        ).returncode
        == 0
    )
    assert (
        json.loads((tmp_path / "route.json").read_text())["results"][0]["action"]
        == "SKIP"
    )
    assert (
        route(
            tmp_path, report, index, scores, "--skip-ambiguity-intercept", "yes"
        ).returncode
        == 0
    )
    assert (
        json.loads((tmp_path / "route.json").read_text())["results"][0]["action"]
        == "COMPILE"
    )


def test_digest_mismatch_rejects_without_publishing(tmp_path):
    report, index, sidecar, _ = prepared(tmp_path)
    scores = scorefile(tmp_path, sidecar, "0" * 64, 1)
    result = route(tmp_path, report, index, scores)
    assert result.returncode == 1 and "SCORE_BINDING_INVALID" in result.stderr
    assert not (tmp_path / "route.json").exists()


def test_semantic_index_carries_report_digest(tmp_path):
    report, index, _, _ = prepared(tmp_path)
    data = json.loads(index.read_text())
    assert data["report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()


def test_custom_threshold_stays_exclusive_and_high_ambiguity_passes(tmp_path):
    report, index, sidecar, digest = prepared(tmp_path)
    scores = scorefile(tmp_path, sidecar, digest, 4, True)
    assert (
        route(tmp_path, report, index, scores, "--compile-threshold", "4").returncode
        == 0
    )
    assert (
        json.loads((tmp_path / "route.json").read_text())["results"][0]["action"]
        == "SKIP"
    )
    assert (
        route(
            tmp_path, report, index, scores, "--skip-ambiguity-intercept", "yes"
        ).returncode
        == 0
    )
    row = json.loads((tmp_path / "route.json").read_text())["results"][0]
    assert (row["action"], row["routing_reason"]) == (
        "COMPILE",
        "SCORE_ABOVE_THRESHOLD",
    )


def test_missing_score_does_not_skip_and_input_paths_stay_untouched(tmp_path):
    report, index, sidecar, digest = prepared(tmp_path)
    scores = scorefile(tmp_path, sidecar, digest, 1)
    manifest = json.loads(scores.read_text())
    manifest["scores"] = []
    scores.write_text(json.dumps(manifest))
    assert route(tmp_path, report, index, scores).returncode == 0
    row = json.loads((tmp_path / "route.json").read_text())["results"][0]
    assert (row["action"], row["score"]) == ("COMPILE", None)
    source = tmp_path / "new.md"
    original = source.read_bytes()
    result = invoke(
        tmp_path,
        "semantic-route",
        "--report",
        report,
        "--diff-index",
        index,
        "--scores",
        scores,
        "--output",
        source,
    )
    assert result.returncode == 1 and "REPORT_INPUT_COLLISION" in result.stderr
    assert source.read_bytes() == original


def test_updated_score_cannot_bless_modified_sidecar(tmp_path):
    report, index, sidecar, _ = prepared(tmp_path)
    sidecar.write_bytes(sidecar.read_bytes() + b"\n")
    scores = scorefile(
        tmp_path, sidecar, hashlib.sha256(sidecar.read_bytes()).hexdigest(), 3
    )
    result = route(tmp_path, report, index, scores)
    assert result.returncode == 1 and "SCORE_BINDING_INVALID" in result.stderr
    assert not (tmp_path / "route.json").exists()


def test_relative_source_and_recovery_witness_are_protected_across_cwd(tmp_path):
    report, index, sidecar, digest = prepared(tmp_path, relative=True, recovery=True)
    scores = scorefile(tmp_path, sidecar, digest, 3)
    other = tmp_path / "another-cwd"
    other.mkdir()
    for name in ("new.md", "witness.md"):
        protected = tmp_path / name
        original = protected.read_bytes()
        result = invoke(
            other,
            "semantic-route",
            "--report",
            report,
            "--diff-index",
            index,
            "--scores",
            scores,
            "--output",
            protected,
        )
        assert result.returncode == 1 and "REPORT_INPUT_COLLISION" in result.stderr
        assert protected.read_bytes() == original
    assert route(tmp_path, report, index, scores).returncode == 0
