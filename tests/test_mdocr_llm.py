import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from mdocr import llm
from mdocr.llm import LlmClient, LlmError

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="optional Node bridge"
)


@pytest.fixture
def model(tmp_path, monkeypatch):
    sdk = tmp_path / "sdk"
    (sdk / "dist").mkdir(parents=True)
    (sdk / "package.json").write_text(
        '{"type":"module","name":"@earendil-works/pi-coding-agent"}'
    )
    (sdk / "dist/index.js").write_text(r"""
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {appendFileSync, readFileSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
export class ModelRuntime {
 static async create(options) {
   assert.equal(options.authPath, join(process.env.PI_CODING_AGENT_DIR, 'auth.json'));
   assert.equal(options.modelsPath, join(process.env.PI_CODING_AGENT_DIR, 'models.json'));
   assert.equal(process.env.OPENAI_API_KEY, undefined);
   return new ModelRuntime();
 }
 getPhysicalModel(provider, id) { return {provider, id, api: 'openai-responses', input: ['text', 'image'], reasoning: true}; }
 isUsingOAuth() { return true; }
 async checkAuth() { return {source: 'OAuth', type: 'oauth'}; }
 async completeSimple(model, context, options) {
   assert.equal(model.id, 'gpt-6.1-terra');
   assert.equal(options.reasoning, 'medium');
   assert.equal(options.maxRetries, 0);
   assert.equal(options.transport, 'sse');
   const payload = options.onPayload({});
   assert.equal(payload.max_output_tokens, 8192);
   assert.equal(payload.text.format.strict, true);
   assert.equal(payload.text.format.type, 'json_schema');
   const [message] = context.messages;
   assert.equal(context.messages.length, 1);
   const [image, repairPart] = message.content;
   const repairMode = repairPart !== undefined;
   assert.equal(message.content.length, repairMode ? 2 : 1);
   if (repairMode) {
     assert.equal(repairPart.type, 'text');
     assert.ok(context.systemPrompt.includes('Mermaid repair only'));
     const prior = JSON.parse(repairPart.text);
     assert.ok(prior.markdown.includes('A -->'));
     assert.ok(prior.errors.some(e => e.block === 1 && /parse error on line/i.test(e.message)));
   }
   assert.equal(image.type, 'image');
   assert.equal(image.mimeType, 'image/png');
   assert.equal(payload.text.format.schema.additionalProperties, false);
   assert.deepEqual(payload.text.format.schema.required, ['kind', 'markdown', 'uncertainty']);
   const bytes = Buffer.from(image.data, 'base64');
   const digest = createHash('sha256').update(bytes).digest('hex');
   const cases = process.env.FAKE_OCR_CASES && JSON.parse(process.env.FAKE_OCR_CASES);
   const trace = process.env.FAKE_OCR_TRACE;
   const record = cases?.[digest];
   if (cases) {
     assert.ok(record, 'expected exact bytes from one planned image');
     assert.ok(!context.systemPrompt.includes('PRIVATE_BODY'));
     appendFileSync(trace, `begin ${record.name}\n`);
     if (record.barrier) {
       const expires = Date.now() + 5000;
       while (readFileSync(trace, 'utf8').split('\n').filter(line => /^begin [abc]1$/.test(line)).length < 3) {
         if (Date.now() >= expires) throw new Error('Three documents did not overlap');
         await delay(10);
       }
     }
   }
   if (process.env.FAKE_ATTACHMENT) {
     writeFileSync(process.env.FAKE_ATTACHMENT, bytes);
   } else if (!cases) {
     assert.equal(context.systemPrompt, 'prompt');
     assert.deepEqual(bytes, Buffer.from(process.env.FAKE_PNG_HEX, 'hex'));
   }
   globalThis.fetch = async () => {
     const outcome = (repairMode && process.env.FAKE_REPAIR_OUTCOME) || record?.outcome || process.env.FAKE_PROVIDER_OUTCOME || 200;
     const number = Array.isArray(outcome)
       ? readFileSync(trace, 'utf8').split('\n').filter(x => x === `begin ${record.name}`).length
       : 0;
     const current = number ? outcome[Math.min(number - 1, outcome.length - 1)] : outcome;
     if (current === 'connection') throw new TypeError('fetch failed', {cause: {code: 'ECONNREFUSED'}});
     if (current === 'complete_stream') {
       return new Response('data: [DONE]\n\n', {status: 200, headers: {'content-type': 'text/event-stream'}});
     }
     if (current === 'dropped_stream' || current === 'invalid_stream') {
       const body = new ReadableStream({
         pull(controller) {
           const cause = current === 'dropped_stream' ? {code: 'UND_ERR_SOCKET'} : undefined;
           controller.error(new TypeError('stream ended', {cause}));
         },
       });
       return new Response(body, {status: 200, headers: {'content-type': 'text/event-stream'}});
     }
     if (current === 'unknown') throw new TypeError('socket closed');
     return new Response('{}', {status: Number(current)});
   };
   let status;
   try {
     const response = await options.fetch('https://api.openai.com/v1/responses');
     status = response.status;
     if (response.headers.get('content-type') === 'text/event-stream') {
       const reader = response.body.getReader();
       const first = await reader.read();
       assert.equal(new TextDecoder().decode(first.value), 'data: [DONE]\n\n');
       assert.equal((await reader.read()).done, true);
     }
   }
   catch { return {stopReason: 'error'}; }
   finally { if (record) appendFileSync(trace, `end ${record.name}\n`); }
   if (repairMode && process.env.FAKE_INTERRUPT_REPAIR) {
     process.kill(process.ppid, 'SIGKILL');
     return {stopReason: 'error'};
   }
   if (status !== 200) return {stopReason: 'error'};
   if (process.env.FAKE_WIRE) {
     const wire = process.env.FAKE_WIRE;
     process.stdout.write(wire, () => process.exit(JSON.parse(wire)?.ok === false ? 1 : 0));
     await new Promise(() => {});
   }
   const usage = {input:4,output:3,cacheRead:2,cacheWrite:1};
   if (process.env.FAKE_REPLY) {
     const {events, ...reply} = JSON.parse(process.env.FAKE_REPLY);
     for (const event of events || []) await options.onProviderStreamEvent?.(event);
     return {usage, ...reply};
   }
   const patchText = (repairMode && process.env.FAKE_REPAIR_PATCH) || process.env.FAKE_PATCH;
   const patch = patchText ? JSON.parse(patchText) : {kind:'text', markdown:record?.name || 'Faithful'};
   return {stopReason:'stop', content:[{type:'text',text:JSON.stringify({uncertainty:'', ...patch})}], usage:{input:4,output:3,cacheRead:0,cacheWrite:0}};
 }
 async settleCredentialWrites() {}
}
""")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("MDOCR_MERMAID_REPAIR_ROUNDS", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-forwarded")
    monkeypatch.setenv("MDOCR_PI_SDK_DIR", str(sdk))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    return LlmClient("openai/gpt-6.1-terra", "medium")


def convert(model, tmp_path):
    model.env = os.environ.copy()
    img = tmp_path / "image.opaque"
    Image.new("RGB", (8, 8), "white").save(img, "PNG")
    model.env["FAKE_PNG_HEX"] = img.read_bytes().hex()
    return model.convert(img.read_bytes(), "prompt")


def test_bridge_submits_exact_images_to_allowed_oauth_model(model, tmp_path):
    assert model.preflight()["model"] == "openai/gpt-6.1-terra"
    assert convert(model, tmp_path) == {
        "image": {"kind": "text", "markdown": "Faithful", "uncertainty": ""},
        "usage_tokens": 7,
    }


@pytest.mark.skipif(
    not any(
        shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser")
    ),
    reason="requires sandboxed Chromium renderer",
)
def test_cli_svg_plan_and_result_bind_original_not_attachment(
    model, tmp_path, monkeypatch
):
    # Use the console entry point installed in the test interpreter's environment.
    console = os.environ.get("MDOCR_TEST_CONSOLE") or str(
        Path(sys.executable).with_name("mdocr")
    )
    attachment = tmp_path / "provider.png"
    monkeypatch.setenv("FAKE_ATTACHMENT", str(attachment))

    def cli(*args):
        return subprocess.run(
            [console, *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    source = tmp_path / "diagram.svg"
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 380 180">
        <rect width="380" height="180" fill="red"/>
        <foreignObject x="30" y="15" width="180" height="130">
          <div xmlns="http://www.w3.org/1999/xhtml" style="background:lime;height:100%">Title</div>
        </foreignObject>
    </svg>"""
    source.write_bytes(svg)
    markdown = b"# Body\r\n\r\n![diagram](diagram.svg)\r\n"
    (tmp_path / "original.md").write_bytes(markdown)
    plan = cli(
        "plan",
        "--mode",
        "llm",
        "--input",
        "original.md",
        "--output",
        "out.md",
        "--plan",
        "plan.json",
    )
    assert plan.returncode == 0, plan.stderr
    plan_bytes = (tmp_path / "plan.json").read_bytes()
    (document,) = json.loads(plan_bytes)["documents"]
    (planned,) = document["images"]
    sha = hashlib.sha256(svg).hexdigest()
    assert not attachment.exists()
    assert planned["sha256"] == sha
    assert planned["source_path"] == str(source)

    run = cli("run", "--plan", "plan.json", "--result", "result.json")
    result = json.loads((tmp_path / "result.json").read_bytes())
    assert run.returncode == 0, (run.stderr, result)
    assert result["plan_sha256"] == hashlib.sha256(plan_bytes).hexdigest()
    (completed,) = result["documents"]
    assert completed["status"] == "completed"
    context = [hashlib.sha256(markdown).hexdigest(), [[planned["id"], sha]]]
    context_bytes = (json.dumps(context, indent=2) + "\n").encode()
    assert (
        completed["ocr"]["context_sha256"] == hashlib.sha256(context_bytes).hexdigest()
    )
    (evidence,) = completed["ocr"]["images"]
    assert evidence["source_sha256"] == sha
    assert evidence["source_path"] == str(source)
    assert evidence["resource_id"] == "diagram.svg"
    assert source.read_bytes() == svg
    assert (tmp_path / "original.md").read_bytes() == markdown
    output = (tmp_path / "out.md").read_bytes()
    assert b"diagram.svg" in output
    assert f"<!-- mdocr:meta mode=llm sha256={sha} -->".encode() in output
    png = attachment.read_bytes()
    assert hashlib.sha256(png).hexdigest() != sha
    with Image.open(io.BytesIO(png)) as image:
        assert (image.format, image.size) == ("PNG", (380, 180))
        rgb = image.convert("RGB")
        assert rgb.getpixel((60, 65)) == (0, 255, 0)
        assert rgb.getpixel((260, 100)) == (255, 0, 0)


def batch_cli(tmp_path, *args):
    return subprocess.run(
        [sys.executable, "-m", "mdocr.cli", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )


def fixture_images(tmp_path, monkeypatch, outcomes, barrier=False):
    cases = {}
    for index, (name, outcome) in enumerate(outcomes.items()):
        image = tmp_path / f"{name}.png"
        Image.new("RGB", (8, 8), (index, 11, 30)).save(image)
        cases[hashlib.sha256(image.read_bytes()).hexdigest()] = {
            "name": name,
            "outcome": outcome,
            "barrier": barrier and name in {"a1", "b1", "c1"},
        }
    trace = tmp_path / "trace"
    monkeypatch.setenv("FAKE_OCR_CASES", json.dumps(cases))
    monkeypatch.setenv("FAKE_OCR_TRACE", str(trace))
    return trace


def plan_batch(tmp_path, name, sources, budget, *extra):
    (tmp_path / f"{name}-inputs.json").write_text(json.dumps(sources))
    plan = batch_cli(
        tmp_path,
        "plan",
        "--mode",
        "llm",
        "--list",
        f"{name}-inputs.json",
        "--output-dir",
        f"out-{name}",
        "--plan",
        f"{name}.json",
        "--cache-dir",
        "cache",
        "--request-budget",
        str(budget),
        *extra,
    )
    assert plan.returncode == 0, plan.stderr


def run_batch(tmp_path, name, expected, *options):
    run = batch_cli(
        tmp_path,
        "run",
        "--plan",
        f"{name}.json",
        "--result",
        f"{name}-result.json",
        *options,
    )
    assert run.returncode == expected, (run.stdout, run.stderr)
    return json.loads((tmp_path / f"{name}-result.json").read_bytes())


@pytest.mark.parametrize(
    ("fix", "budget", "extra", "rounds"),
    [
        (0, 10, [], []),
        (2, 10, ["recover", "exhaust", "exhaust"], [1, 1, 2]),
        (2, 5, ["recover"], [1]),
    ],
)
def test_batch_fix_retries_only_eligible_failed_images(
    model, tmp_path, monkeypatch, fix, budget, extra, rounds
):
    initial = ["recover", "exhaust", "lost", "ok"]
    trace = fixture_images(
        tmp_path,
        monkeypatch,
        {"recover": [429, 200], "exhaust": 429, "lost": "unknown", "ok": 200},
    )
    (tmp_path / "a.md").write_text("\n\n".join(f"![x]({name}.png)" for name in initial))
    plan_batch(
        tmp_path, "fixed", ["a.md"], budget, "--fix", str(fix), "--concurrency", "10"
    )
    result = run_batch(tmp_path, "fixed", 2)
    assert result["budget"]["requests"] == 4 + len(rounds)
    assert [a["fix_round"] for a in result["requests"][4:]] == rounds
    assert result["requests"][2]["status"] == "unknown"
    assert trace.read_text().splitlines()[::2] == [
        "begin " + name for name in initial + extra
    ]
    assert run_batch(tmp_path, "fixed", 2) == result
    assert len(trace.read_text().splitlines()) == 2 * (4 + len(rounds))


@pytest.mark.parametrize("fix", [0, 1, 2])
@pytest.mark.parametrize("failure", ["connection", "dropped_stream"])
def test_batch_fix_retries_observed_connection_interruptions(
    model, tmp_path, monkeypatch, fix, failure
):
    trace = fixture_images(tmp_path, monkeypatch, {"diagram": [failure, failure, 200]})
    (tmp_path / "a.md").write_text("![flow](diagram.png)\n")
    plan_batch(tmp_path, "connection", ["a.md"], 4, "--fix", str(fix))
    result = run_batch(tmp_path, "connection", 0 if fix == 2 else 2)
    assert result["budget"]["requests"] == fix + 1
    assert [a.get("fix_round", 0) for a in result["requests"]] == list(range(fix + 1))
    assert all(
        (a["status"], a["reason"]) == ("unknown", "connection_outcome_unknown")
        for a in result["requests"][: min(fix + 1, 2)]
    )
    if fix == 2:
        assert result["requests"][-1]["status"] == "success"
    assert trace.read_text().splitlines() == ["begin diagram", "end diagram"] * (
        fix + 1
    )


def test_each_image_is_private_and_success_survives_unknown_peer(
    model, tmp_path, monkeypatch
):
    trace = fixture_images(tmp_path, monkeypatch, {"a1": "unknown", "a2": 200})
    (tmp_path / "a.md").write_text("PRIVATE_BODY\n\n![x](a1.png)\n\n![y](a2.png)\n")
    plan_batch(tmp_path, "first", ["a.md"], 2)
    result = run_batch(tmp_path, "first", 2)
    assert result["budget"] == {"requests": 2, "tokens": 7, "reserved_tokens": 0}
    (doc,) = result["documents"]
    assert [r["status"] for r in doc["resources"]] == ["unknown", "success"]
    assert doc["output"] is None and "ocr" not in doc
    assert not (tmp_path / "out-first/a.md").exists()
    calls = ["begin a1", "end a1", "begin a2", "end a2"]
    assert trace.read_text().splitlines() == calls
    assert run_batch(tmp_path, "first", 2, "--retry-failed") == result

    # Different body, occurrence ID, source path and resource ID; same image bytes.
    shutil.copyfile(tmp_path / "a2.png", tmp_path / "copy.png")
    (tmp_path / "copy.md").write_text(
        "# Different authored body\n\n![copy](copy.png)\n"
    )
    plan_batch(tmp_path, "cache", ["copy.md"], 0)
    reused = run_batch(tmp_path, "cache", 0)
    assert reused["budget"]["requests"] == 0
    (copied,) = reused["documents"]
    assert copied["resources"][0]["status"] == "reused"
    (evidence,) = copied["ocr"]["images"]
    assert evidence["resource_id"] == "copy.png"
    assert b"\na2\n<!-- mdocr:ocr-end -->" in Path(evidence["artifact"]).read_bytes()
    assert trace.read_text().splitlines() == calls


def test_three_markdown_pipelines_overlap_but_images_within_one_do_not(
    model, tmp_path, monkeypatch
):
    trace = fixture_images(
        tmp_path,
        monkeypatch,
        {name: 200 for name in ("a1", "a2", "b1", "b2", "c1", "c2", "d1")},
        barrier=True,
    )
    for doc in "abcd":
        links = [f"![first]({doc}1.png)"]
        if doc != "d":
            links.append(f"![second]({doc}2.png)")
        (tmp_path / f"{doc}.md").write_text("PRIVATE_BODY\n\n" + "\n\n".join(links))
    plan_batch(tmp_path, "pipelines", [f"{doc}.md" for doc in "abcd"], 7)
    result = run_batch(tmp_path, "pipelines", 0)
    assert result["budget"]["requests"] == 7
    assert result["concurrency"] == {"initial": 3, "final": 3, "transitions": []}
    events = trace.read_text().splitlines()
    assert len(events) == 14
    assert set(events[:3]) == {"begin a1", "begin b1", "begin c1"}
    for doc in "abc":
        assert events.index(f"end {doc}1") < events.index(f"begin {doc}2")
    assert min(events.index(f"end {doc}2") for doc in "abc") < events.index("begin d1")


def test_rate_limit_drains_pipeline_tiers_and_preserves_budget_on_resume(
    model, tmp_path, monkeypatch
):
    trace = fixture_images(
        tmp_path,
        monkeypatch,
        {name: 429 for name in ("a1", "a2", "b1", "b2", "c1", "c2")} | {"d1": 200},
        barrier=True,
    )
    for doc in "abc":
        (tmp_path / f"{doc}.md").write_text(f"![1]({doc}1.png)\n\n![2]({doc}2.png)\n")
    (tmp_path / "d.md").write_text("![1](d1.png)\n")
    plan_batch(tmp_path, "throttle", [f"{doc}.md" for doc in "abcd"], 7)
    result = run_batch(tmp_path, "throttle", 2)
    assert result["concurrency"]["transitions"] == [
        {"from": 3, "to": 2},
        {"from": 2, "to": 1},
    ]
    assert result["budget"]["requests"] == 6
    assert result["causes"] == ["OCR_RATE_LIMITED"]
    events = trace.read_text().splitlines()
    assert set(events[:3]) == {"begin a1", "begin b1", "begin c1"}
    assert set(events[3:6]) == {"end a1", "end b1", "end c1"}
    assert events[-2:] == ["begin c2", "end c2"]
    assert result["documents"][-1]["resources"][0]["status"] == "unattempted"
    resumed = run_batch(tmp_path, "throttle", 2)
    assert resumed["concurrency"]["final"] == 1
    assert resumed["budget"]["requests"] == 7
    assert resumed["documents"][-1]["status"] == "completed"
    assert trace.read_text().splitlines() == [*events, "begin d1", "end d1"]


def test_structured_refusal_is_not_ocr_even_if_text_matches_schema(
    model, tmp_path, monkeypatch
):
    fixture_images(tmp_path, monkeypatch, {"a1": 200})
    (tmp_path / "a.md").write_text("![image](a1.png)\n")
    refusal = json.dumps({"kind": "text", "markdown": "PRIVATE", "uncertainty": ""})
    # The SDK flattens refusal parts into text in its AssistantMessage.
    monkeypatch.setenv(
        "FAKE_REPLY",
        json.dumps(
            {
                "stopReason": "stop",
                "content": [{"type": "text", "text": refusal}],
                "events": [
                    {
                        "type": "response.completed",
                        "response": {
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [
                                        {"type": "refusal", "refusal": refusal}
                                    ],
                                }
                            ],
                        },
                    }
                ],
            }
        ),
    )
    plan_batch(tmp_path, "refusal", ["a.md"], 1)
    result = run_batch(tmp_path, "refusal", 2)
    (attempt,) = result["requests"]
    (doc,) = result["documents"]
    assert (attempt["status"], attempt["reason"], attempt["usage_tokens"]) == (
        "failed",
        "response_refusal",
        10,
    )
    assert doc["resources"][0]["reason"] == "response_refusal"
    assert doc["output"] is None and "ocr" not in doc
    assert "PRIVATE" not in json.dumps(result)
    assert result["budget"]["tokens"] == 10


@pytest.mark.parametrize(
    "wire",
    [
        "[]",
        '{"ok": false, "error": null}',
        '{"ok": true, "image": {}, "usage_tokens": "invalid"}',
    ],
)
def test_malformed_bridge_protocol_after_submission_is_not_retried(
    model, tmp_path, monkeypatch, wire
):
    trace = fixture_images(tmp_path, monkeypatch, {"a1": 200})
    (tmp_path / "a.md").write_text("![image](a1.png)\n")
    monkeypatch.setenv("FAKE_WIRE", wire)
    plan_batch(tmp_path, "wire", ["a.md"], 2)
    result = run_batch(tmp_path, "wire", 2)
    (attempt,) = result["requests"]
    assert (attempt["status"], attempt["reason"]) == ("unknown", "unknown_outcome")
    assert run_batch(tmp_path, "wire", 2, "--retry-failed") == result
    assert trace.read_text().splitlines() == ["begin a1", "end a1"]


def test_passes_completed_stream_bytes_to_sdk(model, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PROVIDER_OUTCOME", "complete_stream")
    assert convert(model, tmp_path)["image"]["markdown"] == "Faithful"


def test_rejects_non_oauth_provider_route_before_request(model):
    model.model = "anthropic/claude"
    with pytest.raises(LlmError, match="allowlisted"):
        model.preflight()


@pytest.mark.parametrize(
    ("wire_status", "status", "reason"),
    [
        ("429", 429, "rate_limited"),
        ("403", 403, "auth_error"),
        ("unknown", None, "unknown_outcome"),
        ("connection", None, "connection_outcome_unknown"),
        ("dropped_stream", None, "connection_outcome_unknown"),
        ("invalid_stream", None, "unknown_outcome"),
    ],
)
def test_preserves_http_status_or_unknown_after_sdk_return(
    model, tmp_path, monkeypatch, wire_status, status, reason
):
    monkeypatch.setenv("FAKE_PROVIDER_OUTCOME", wire_status)
    with pytest.raises(LlmError) as exc:
        convert(model, tmp_path)
    assert (exc.value.status, exc.value.reason) == (status, reason)


def test_missing_parser_is_explicit_for_mixed_patch(model, tmp_path, monkeypatch):
    monkeypatch.delenv("MDOCR_NODE_DEPENDENCIES", raising=False)
    monkeypatch.setenv(
        "FAKE_PATCH",
        '{"kind":"mixed","markdown":"```mermaid\\nflowchart TD\\n A --> B\\n```"}',
    )
    with pytest.raises(LlmError) as exc:
        convert(model, tmp_path)
    assert exc.value.reason == "mermaid_parser_unavailable"


@pytest.mark.parametrize("suffix", ["\u00a0", "\u2028"])
def test_mermaid_closer_rejects_non_commonmark_whitespace(
    model, tmp_path, monkeypatch, suffix
):
    monkeypatch.setenv(
        "FAKE_PATCH",
        json.dumps(
            {
                "kind": "mermaid",
                "markdown": f"```mermaid\nflowchart TD\n A -->\n```{suffix}\nProse",
            }
        ),
    )
    with pytest.raises(LlmError) as exc:
        convert(model, tmp_path)
    assert exc.value.reason == "response_mermaid_fence_invalid"


def test_mermaid_lazy_import_failure_overrides_earlier_syntax_warning(
    model, tmp_path, monkeypatch
):
    packages = tmp_path / "parser" / "node_modules"
    for name, script in (
        ("jsdom", "exports.JSDOM = class { window = { document: {} }; };"),
        (
            "mermaid",
            """module.exports = {
              initialize() {},
              async parse(text, options) {
                try {
                  if (text.startsWith('sequenceDiagram')) await import('./missing.mjs');
                  throw Object.assign(new Error('Syntax error'), {hash: {token: 'EOF'}});
                } catch (error) {
                  if (options?.suppressErrors) return false;
                  throw error;
                }
              }
            };""",
        ),
    ):
        package = packages / name
        package.mkdir(parents=True)
        (package / "index.js").write_text(script)
    monkeypatch.setenv("MDOCR_NODE_DEPENDENCIES", str(packages.parent))
    markdown = "```mermaid\nflowchart TD\n A -->\n```\n\n```mermaid\nsequenceDiagram\n A->>B: Ping\n```"
    monkeypatch.setenv(
        "FAKE_PATCH", json.dumps({"kind": "mixed", "markdown": markdown})
    )
    with pytest.raises(LlmError) as exc:
        convert(model, tmp_path)
    assert exc.value.reason == "mermaid_parser_unavailable"
    assert "missing.mjs" not in str(exc.value)


@pytest.mark.skipif(
    not os.environ.get("MDOCR_NODE_DEPENDENCIES"), reason="requires real parser"
)
@pytest.mark.parametrize("fence", ["```", "````", "~~~"])
def test_managed_mermaid_parser_warns_without_changing_content(
    model, tmp_path, monkeypatch, fence
):
    installed = tmp_path / "managed environment"
    bridge = installed / "lib/python/site-packages/mdocr/pi_bridge.mjs"
    bridge.parent.mkdir(parents=True)
    shutil.copyfile(llm._PI_BRIDGE, bridge)
    packages = Path(os.environ["MDOCR_NODE_DEPENDENCIES"]).resolve() / "node_modules"
    (installed / "node_modules").symlink_to(packages, target_is_directory=True)
    monkeypatch.setattr(llm, "_PI_BRIDGE", bridge)
    monkeypatch.delenv("MDOCR_NODE_DEPENDENCIES")

    monkeypatch.setenv(
        "FAKE_PATCH",
        json.dumps(
            {
                "kind": "mixed",
                "markdown": f"{fence}mermaid\nflowchart TD\n A --> B\n{fence}",
            }
        ),
    )
    assert "warnings" not in convert(model, tmp_path)["image"]
    malformed = f"{fence}mermaid\nflowchart TD\n A -->\n{fence}"
    monkeypatch.setenv(
        "FAKE_PATCH", json.dumps({"kind": "mixed", "markdown": malformed})
    )
    patch = convert(model, tmp_path)["image"]
    assert patch["markdown"] == malformed
    assert patch["warnings"] == ["response_mermaid_syntax_invalid"]


@pytest.mark.skipif(
    not os.environ.get("MDOCR_NODE_DEPENDENCIES"), reason="requires real parser"
)
@pytest.mark.parametrize(
    "diagram",
    [
        "flowchart TD\n A@{shape: invalidshape} --> B",
        "flowchart TD\n A --> B\n linkStyle 2 stroke:red",
        "sequenceDiagram\n participant A\n create participant A\n B->>A: start",
        "sequenceDiagram\n create participant B\n A->>C: start",
        "sequenceDiagram\n participant A\n destroy A\n B->>C: finish",
    ],
)
def test_mermaid_reports_known_invalid_diagram_not_parser_outage(
    model, tmp_path, monkeypatch, diagram
):
    markdown = f"```mermaid\n{diagram}\n```"
    monkeypatch.setenv(
        "FAKE_PATCH", json.dumps({"kind": "mermaid", "markdown": markdown})
    )
    patch = convert(model, tmp_path)["image"]
    assert patch["markdown"] == markdown
    assert patch["warnings"] == ["response_mermaid_syntax_invalid"]


@pytest.mark.skipif(
    not os.environ.get("MDOCR_NODE_DEPENDENCIES"), reason="requires real parser"
)
def test_mermaid_warning_publishes_and_survives_cache_reuse(
    model, tmp_path, monkeypatch
):
    monkeypatch.setenv("MDOCR_MERMAID_REPAIR_ROUNDS", "0")
    malformed = "```mermaid\nflowchart TD\n A -->\n```"
    monkeypatch.setenv(
        "FAKE_PATCH", json.dumps({"kind": "mermaid", "markdown": malformed})
    )
    trace = fixture_images(tmp_path, monkeypatch, {"diagram": 200})
    image = tmp_path / "diagram.png"
    (tmp_path / "a.md").write_text("# Body\n\n![flow](diagram.png)\n")
    for name, budget, status in (("initial", 1, "success"), ("reuse", 0, "reused")):
        plan_batch(tmp_path, name, ["a.md"], budget)
        result = run_batch(tmp_path, name, 0)
        assert (
            result["status"] == "completed" and result["budget"]["requests"] == budget
        )
        (doc,) = result["documents"]
        (resource,) = doc["resources"]
        assert doc["status"] == "completed" and resource["status"] == status
        assert resource["id"] and resource["resource_id"] == "diagram.png"
        assert resource["warnings"] == ["response_mermaid_syntax_invalid"]
        (evidence,) = doc["ocr"]["images"]
        assert (
            evidence["source_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
        )
        assert malformed in Path(evidence["artifact"]).read_text()
        assert malformed in Path(doc["output"]).read_text()
    assert trace.read_text().splitlines() == ["begin diagram", "end diagram"]


@pytest.mark.skipif(
    not os.environ.get("MDOCR_NODE_DEPENDENCIES"), reason="requires real parser"
)
@pytest.mark.parametrize(
    ("repair", "budget", "calls", "last_reason"),
    [
        ("valid", 4, 2, None),
        ("still_invalid", 4, 4, None),
        ("scope_changed", 4, 2, "response_mermaid_repair_scope_changed"),
        ("other_diagram_changed", 4, 2, "response_mermaid_repair_scope_changed"),
        ("unknown", 4, 2, "unknown_outcome"),
        ("valid", 1, 1, None),
    ],
)
def test_mermaid_repair_uses_durable_requests_and_keeps_fallback(
    model, tmp_path, monkeypatch, repair, budget, calls, last_reason
):
    trace = fixture_images(tmp_path, monkeypatch, {"diagram": 200})
    (tmp_path / "a.md").write_text("# Body\n\n![flow](diagram.png)\n")
    initial = (
        "Kept prose.\n\n```mermaid\nflowchart TD\n A -->\n```\n\n"
        "```mermaid\nflowchart LR\n C --> D\n```"
    )
    repaired = initial.replace("A -->", "A --> B")
    if repair == "scope_changed":
        repaired = repaired.replace("Kept prose.", "Invented prose.")
    elif repair == "other_diagram_changed":
        repaired = repaired.replace("C --> D", "C --> E")
    elif repair == "still_invalid":
        repaired = initial
    elif repair == "unknown":
        monkeypatch.setenv("FAKE_REPAIR_OUTCOME", "unknown")
    monkeypatch.setenv("FAKE_PATCH", json.dumps({"kind": "mixed", "markdown": initial}))
    monkeypatch.setenv(
        "FAKE_REPAIR_PATCH", json.dumps({"kind": "mixed", "markdown": repaired})
    )
    plan_batch(tmp_path, "repair", ["a.md"], budget)
    plan = json.loads((tmp_path / "repair.json").read_bytes())
    assert plan["settings"]["mermaid_repair_rounds"] == 3
    monkeypatch.setenv("MDOCR_MERMAID_REPAIR_ROUNDS", "0")  # plan policy is frozen
    result = run_batch(tmp_path, "repair", 0)
    assert result["budget"]["requests"] == calls
    assert [x.get("repair_round", 0) for x in result["requests"]] == list(range(calls))
    assert result["requests"][-1].get("reason") == last_reason
    (doc,) = result["documents"]
    (resource,) = doc["resources"]
    improved = repair == "valid" and budget > 1
    assert ("warnings" in resource) is not improved
    assert (repaired if improved else initial) in Path(doc["output"]).read_text()
    assert "Parse error on line" not in json.dumps(result)
    assert run_batch(tmp_path, "repair", 0, "--retry-failed") == result
    assert trace.read_text().splitlines() == ["begin diagram", "end diagram"] * calls


@pytest.mark.skipif(
    not os.environ.get("MDOCR_NODE_DEPENDENCIES"), reason="requires real parser"
)
def test_interrupted_mermaid_repair_resumes_without_resending(
    model, tmp_path, monkeypatch
):
    trace = fixture_images(tmp_path, monkeypatch, {"diagram": 200})
    (tmp_path / "a.md").write_text("# Body\n\n![flow](diagram.png)\n")
    initial = "```mermaid\nflowchart TD\n A -->\n```"
    monkeypatch.setenv(
        "FAKE_PATCH", json.dumps({"kind": "mermaid", "markdown": initial})
    )
    monkeypatch.setenv("MDOCR_MERMAID_REPAIR_ROUNDS", "3")
    monkeypatch.setenv("FAKE_INTERRUPT_REPAIR", "1")
    plan_batch(tmp_path, "interrupted", ["a.md"], 4)

    interrupted = run_batch(tmp_path, "interrupted", -signal.SIGKILL)
    assert interrupted == json.loads(
        (tmp_path / "interrupted.json.execution.json").read_bytes()
    )
    assert interrupted["status"] == "running"
    first, repair = interrupted["requests"]
    assert first["status"] == "success"
    assert repair["status"] == "admitted" and repair["repair_round"] == 1
    assert interrupted["budget"]["requests"] == 2
    assert trace.read_text().splitlines() == ["begin diagram", "end diagram"] * 2

    monkeypatch.delenv("FAKE_INTERRUPT_REPAIR")
    result = run_batch(tmp_path, "interrupted", 0, "--retry-failed")
    assert result["status"] == "completed"
    assert result["budget"] == interrupted["budget"]
    assert result["requests"] == [
        first,
        dict(repair, status="unknown", reason="OCR_OUTCOME_UNKNOWN"),
    ]
    (doc,) = result["documents"]
    (resource,) = doc["resources"]
    assert resource["warnings"] == ["response_mermaid_syntax_invalid"]
    assert initial in Path(doc["output"]).read_text()
    assert trace.read_text().splitlines() == ["begin diagram", "end diagram"] * 2


@pytest.mark.skipif(
    not os.environ.get("MDOCR_NODE_DEPENDENCIES"), reason="requires real parser"
)
@pytest.mark.parametrize("settled_repairs", [0, 1])
def test_resume_rebuilds_unsent_mermaid_repair(
    model, tmp_path, monkeypatch, settled_repairs
):
    trace = fixture_images(tmp_path, monkeypatch, {"diagram": 200})
    (tmp_path / "a.md").write_text("![flow](diagram.png)\n")
    initial = "```mermaid\nflowchart TD\n A -->\n```"
    monkeypatch.setenv("MDOCR_MERMAID_REPAIR_ROUNDS", "2")
    monkeypatch.setenv(
        "FAKE_PATCH", json.dumps({"kind": "mermaid", "markdown": initial})
    )
    monkeypatch.setenv(
        "FAKE_REPAIR_PATCH", json.dumps({"kind": "mermaid", "markdown": initial})
    )
    plan_batch(tmp_path, "resume", ["a.md"], 3)
    interrupted = subprocess.run(
        [
            sys.executable,
            "-c",
            f"""
import os
from mdocr import cli, runner
original = runner.Execution.dispatch_pending
calls = 0
def interrupted(*args, **kwargs):
    global calls
    original(*args, **kwargs)
    calls += 1
    if calls == {settled_repairs + 1}:
        os._exit(75)
runner.Execution.dispatch_pending = interrupted
cli.main(['run','--plan','resume.json','--result','resume-result.json'])
""",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        timeout=25,
    )
    assert interrupted.returncode == 75, interrupted.stderr
    before = json.loads((tmp_path / "resume.json.execution.json").read_bytes())
    assert [a.get("repair_round", 0) for a in before["requests"]] == list(
        range(settled_repairs + 1)
    )
    assert before["requests"][-1]["status"] == "success"
    fixed = initial.replace("A -->", "A --> B")
    monkeypatch.setenv(
        "FAKE_REPAIR_PATCH", json.dumps({"kind": "mermaid", "markdown": fixed})
    )
    result = run_batch(tmp_path, "resume", 0)
    assert result["requests"][:-1] == before["requests"]
    assert result["requests"][-1]["repair_round"] == settled_repairs + 1
    assert fixed in Path(result["documents"][0]["output"]).read_text()
    assert trace.read_text().splitlines() == ["begin diagram", "end diagram"] * (
        settled_repairs + 2
    )


def test_optional_bridge_usage_may_be_omitted(model, tmp_path, monkeypatch):
    monkeypatch.setenv(
        "FAKE_WIRE",
        json.dumps(
            {
                "ok": True,
                "image": {"kind": "text", "markdown": "Recognized.", "uncertainty": ""},
            }
        ),
    )
    assert convert(model, tmp_path)["usage_tokens"] is None


def test_bridge_honors_https_proxy_without_direct_fallback(
    model, tmp_path, monkeypatch
):
    package = tmp_path / "sdk" / "node_modules" / "undici"
    package.mkdir(parents=True)
    (package / "index.js").write_text("""
const assert = require('node:assert/strict');
exports.ProxyAgent = class {
 constructor(proxy) { assert.equal(proxy, 'http://proxy.invalid:8080'); }
 async destroy() {}
};
exports.fetch = async (url, options) => {
 assert.equal(url.href, 'https://api.openai.com/v1/responses');
 assert.ok(options.dispatcher instanceof exports.ProxyAgent);
 assert.equal(options.redirect, 'error');
 return new Response('{}', {status: 429});
};
""")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    with pytest.raises(LlmError) as exc:
        convert(model, tmp_path)
    assert exc.value.status == 429


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ({"stopReason": "length"}, "response_output_limit"),
        (
            {"stopReason": "error", "rawStopReason": "incomplete.content_filter"},
            "response_content_filter",
        ),
        (
            {"stopReason": "error", "rawStopReason": "incomplete.PRIVATE"},
            "response_incomplete",
        ),
        (
            {"stopReason": "error", "errorMessage": "incomplete: PRIVATE"},
            "unknown_outcome",
        ),
        (
            {"stopReason": "stop", "content": [{"type": "text", "text": "PRIVATE"}]},
            "response_json_invalid",
        ),
    ],
)
def test_provider_stop_and_json_failures_preserve_safe_usage(
    model, tmp_path, monkeypatch, reply, reason
):
    monkeypatch.setenv("FAKE_REPLY", json.dumps(reply))
    with pytest.raises(LlmError) as exc:
        convert(model, tmp_path)
    assert exc.value.reason == reason
    assert exc.value.usage_tokens == 10
    assert "PRIVATE" not in str(exc.value)


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"id": "wrong", "kind": "text", "markdown": "X"}, "response_schema_invalid"),
        (
            {"kind": "table", "markdown": "<table><tr><td>X</td></tr></table>"},
            "response_table_invalid",
        ),
        (
            {"kind": "table", "markdown": "| A | B |\n| --- | --- |\n| X |"},
            "response_table_invalid",
        ),
        (
            {"kind": "mermaid", "markdown": "```mermaid\nA"},
            "response_mermaid_fence_invalid",
        ),
    ],
)
def test_rejected_patches_preserve_reported_usage(
    model, tmp_path, monkeypatch, patch, reason
):
    monkeypatch.setenv("FAKE_PATCH", json.dumps(patch))
    with pytest.raises(LlmError) as exc:
        convert(model, tmp_path)
    assert exc.value.reason == reason
    assert exc.value.usage_tokens == 7


def test_markdown_table_accepts_escaped_pipe_in_cell(model, tmp_path, monkeypatch):
    import json

    markdown = "| A | B |\n| --- | :---: |\n| X \\| Y | Z |"
    monkeypatch.setenv(
        "FAKE_PATCH", json.dumps({"kind": "table", "markdown": markdown})
    )
    assert convert(model, tmp_path)["image"]["markdown"] == markdown
