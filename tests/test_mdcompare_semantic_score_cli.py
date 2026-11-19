"""Public semantic-score CLI with a local fake Pi SDK and zero network calls."""

import json
import shutil
from pathlib import Path

import pytest

from tests.test_mdcompare_semantic_route_cli import invoke, prepared, route
from tests.test_orchestrator_apply_cli import (
    cli,
    instance,
    seed_body_baseline,
    two_sources,
)
from tests.test_orchestrator_semantic_cli import pause


@pytest.fixture
def score_runtime(tmp_path, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("requires Node")
    sdk = tmp_path / "score-sdk"
    sdk.mkdir()
    (sdk / "package.json").write_text(
        '{"name":"@earendil-works/pi-coding-agent","type":"module"}'
    )
    (sdk / "dist").mkdir()
    (sdk / "dist/index.js").write_text("""
import assert from 'node:assert/strict';
import { appendFileSync } from 'node:fs';
export class ModelRuntime {
 static async create(opts) { assert.equal(opts.allowModelNetwork,false); return new ModelRuntime(); }
 getPhysicalModel(provider,id) { assert.equal(provider,'openai'); assert.equal(id,'gpt-6.1-terra'); return {provider,id,api:'openai-responses',reasoning:true}; }
 isUsingOAuth() { return true; }
 async checkAuth(provider,opts) { assert.equal(process.env.OPENAI_API_KEY,undefined); assert.deepEqual(opts.env,{}); return {type:'oauth'}; }
 async completeSimple(model,context,opts) {
  assert.equal(opts.reasoning,'medium'); assert.equal(opts.maxRetries,0);
  assert.equal(opts.onPayload({}).text.format.strict,true);
  const evidence=JSON.parse(context.messages[0].content[0].text);
  assert.ok(evidence.old_units.length && evidence.new_units.length && evidence.operations.length);
  assert.ok(evidence.operations.every(x=>Number.isInteger(x.operation_id)));
  assert.ok(!('source_inputs' in evidence));
  globalThis.fetch=async ()=>{appendFileSync(process.env.SCORE_CALLS,'submitted\\n');
    if(process.env.SCORE_TEST_REPLY==='unknown') throw TypeError('synthetic disconnect');
    return new Response('{}',{status:process.env.SCORE_TEST_REPLY==='429'?429:200});};
  try {await opts.fetch('https://api.openai.com/v1/responses');} catch {return {stopReason:'error'};}
  const value=process.env.SCORE_TEST_REPLY || JSON.stringify({evaluation_status:'evaluated',change_score:3,
    checked_operations:evidence.operations.map(x=>x.operation_id),changes:[{operation_id:0,before:'previous',after:'new',ambiguity:'uncertain'}],reason:'possible difference',missing_evidence:[]});
  return {stopReason:'stop',content:[{type:'text',text:value}],usage:{input:7,output:3,cacheRead:0,cacheWrite:0}};
 }
 async settleCredentialWrites() {}
}
""")
    calls = tmp_path / "score-calls.log"
    monkeypatch.setenv("MDCOMPARE_PI_SDK_DIR", str(sdk))
    monkeypatch.setenv("SCORE_CALLS", str(calls))
    monkeypatch.setenv("OPENAI_API_KEY", "not-used")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    return calls


def run_score(tmp_path, report, index, *extra):
    return invoke(
        tmp_path,
        "semantic-score",
        "--report",
        report,
        "--diff-index",
        index,
        "--output",
        tmp_path / "scores.json",
        "--request-budget",
        "1",
        *extra,
    )


def test_score_defaults_route_ambiguous_three_to_skip_and_resume_without_replay(
    tmp_path, score_runtime
):
    report, index, sidecar, digest = prepared(tmp_path)
    for _ in range(2):
        result = run_score(tmp_path, report, index)
        assert result.returncode == 0, result.stderr
    assert score_runtime.read_text().splitlines() == ["submitted"]
    scores = tmp_path / "scores.json"
    saved = json.loads(scores.read_text())
    (score,) = saved["scores"]
    assert (
        score["score"],
        score["ambiguous"],
        score["evidence_sha256"],
        score["usage_tokens"],
    ) == (3, True, digest, 10)
    assert route(tmp_path, report, index, scores).returncode == 0
    assert (
        json.loads((tmp_path / "route.json").read_text())["results"][0]["action"]
        == "SKIP"
    )
    sidecar.write_bytes(sidecar.read_bytes() + b" ")
    assert run_score(tmp_path, report, index).returncode == 1
    assert score_runtime.read_text().splitlines() == ["submitted"]


@pytest.mark.parametrize(
    ("wire", "reason"),
    [
        ("429", "rate_limited"),
        ("unknown", "unknown_outcome"),
        ('{"score":0,"ambiguous":false}', "invalid_response"),
    ],
)
def test_unscored_failures_keep_compile_and_never_retry(
    tmp_path, score_runtime, monkeypatch, wire, reason
):
    monkeypatch.setenv("SCORE_TEST_REPLY", wire)
    report, index, _, _ = prepared(tmp_path)
    for _ in range(2):
        assert run_score(tmp_path, report, index).returncode == 2
    saved = json.loads((tmp_path / "scores.json").read_text())
    assert saved["scores"] == [] and saved["unscored"][0]["reason"] == reason
    assert score_runtime.read_text().splitlines() == ["submitted"]
    assert route(tmp_path, report, index, tmp_path / "scores.json").returncode == 0
    assert (
        json.loads((tmp_path / "route.json").read_text())["results"][0]["action"]
        == "COMPILE"
    )


def test_oversized_evidence_is_not_sent_or_clipped(tmp_path, score_runtime):
    report, index, _, _ = prepared(tmp_path)
    assert (
        run_score(tmp_path, report, index, "--max-evidence-bytes", "10").returncode == 2
    )
    assert not score_runtime.exists()
    saved = json.loads((tmp_path / "scores.json").read_text())
    assert saved["unscored"][0]["reason"] == "OVERSIZED_EVIDENCE"


def test_budgeted_semantic_scoring_routes_changed_and_resume_verifies_frozen_artifacts(
    tmp_path, score_runtime
):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    seed_body_baseline(tmp_path, config, "# Title\n\nPrevious rule.\n")
    config.write_text(config.read_text() + "\n[semantic_score]\nrequest_budget = 1\n")
    run_path, _, plan = pause(config, differences)
    changed, new = plan["documents"]
    assert (
        changed["semantic_status"],
        changed["action"],
        changed["semantic_score"],
        changed["semantic_ambiguity_advisory"],
    ) == ("CHANGED", "reuse", 3, True)
    assert new["semantic_status"] == "NEW" and new["action"] == "compile"
    assert score_runtime.read_text().splitlines() == ["submitted"]
    route = Path(plan["semantic_scoring"]["route"]["path"])
    route.write_text(route.read_text() + " ")
    retried = cli("apply", "--config", config, "--resume", run_path)
    assert (
        retried.returncode == 1
        and "frozen semantic scoring artifacts changed" in retried.stderr
    )
    assert score_runtime.read_text().splitlines() == ["submitted"]


def test_score_rejects_incomplete_coverage_and_preserves_reply(
    tmp_path, score_runtime, monkeypatch
):
    report, index, _, _ = prepared(tmp_path)
    value = {
        "evaluation_status": "evaluated",
        "change_score": 1,
        "checked_operations": [],
        "changes": [],
        "reason": "no change",
        "missing_evidence": [],
    }
    monkeypatch.setenv("SCORE_TEST_REPLY", json.dumps(value))
    assert run_score(tmp_path, report, index).returncode == 2
    output = tmp_path / "scores.json"
    scores = json.loads(output.read_text())
    assert scores["scores"] == []
    assert scores["unscored"][0]["reason"] == "invalid_response"
    journal = json.loads(output.with_suffix(".json.execution.json").read_text())
    assert json.loads(journal["attempts"][0]["reply"]) == value


def test_retention_candidate_is_context_not_a_second_operation_target():
    from mdcompare.semantic_context import prepare

    def unit(text):
        return {"kind": "paragraph", "context": [], "key": text, "text": text}

    old = [unit("Old surrounding rule"), unit("Threshold is five")]
    new = [
        unit("New surrounding rule"),
        unit("Global: Threshold is five; expiry is Tuesday"),
    ]
    packet = prepare(
        {
            "source_inputs": {"baseline": {}, "incoming": {}},
            "old_units": old,
            "new_units": new,
            "changes": [
                {"operation": "modify", "old_ref": 0, "new_ref": 0},
                {"operation": "delete", "old_ref": 1, "new_ref": None},
                {"operation": "add", "old_ref": None, "new_ref": 1},
            ],
        }
    )
    assert packet["operations"][1] == {
        "operation": "delete",
        "operation_id": 1,
        "old_ref": 1,
        "new_ref": None,
        "retained_text_candidate_new_ref": 1,
    }
    assert packet["new_units"][1]["text"] == new[1]["text"]
