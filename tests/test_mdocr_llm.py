import os
import shutil

import pytest

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
   assert.equal(context.systemPrompt, 'prompt');
   const image = context.messages[0].content[2];
   assert.equal(image.mimeType, 'image/png');
   assert.equal(Buffer.from(image.data, 'base64').toString(), 'fake image bytes');
   globalThis.fetch = async () => {
     if(process.env.FAKE_PROVIDER_OUTCOME === 'unknown') throw new TypeError('socket closed');
     return new Response('{}', {status: Number(process.env.FAKE_PROVIDER_OUTCOME || 200)});
   };
   let status;
   try { status = (await options.fetch('https://api.openai.com/v1/responses')).status; }
   catch { return {stopReason: 'error'}; }
   if (status !== 200) return {stopReason: 'error'};
   const patch = process.env.FAKE_PATCH ? JSON.parse(process.env.FAKE_PATCH) : {kind:'text', markdown:'Faithful'};
   return {stopReason:'stop', content:[{type:'text',text:JSON.stringify({images:[{id:'image-1', uncertainty:'', ...patch}]})}], usage:{input:4,output:3,cacheRead:0,cacheWrite:0}};
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
    img = tmp_path / "image.png"
    img.write_bytes(b"fake image bytes")
    return model.convert(
        "before ![x](image-1) after", [{"id": "image-1", "path": str(img)}], "prompt"
    )


def test_bridge_submits_exact_images_to_allowed_oauth_model(model, tmp_path):
    assert model.preflight()["model"] == "openai/gpt-6.1-terra"
    assert convert(model, tmp_path) == {
        "patches": [
            {"id": "image-1", "kind": "text", "markdown": "Faithful", "uncertainty": ""}
        ],
        "usage_tokens": 7,
    }


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
def test_real_mermaid_parser_accepts_and_rejects_diagrams(model, tmp_path, monkeypatch):
    import json

    monkeypatch.setenv(
        "FAKE_PATCH",
        json.dumps(
            {"kind": "mixed", "markdown": "```mermaid\nflowchart TD\n A --> B\n```"}
        ),
    )
    assert convert(model, tmp_path)["patches"][0]["kind"] == "mixed"
    monkeypatch.setenv(
        "FAKE_PATCH",
        json.dumps(
            {"kind": "mixed", "markdown": "```mermaid\nflowchart TD\n A -->\n```"}
        ),
    )
    with pytest.raises(LlmError, match="syntactically invalid Mermaid"):
        convert(model, tmp_path)


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
