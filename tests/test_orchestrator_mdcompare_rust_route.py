from types import SimpleNamespace

from dogpdkb import rust_mdcompare
from dogpdkb.records import atomic_json


def test_route_consumes_public_evidence_under_an_opaque_binding(tmp_path, monkeypatch):
    plan = tmp_path / "work.json"
    comparison_sha = "a" * 64
    result = (
        plan.with_suffix(".mdcompare") / "routes" / "opaque-route-id" / "result.json"
    )
    result.parent.mkdir(parents=True)
    score = tmp_path / "scores.json"
    score.write_text("{}")
    original = {
        "knowledge_base": "funds",
        "document_id": "42",
        "semantic_status": "CHANGED",
        "action": "COMPILE",
    }
    row = dict(
        original,
        action="SKIP",
        original_action="COMPILE",
        score=4,
        ambiguous=False,
        routing_reason="SCORE_AT_OR_BELOW_THRESHOLD",
    )
    atomic_json(
        result,
        {
            "schema_version": "mdcompare-route-result-1",
            "state": "complete",
            "comparison_sha256": comparison_sha,
            "scores_sha256": rust_mdcompare._digest(score.read_bytes()),
            "compile_above": 4,
            "compile_ambiguous": True,
            "results": [row],
        },
    )
    monkeypatch.setattr(
        rust_mdcompare,
        "_invoke",
        lambda *_: (
            0,
            {"comparison_sha256": comparison_sha, "artifacts": {"result": str(result)}},
        ),
    )
    comparison = SimpleNamespace(
        plan=plan, sha256=comparison_sha, payload={"results": [original]}
    )
    settings = SimpleNamespace(mdcompare_config=tmp_path / "tool-owned.toml")

    assert rust_mdcompare.route(
        settings, None, comparison, score, {"42": (4, False)}
    ) == (
        result,
        {"results": [row]},
    )
