"""Public semantic-score CLI with a local fake Pi SDK and zero network calls."""

import fcntl
import json
import shutil
import signal
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
import { appendFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { setTimeout } from 'node:timers/promises';

const marker = (kind, id) => `${process.env.SCORE_CALLS}.${kind}.${id}`;
async function until(predicate) {
  for (let i=0; i<250; i++) {
    if (predicate()) return;
    await setTimeout(20);
  }
  throw Error('synthetic provider barrier did not open');
}
async function waveStatus(evidence) {
  if (!process.env.SCORE_WAVES) return {};
  const waves = JSON.parse(process.env.SCORE_WAVES);
  const id = evidence.new_units.map(x=>x.text).join(' ').match(/case-(\\d+)/)[1];
  const wave = waves.findIndex(group => group.includes(id));
  assert.notEqual(wave, -1);
  for (const previous of waves.slice(0, wave).flat()) {
    assert.ok(existsSync(marker('finished', previous)), 'must drain old tier');
  }
  const journal = JSON.parse(readFileSync(process.env.SCORE_JOURNAL, 'utf8'));
  assert.equal(journal.attempts.find(row=>row.document_id===id)?.status,'admitted');
  writeFileSync(marker('started', id), '');
  await until(() => waves[wave].every(item => existsSync(marker('started',item))));
  const rates = JSON.parse(process.env.SCORE_429 || '[]');
  if (rates.length && !rates.includes(id)) {
    await until(() => JSON.parse(readFileSync(process.env.SCORE_JOURNAL, 'utf8')).concurrency === 2-wave);
  }
  writeFileSync(marker('finished', id), '');
  if (id === process.env.SCORE_CRASH_AFTER_DRAIN_START) {
    process.kill(process.ppid, 'SIGKILL');
    process.exit(0);
  }
  return {status:rates.includes(id) ? 429 : 200};
}
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
    if(process.env.SCORE_TEST_REPLY==='crash') {process.kill(process.ppid,'SIGKILL'); process.exit(0);}
    if(process.env.SCORE_TEST_REPLY==='unknown') throw TypeError('synthetic disconnect');
    const {status} = await waveStatus(evidence);
    return new Response('{}',{status:status ?? (process.env.SCORE_TEST_REPLY==='429'?429:200)});};
  let response;
  try {response=await opts.fetch('https://api.openai.com/v1/responses');} catch {return {stopReason:'error'};}
  if (response.status === 429 && process.env.SCORE_429) throw Error('synthetic SDK error');
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


def changed_batch(tmp_path, size):
    manifests = []
    for side, text in (
        ("old", "Rule expires in April"),
        ("new", "Rule lasts until May"),
    ):
        documents = []
        for n in range(1, size + 1):
            body = tmp_path / f"{side}-{n}.md"
            body.write_text(f"case-{n}: {text}.\n")
            documents.append(
                {
                    "knowledge_base": "book",
                    "document_id": str(n),
                    "content_kind": "raw",
                    "path": str(body),
                    "metadata": {
                        "name": "Rule",
                        "logical_path": "",
                        "source_ref": f"book:{n}",
                    },
                    "resources": [],
                }
            )
        path = tmp_path / f"{side}.json"
        path.write_text(json.dumps({"schema_version": "1.0", "documents": documents}))
        manifests.append(path)
    report, index = tmp_path / "semantic.json", tmp_path / "diff.json"
    result = invoke(
        tmp_path,
        "semantic",
        "--baseline",
        manifests[0],
        "--incoming",
        manifests[1],
        "--output",
        report,
        "--diff-output",
        index,
    )
    assert result.returncode == 0, result.stderr
    return report, index


def run_score(tmp_path, report, index, *extra, budget=1):
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
        str(budget),
        *extra,
    )


def test_score_default_overlaps_three_requests_without_exceeding_budget(
    tmp_path, score_runtime, monkeypatch
):
    report, index = changed_batch(tmp_path, 4)
    monkeypatch.setenv("SCORE_WAVES", json.dumps([["1", "2", "3"]]))
    monkeypatch.setenv("SCORE_JOURNAL", str(tmp_path / "scores.json.execution.json"))
    result = run_score(tmp_path, report, index, budget=3)
    assert result.returncode == 2, result.stderr
    output = tmp_path / "scores.json"
    original = output.read_bytes()
    saved = json.loads(original)
    assert [row["document_id"] for row in saved["scores"]] == ["1", "2", "3"]
    assert [(row["document_id"], row["reason"]) for row in saved["unscored"]] == [
        ("4", "REQUEST_BUDGET_EXHAUSTED")
    ]
    assert run_score(tmp_path, report, index, budget=3).returncode == 2
    changed_budget = run_score(tmp_path, report, index, budget=4)
    assert changed_budget.returncode == 1, changed_budget.stderr
    assert "SCORE_EXECUTION_BINDING_INVALID" in changed_budget.stderr
    assert output.read_bytes() == original
    assert score_runtime.read_text().splitlines() == ["submitted"] * 3


def test_zero_budget_never_admits_an_eligible_change(tmp_path, score_runtime):
    report, index, _, _ = prepared(tmp_path)
    result = run_score(tmp_path, report, index, budget=0)
    assert result.returncode == 2, result.stderr
    saved = json.loads((tmp_path / "scores.json").read_text())
    assert saved["execution"]["requests"] == 0
    assert saved["scores"] == []
    assert saved["unscored"][0]["reason"] == "REQUEST_BUDGET_EXHAUSTED"
    assert not score_runtime.exists()


def test_score_drains_each_rate_limited_tier_and_stops_at_one(
    tmp_path, score_runtime, monkeypatch
):
    report, index = changed_batch(tmp_path, 7)
    monkeypatch.setenv("SCORE_WAVES", json.dumps([["1", "2", "3"], ["4", "5"], ["6"]]))
    monkeypatch.setenv("SCORE_429", json.dumps(["1", "2", "4", "6"]))
    monkeypatch.setenv("SCORE_JOURNAL", str(tmp_path / "scores.json.execution.json"))
    for _ in range(2):
        result = run_score(tmp_path, report, index, budget=7)
        assert result.returncode == 2, result.stderr
    saved = json.loads((tmp_path / "scores.json").read_text())
    assert saved["execution"]["stop"] == "rate_limited"
    assert saved["execution"]["concurrency"] == 1
    assert [row["document_id"] for row in saved["scores"]] == ["3", "5"]
    assert [(row["document_id"], row["reason"]) for row in saved["unscored"]] == [
        (str(n), "rate_limited") for n in (1, 2, 4, 6, 7)
    ]
    assert score_runtime.read_text().splitlines() == ["submitted"] * 6


def wait_for_orphaned_bridges(tmp_path):
    with (tmp_path / "scores.json.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)


def test_score_resume_uses_persisted_lower_tier_after_interrupted_drain(
    tmp_path, score_runtime, monkeypatch
):
    report, index = changed_batch(tmp_path, 6)
    monkeypatch.setenv("SCORE_WAVES", json.dumps([["1", "2", "3"]]))
    monkeypatch.setenv("SCORE_429", json.dumps(["1"]))
    monkeypatch.setenv("SCORE_JOURNAL", str(tmp_path / "scores.json.execution.json"))
    monkeypatch.setenv("SCORE_CRASH_AFTER_DRAIN_START", "2")
    assert run_score(tmp_path, report, index, budget=5).returncode == -signal.SIGKILL
    wait_for_orphaned_bridges(tmp_path)
    monkeypatch.delenv("SCORE_CRASH_AFTER_DRAIN_START")
    monkeypatch.delenv("SCORE_429")
    monkeypatch.setenv("SCORE_WAVES", json.dumps([["4", "5"]]))
    result = run_score(tmp_path, report, index, budget=5)
    assert result.returncode == 2, result.stderr
    saved = json.loads((tmp_path / "scores.json").read_text())
    assert saved["execution"]["concurrency"] == 2
    assert [
        (row["document_id"], row["concurrency"]) for row in saved["scores"][-2:]
    ] == [
        ("4", 2),
        ("5", 2),
    ]
    assert [(row["document_id"], row["reason"]) for row in saved["unscored"][:2]] == [
        ("1", "rate_limited"),
        ("2", "unknown_outcome"),
    ]
    assert saved["unscored"][-1]["reason"] == "REQUEST_BUDGET_EXHAUSTED"
    assert score_runtime.read_text().splitlines() == ["submitted"] * 5


def test_score_resume_never_replays_interrupted_admission_or_refunds_budget(
    tmp_path, score_runtime, monkeypatch
):
    report, index = changed_batch(tmp_path, 3)
    monkeypatch.setenv("SCORE_TEST_REPLY", "crash")
    args = (tmp_path, report, index, "--concurrency", "1")
    assert run_score(*args, budget=2).returncode == -signal.SIGKILL
    wait_for_orphaned_bridges(tmp_path)
    journal = tmp_path / "scores.json.execution.json"
    assert [row["status"] for row in json.loads(journal.read_text())["attempts"]] == [
        "admitted"
    ]
    assert not (tmp_path / "scores.json").exists()
    monkeypatch.delenv("SCORE_TEST_REPLY")
    for _ in range(2):
        assert run_score(*args, budget=2).returncode == 2
    saved = json.loads((tmp_path / "scores.json").read_text())
    assert [row["document_id"] for row in saved["scores"]] == ["2"]
    assert [(row["document_id"], row["reason"]) for row in saved["unscored"]] == [
        ("1", "unknown_outcome"),
        ("3", "REQUEST_BUDGET_EXHAUSTED"),
    ]
    assert score_runtime.read_text().splitlines() == ["submitted"] * 2


def test_score_cannot_restart_when_journal_is_missing_after_admission(
    tmp_path, score_runtime, monkeypatch
):
    report, index = changed_batch(tmp_path, 2)
    args = (tmp_path, report, index, "--concurrency", "1")
    monkeypatch.setenv("SCORE_TEST_REPLY", "crash")
    assert run_score(*args, budget=2).returncode == -signal.SIGKILL
    wait_for_orphaned_bridges(tmp_path)
    (tmp_path / "scores.json.execution.json").unlink()
    assert not (tmp_path / "scores.json").exists()
    monkeypatch.delenv("SCORE_TEST_REPLY")
    result = run_score(*args, budget=2)
    assert result.returncode == 1, result.stderr
    assert "SCORE_EXECUTION_JOURNAL_MISSING" in result.stderr
    assert score_runtime.read_text().splitlines() == ["submitted"]


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


def test_unknown_score_is_not_replayed_but_proven_unsent_peer_can_score_next_run(
    tmp_path, score_runtime, monkeypatch
):
    config, differences = instance(tmp_path)
    two_sources(tmp_path, differences)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for identifier in ("42", "43"):
        (corpus / f"Title-2026-11-11-[{identifier}].md").write_text(
            f"# Title\n\nPrevious rules for document {identifier}.\n"
        )
    initialized = cli("init-state", "--config", config)
    assert initialized.returncode == 0, initialized.stderr
    config.write_text(config.read_text() + "\n[semantic_score]\nrequest_budget=1\n")
    monkeypatch.setenv("SCORE_TEST_REPLY", "unknown")
    _, first, first_plan = pause(config, differences)
    assert [row["action"] for row in first_plan["documents"]] == ["compile", "compile"]
    scores = Path(first["artifacts"]["semantic_scoring"]["scores"]["path"])
    assert {row["reason"] for row in json.loads(scores.read_bytes())["unscored"]} == {
        "unknown_outcome"
    }

    monkeypatch.delenv("SCORE_TEST_REPLY")
    _, _, second_plan = pause(config, differences)
    assert [row.get("semantic_score") for row in second_plan["documents"]] == [None, 3]
    assert [row["action"] for row in second_plan["documents"]] == ["compile", "reuse"]
    assert score_runtime.read_text().splitlines() == ["submitted", "submitted"]

    _, _, third_plan = pause(config, differences)
    assert [row.get("semantic_score") for row in third_plan["documents"]] == [
        None,
        None,
    ]
    assert score_runtime.read_text().splitlines() == ["submitted", "submitted"]


def test_score_process_loss_keeps_compile_without_cross_run_replay(
    tmp_path, score_runtime, monkeypatch
):
    config, differences = instance(tmp_path)
    seed_body_baseline(tmp_path, config, "# Title\n\nPrevious rule.\n")
    config.write_text(config.read_text() + "\n[semantic_score]\nrequest_budget=1\n")
    monkeypatch.setenv("SCORE_TEST_REPLY", "crash")
    _, first, plan = pause(config, differences)
    assert plan["documents"][0]["action"] == "compile"
    assert first["failures"][-1]["reason"] == "SEMANTIC_SCORE_UNVERIFIABLE"

    monkeypatch.delenv("SCORE_TEST_REPLY")
    _, _, plan = pause(config, differences)
    assert plan["documents"][0]["action"] == "compile"
    assert score_runtime.read_text().splitlines() == ["submitted"]


@pytest.mark.parametrize("duplicate_score", [False, True])
def test_score_rejects_ambiguous_wire_encoding_or_incomplete_coverage(
    tmp_path, score_runtime, monkeypatch, duplicate_score
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
    if duplicate_score:
        value["checked_operations"] = [0, 1]  # One addition and one deletion.
    wire = json.dumps(value)
    if duplicate_score:
        wire = '{"change_score":5,' + wire[1:]
    monkeypatch.setenv("SCORE_TEST_REPLY", wire)
    assert run_score(tmp_path, report, index).returncode == 2
    output = tmp_path / "scores.json"
    scores = json.loads(output.read_text())
    assert scores["scores"] == []
    assert scores["unscored"][0]["reason"] == "invalid_response"
    journal = json.loads(output.with_suffix(".json.execution.json").read_text())
    assert journal["attempts"][0]["reply"] == wire


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


@pytest.mark.parametrize("fail_sync", [False, True])
def test_score_admission_syncs_directory_before_provider(
    tmp_path, monkeypatch, fail_sync
):
    import os
    import stat

    from mdcompare import semantic_score
    from mdcompare.cli import main

    report, index, _, _ = prepared(tmp_path)
    output = tmp_path / "scores.json"
    journal = tmp_path / "scores.json.execution.json"
    original_sync = os.fsync
    synced = False
    sent = False

    def fsync(fd):
        nonlocal synced
        if stat.S_ISDIR(os.fstat(fd).st_mode) and not synced:
            if fail_sync:
                raise OSError("synthetic crash-durability failure")
            assert (
                json.loads(journal.read_text())["attempts"][0]["status"] == "admitted"
            )
            synced = True
        original_sync(fd)

    def assess(*_):
        nonlocal sent
        assert synced
        sent = True
        return {"ok": False, "reason": "unknown_outcome"}

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(semantic_score, "_assess", assess)
    args = [
        "semantic-score",
        "--report",
        str(report),
        "--diff-index",
        str(index),
        "--output",
        str(output),
        "--request-budget",
        "1",
    ]
    assert main(args) == (1 if fail_sync else 2)
    assert sent is not fail_sync
    if not fail_sync:
        assert main(args) == 2
