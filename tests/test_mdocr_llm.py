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
import {writeFileSync} from 'node:fs';
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
   const image = context.messages[0].content[2];
   assert.equal(image.mimeType, 'image/png');
   const bytes = Buffer.from(image.data, 'base64');
   if (process.env.FAKE_ATTACHMENT) {
     writeFileSync(process.env.FAKE_ATTACHMENT, bytes);
   } else {
     assert.equal(context.systemPrompt, 'prompt');
     assert.deepEqual(bytes, Buffer.from(process.env.FAKE_PNG_HEX, 'hex'));
   }
   globalThis.fetch = async () => {
     if(process.env.FAKE_PROVIDER_OUTCOME === 'unknown') throw new TypeError('socket closed');
     return new Response('{}', {status: Number(process.env.FAKE_PROVIDER_OUTCOME || 200)});
   };
   let status;
   try { status = (await options.fetch('https://api.openai.com/v1/responses')).status; }
   catch { return {stopReason: 'error'}; }
   if (status !== 200) return {stopReason: 'error'};
   const patch = process.env.FAKE_PATCH ? JSON.parse(process.env.FAKE_PATCH) : {kind:'text', markdown:'Faithful'};
   const id = context.messages[0].content[1].text.split(': ').at(-1);
   return {stopReason:'stop', content:[{type:'text',text:JSON.stringify({images:[{id, uncertainty:'', ...patch}]})}], usage:{input:4,output:3,cacheRead:0,cacheWrite:0}};
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
def test_real_mermaid_parser_accepts_and_rejects_diagrams(
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
    assert convert(model, tmp_path)["patches"][0]["kind"] == "mixed"
    monkeypatch.setenv(
        "FAKE_PATCH",
        json.dumps(
            {
                "kind": "mixed",
                "markdown": f"{fence}mermaid\nflowchart TD\n A -->\n{fence}",
            }
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


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "wrong", "kind": "text", "markdown": "X"},
        {"kind": "table", "markdown": "<table><tr><td>X</td></tr></table>"},
        {"kind": "table", "markdown": "| A | B |\n| --- | --- |\n| X |"},
    ],
)
def test_rejected_patches_preserve_reported_usage(model, tmp_path, monkeypatch, patch):
    import json

    monkeypatch.setenv("FAKE_PATCH", json.dumps(patch))
    with pytest.raises(LlmError) as exc:
        convert(model, tmp_path)
    assert exc.value.reason == "invalid_response"
    assert exc.value.usage_tokens == 7


def test_markdown_table_accepts_escaped_pipe_in_cell(model, tmp_path, monkeypatch):
    import json

    markdown = "| A | B |\n| --- | :---: |\n| X \\| Y | Z |"
    monkeypatch.setenv(
        "FAKE_PATCH", json.dumps({"kind": "table", "markdown": markdown})
    )
    assert convert(model, tmp_path)["patches"][0]["markdown"] == markdown
