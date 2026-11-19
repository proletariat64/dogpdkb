"""Environment diagnostics are local, redacted, and backend-specific."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _doctor(tmp_path, tool, *args):
    entry = "mdocr.cli" if tool == "mdocr" else "mdcompare"
    return subprocess.run(
        [sys.executable, "-m", entry, "doctor", "--json", *args],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1"),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )


def test_offline_and_paddle_modes_do_not_need_node(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("PADDLEOCR_TOKEN", raising=False)
    before = set(tmp_path.iterdir())
    for tool, options, expected in (
        ("mdcompare", [], 0),
        ("mdocr", ["--mode", "prepare"], 0),
        ("mdocr", [], 2),
    ):
        response = _doctor(tmp_path, tool, *options)
        report = json.loads(response.stdout)
        assert response.returncode == expected, response.stderr
        assert report["schema"] == f"{tool}-doctor-1"
        assert report["network_checked"] is False
        assert report["ok"] is (expected == 0)
    monkeypatch.setenv("PADDLEOCR_TOKEN", "synthetic-token-do-not-print")
    response = _doctor(tmp_path, "mdocr")
    assert (
        response.returncode == 0
        and "synthetic-token" not in response.stdout + response.stderr
    )
    for tool in ("mdcompare", "mdocr"):
        failed = _doctor(tmp_path, tool, "--mode", "llm")
        assert failed.returncode == 2
        assert json.loads(failed.stdout)["checks"][-1]["code"] == "node_unavailable"
    assert set(tmp_path.iterdir()) == before


@pytest.fixture
def fake_pi(tmp_path, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("requires Node")
    sdk = tmp_path / "pi"
    (sdk / "dist").mkdir(parents=True)
    (sdk / "package.json").write_text(
        '{"name":"@earendil-works/pi-coding-agent","type":"module"}'
    )
    (sdk / "dist/index.js").write_text("""
import assert from 'node:assert/strict';
export function readStoredCredential(provider) {
  assert.equal(provider, 'openai-codex');
  if (process.env.DOCTOR_NO_OAUTH) return {type:'api_key',key:'do-not-print-api-key'};
  return {type:'oauth',access:'do-not-print-access',refresh:'do-not-print-refresh',expires:1};
}
export class ModelRuntime {
  static async create(opts) {
    assert.equal(opts.allowModelNetwork,false);
    assert.equal(opts.refreshOnCreate,false);
    assert.equal(opts.authPath,undefined);
    assert.equal(await opts.credentials.read(),undefined);
    assert.throws(() => fetch('https://example.invalid'));
    return new ModelRuntime();
  }
  getPhysicalModel(provider,id) {
    assert.equal(id,'fixture-model');
    return {provider,id,api:'openai-codex-responses',input:['text','image'],reasoning:true,thinkingLevelMap:{low:'low'}};
  }
}
""")
    for name in ("MDOCR_PI_SDK_DIR", "MDCOMPARE_PI_SDK_DIR"):
        monkeypatch.setenv(name, str(sdk))
    for name in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "absent-auth-home"))
    monkeypatch.delenv("MDOCR_NODE_DEPENDENCIES", raising=False)


def test_local_pi_model_and_credentials_without_refresh(tmp_path, monkeypatch, fake_pi):
    options = (
        "--mode",
        "llm",
        "--model",
        "openai-codex/fixture-model",
        "--effort",
        "low",
    )
    response = _doctor(tmp_path, "mdcompare", *options)
    assert response.returncode == 0, response.stderr
    assert json.loads(response.stdout)["checks"][-1]["code"] == "oauth_expired"
    assert "do-not-print" not in response.stdout + response.stderr
    response = _doctor(tmp_path, "mdocr", *options)
    assert response.returncode == 2
    assert (
        json.loads(response.stdout)["checks"][-1]["code"]
        == "mermaid_parser_unavailable"
    )
    monkeypatch.setenv("DOCTOR_NO_OAUTH", "1")
    monkeypatch.setenv("CODEX_API_KEY", "do-not-print-ambient")
    for tool in ("mdcompare", "mdocr"):
        response = _doctor(tmp_path, tool, *options)
        assert response.returncode == 2
        assert json.loads(response.stdout)["checks"][-1]["code"] == "oauth_required"
        assert "do-not-print" not in response.stdout + response.stderr
    assert not (tmp_path / "absent-auth-home").exists()


@pytest.mark.parametrize("tool", ["mdcompare", "mdocr"])
def test_unimportable_pillow_is_reported_without_traceback(tmp_path, tool):
    (tmp_path / "PIL.py").write_text("raise ImportError('internal dependency error')\n")
    options = ["--mode", "prepare"] if tool == "mdocr" else []
    result = _doctor(tmp_path, tool, *options)
    assert result.returncode == 2, result.stderr
    pillow = next(
        check
        for check in json.loads(result.stdout)["checks"]
        if check["name"] == "pillow"
    )
    assert pillow["code"] == "dependency_unavailable"
    assert "Traceback" not in result.stderr
