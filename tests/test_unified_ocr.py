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


def _shim_source(log_path: Path, mode: str, image_file: Path) -> str:
    """A sitecustomize that doubles requests.Session for images + PaddleOCR."""
    return f"""
import io, json, time
time.sleep = lambda seconds: None
import requests
from PIL import Image

LOG = {str(log_path)!r}
MODE = {mode!r}
IMAGE_FILE = {str(image_file)!r}

def _png():
    with open(IMAGE_FILE, "rb") as stream:
        return stream.read()

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
    if url == "https://example.com/result.jsonl":
        if MODE == "blank":
            return Resp(text="")
        payload = {{"result": {{"layoutParsingResults": [
            {{"markdown": {{"text": "OCR output line"}}}}]}}}}
        return Resp(text=json.dumps(payload))
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
"""


class OcrWorkspace(Workspace):
    """A Workspace whose doc 2 carries one image and whose OCR boundary is doubled."""

    def __init__(
        self, tmp_path: Path, *, mode: str = "success", image_url: str = IMAGE_URL
    ):
        super().__init__(tmp_path, DOCUMENTS)
        self.mode = mode
        self.image_url = image_url
        self.ocr_log = tmp_path / "ocr-calls.log"
        self.ocr_cache = tmp_path / "ocr-cache"
        self.image_file = tmp_path / "serve-image.png"
        self.write_image(200)
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
            _shim_source(self.ocr_log, mode, self.image_file), encoding="utf-8"
        )
        self.env["PYTHONPATH"] = os.pathsep.join([str(shim), str(ROOT / "src")])
        self.env["PADDLEOCR_TOKEN"] = "test-token"

    def write_image(self, red: int) -> None:
        """Serve a distinct-but-valid PNG so content changes are observable."""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (red, 30, 30)).save(buf, format="PNG")
        self.image_file.write_bytes(buf.getvalue())

    def submissions(self) -> int:
        if not self.ocr_log.is_file():
            return 0
        return sum(
            1
            for line in self.ocr_log.read_text(encoding="utf-8").splitlines()
            if line == "POST"
        )

    def set_mode(self, mode: str) -> None:
        """Re-bake the shim so the next sync subprocess serves a new boundary."""
        self.mode = mode
        shim = self.root / "python-shim" / "sitecustomize.py"
        shim.write_text(
            _shim_source(self.ocr_log, mode, self.image_file), encoding="utf-8"
        )

    def latest_run(self) -> Path:
        return self.runs()[-1]

    def ocr_reference(self, run: Path | None = None) -> dict[str, Any]:
        return read_json((run or self.only_run()) / "ocr" / "ocr.json")

    def ocr_envelope(self, run: Path | None = None) -> dict[str, Any]:
        return read_json((run or self.only_run()) / "ocr" / "result.json")


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


def test_repeat_sync_reuses_the_cached_success_without_a_second_submission(
    ocr_workspace: OcrWorkspace,
):
    # A full sync fails at the still-unregistered compile stage, which releases
    # the batch claim so a second batch may start. OCR runs in both batches.
    first = ocr_workspace.sync()
    assert first.returncode != 0
    run1 = ocr_workspace.latest_run()
    assert ocr_workspace.ocr_envelope(run1)["counts"]["success"] == 1
    assert ocr_workspace.submissions() == 1

    second = ocr_workspace.sync()
    assert second.returncode != 0
    run2 = ocr_workspace.latest_run()
    assert run2 != run1
    counts = ocr_workspace.ocr_envelope(run2)["counts"]
    # Same verified content + compatible config reuses; no proactive re-OCR.
    assert counts["reused"] == 1
    assert counts["success"] == 0
    assert counts["failed"] == 0
    assert ocr_workspace.submissions() == 1

    reference = ocr_workspace.ocr_reference(run2)
    row = next(r for r in reference["documents"] if r["document_id"] == 2)
    image = row["ocr"]["images"][0]
    assert image["status"] == "success"
    assert Path(image["artifact"]).is_file()


def test_same_url_with_changed_image_content_is_recognized_again(
    ocr_workspace: OcrWorkspace,
):
    ocr_workspace.sync()
    run1 = ocr_workspace.latest_run()
    assert ocr_workspace.ocr_envelope(run1)["counts"]["success"] == 1
    assert ocr_workspace.submissions() == 1

    # Same URL, different bytes: the URL alone must not prove the image unchanged.
    ocr_workspace.write_image(20)
    ocr_workspace.sync()
    run2 = ocr_workspace.latest_run()
    counts = ocr_workspace.ocr_envelope(run2)["counts"]
    assert counts["success"] == 1
    assert counts["reused"] == 0
    assert ocr_workspace.submissions() == 2


def test_a_blank_result_never_overwrites_a_cached_success(
    ocr_workspace: OcrWorkspace,
):
    ocr_workspace.sync()
    run1 = ocr_workspace.latest_run()
    assert ocr_workspace.ocr_envelope(run1)["counts"]["success"] == 1
    cached = sorted(ocr_workspace.ocr_cache.glob("fund/*/artifact.md"))
    assert len(cached) == 1
    before = cached[0].read_text(encoding="utf-8")
    assert "OCR output line" in before

    # A different image (cache miss) meets an unstable interface returning blank.
    # It must fail without clobbering the valid cached success for the first image
    # and without minting a success result of its own.
    ocr_workspace.write_image(20)
    ocr_workspace.set_mode("blank")
    ocr_workspace.sync()
    run2 = ocr_workspace.latest_run()
    counts2 = ocr_workspace.ocr_envelope(run2)["counts"]
    assert counts2["failed"] == 1
    assert counts2["reused"] == 0
    assert counts2["success"] == 0
    still = sorted(ocr_workspace.ocr_cache.glob("fund/*/artifact.md"))
    assert len(still) == 1
    assert still[0].read_text(encoding="utf-8") == before
