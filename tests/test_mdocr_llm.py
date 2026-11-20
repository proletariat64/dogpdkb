import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

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
   const [image] = message.content;
   assert.equal(message.content.length, 1);
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
     const outcome = record?.outcome || process.env.FAKE_PROVIDER_OUTCOME || 200;
     if (outcome === 'unknown') throw new TypeError('socket closed');
     return new Response('{}', {status: Number(outcome)});
   };
   let status;
   try { status = (await options.fetch('https://api.openai.com/v1/responses')).status; }
   catch { return {stopReason: 'error'}; }
   finally { if (record) appendFileSync(trace, `end ${record.name}\n`); }
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
   const patch = process.env.FAKE_PATCH ? JSON.parse(process.env.FAKE_PATCH) : {kind:'text', markdown:record?.name || 'Faithful'};
   return {stopReason:'stop', content:[{type:'text',text:JSON.stringify({uncertainty:'', ...patch})}], usage:{input:4,output:3,cacheRead:0,cacheWrite:0}};
 }
 async settleCredentialWrites() {}
}
""")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
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


def plan_batch(tmp_path, name, sources, budget):
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


@pytest.mark.skipif(
    not os.environ.get("MDOCR_NODE_DEPENDENCIES"), reason="requires real parser"
)
@pytest.mark.parametrize("fence", ["```", "````", "~~~"])
def test_real_mermaid_parser_warns_without_changing_content(
    model, tmp_path, monkeypatch, fence
):
    import json

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
def test_mermaid_warning_publishes_and_survives_cache_reuse(
    model, tmp_path, monkeypatch
):
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
