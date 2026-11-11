"""Unified OCR best-effort stage, through the installed CLI entry (ticket 05).

These tests drive the installed ``dogpdkb sync`` black box with a temporary
corpus, the existing local Yuque proxy double, and an OCR HTTP double injected
through a ``sitecustomize`` shim (no real PaddleOCR call, token, or network).
The shim doubles ``requests.Session`` so both image download and the PaddleOCR
job API are served locally and every billable submission is recorded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_unified_sync import DOCUMENTS, Workspace, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
OCR_SCHEMA = "unified-ocr-1.0"
CONTRACT = "prepared-jpeg-ocr-v1"
IMAGE_URL = "https://cdn.example.invalid/a.png"


def _shim_source(log_path: Path, mode: str) -> str:
    """A sitecustomize that doubles requests.Session for images + PaddleOCR."""
    return f'''
import io, json, time
time.sleep = lambda seconds: None
import requests
from PIL import Image

LOG = {str(log_path)!r}
MODE = {mode!r}

def _png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()

class Resp:
    def __init__(self, status_code=200, json_data=None, text="", content=b""):
        self.status_code = status_code
        self._j = json_data
        self.text = text
        self.content = content
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{{self.status_code}} Error", response=self)
    def json(self):
        return self._j

def _record(kind):
    with open(LOG, "a", encoding="utf-8") as stream:
        stream.write(kind + "\\n")

def _handle(method, url, **kwargs):
    if "paddleocr.aistudio-app.com" in url:
        if method == "POST" and url.endswith("/jobs"):
            _record("POST")
            if MODE == "auth":
                return Resp(status_code=401, text="unauthorized")
            if MODE == "quota":
                return Resp(status_code=403, text="quota exhausted")
            return Resp(json_data={{"data": {{"jobId": "job-1"}}}})
        if method == "GET" and "/jobs/" in url:
            if MODE == "fail":
                return Resp(json_data={{"data": {{"state": "failed", "errorMsg": "boom"}}}})
            return Resp(json_data={{"data": {{"state": "done",
                "resultUrl": {{"jsonUrl": "https://example.com/result.jsonl"}}}}}})
        if url == "https://example.com/result.jsonl":
            if MODE == "blank":
                return Resp(text="")
            payload = {{"result": {{"layoutParsingResults": [
                {{"markdown": {{"text": "OCR output line"}}}}]}}}}
            return Resp(text=json.dumps(payload))
        raise AssertionError(url)
    if url.startswith("https://cdn.example.invalid/"):
        _record("IMG " + url)
        return Resp(content=_png())
    raise AssertionError(f"unexpected {{method}} {{url}}")

def _request(self, method, url, **kwargs):
    return _handle(method, url, **kwargs)

def _get(self, url, **kwargs):
    return _handle("GET", url, **kwargs)

requests.Session.request = _request
requests.Session.get = _get
'''


class OcrWorkspace(Workspace):
    """A Workspace whose doc 2 carries one image and whose OCR boundary is doubled."""

    def __init__(self, tmp_path: Path, *, mode: str = "success", image_url: str = IMAGE_URL):
        super().__init__(tmp_path, DOCUMENTS)
        self.mode = mode
        self.image_url = image_url
        self.ocr_log = tmp_path / "ocr-calls.log"
        self.ocr_cache = tmp_path / "ocr-cache"
        # Give doc 2 an image so the plan produces an OCR work item.
        remote = read_json(tmp_path / "remote.json")
        remote["details"]["2"]["body"] = f"body 2\n\n![img]({image_url})\n"
        write_json(tmp_path / "remote.json", remote)
        # Persist the cache outside wash via the config system.
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "[paths]\n", f'[paths]\nocr_cache="{self.ocr_cache}"\n', 1
            ),
            encoding="utf-8",
        )
        shim = tmp_path / "python-shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(
            _shim_source(self.ocr_log, mode), encoding="utf-8"
        )
        self.env["PYTHONPATH"] = os.pathsep.join([str(shim), str(ROOT / "src")])
        self.env["PADDLEOCR_TOKEN"] = "test-token"

    def submissions(self) -> int:
        if not self.ocr_log.is_file():
            return 0
        return sum(
            1
            for line in self.ocr_log.read_text(encoding="utf-8").splitlines()
            if line == "POST"
        )

    def ocr_reference(self) -> dict[str, Any]:
        return read_json(self.only_run() / "ocr" / "ocr.json")

    def ocr_envelope(self) -> dict[str, Any]:
        return read_json(self.only_run() / "ocr" / "result.json")


@pytest.fixture
def ocr_workspace(tmp_path: Path) -> OcrWorkspace:
    return OcrWorkspace(tmp_path)


def test_ocr_stage_processes_the_planned_image_and_persists_a_success_result(
    ocr_workspace: OcrWorkspace,
):
    completed = ocr_workspace.sync("--stop-after", "ocr")

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["stages"]["plan"] == "completed"
    assert summary["stages"]["ocr"] == "completed"

    envelope = ocr_workspace.ocr_envelope()
    assert envelope["status"] == "completed"
    assert envelope["counts"]["success"] == 1
    assert envelope["counts"]["failed"] == 0
    assert envelope["counts"]["unattempted"] == 0

    # The OCR-result-reference (04 schema) is published for compile to consume.
    reference = ocr_workspace.ocr_reference()
    assert reference["schema_version"] == OCR_SCHEMA
    row = next(r for r in reference["documents"] if r["document_id"] == 2)
    assert row["ocr"]["contract"] == CONTRACT
    image = row["ocr"]["images"][0]
    assert image["resource_id"] == IMAGE_URL
    assert image["status"] == "success"
    assert Path(image["artifact"]).is_file()
    assert Path(image["prepared_image"]).is_file()

    # Exactly one billable submission; the success result is cached outside wash.
    assert ocr_workspace.submissions() == 1
    assert ocr_workspace.ocr_cache.is_dir()
    assert not ocr_workspace.ocr_cache.is_relative_to(ocr_workspace.staging)

    # Success evidence is persisted to the canonical graph record's ocr field.
    state = read_json(ocr_workspace.corpus / ".sync-state.json")
    graph = state["books"]["fund"]["documents"]["2"]["graph"]
    assert graph["ocr"]["contract"] == CONTRACT
    assert graph["ocr"]["images"][0]["status"] == "success"