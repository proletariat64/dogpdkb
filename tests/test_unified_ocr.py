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
SECOND_IMAGE_URL = "https://cdn.example.invalid/b.png"


def _shim_source(log_path: Path, mode: str, image_file: Path, primary_url: str) -> str:
    """A sitecustomize that doubles requests.Session for images + PaddleOCR."""
    return f"""
import io, json, time
time.sleep = lambda seconds: None
import requests
from PIL import Image

LOG = {str(log_path)!r}
MODE = {mode!r}
IMAGE_FILE = {str(image_file)!r}
PRIMARY_URL = {primary_url!r}

def _png(url):
    if url == PRIMARY_URL:
        with open(IMAGE_FILE, "rb") as stream:
            return stream.read()
    seed = sum(url.encode()) % 256
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (seed, 90, 90)).save(buf, format="PNG")
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
            fname = kwargs.get("files", {{}}).get("file", ("job",))[0]
            job = "job-b" if fname.startswith("b") else "job-a"
            return Resp(json_data={{"data": {{"jobId": job}}}})
        if method == "GET" and "/jobs/" in url:
            if MODE == "fail" or (MODE == "isolation" and url.endswith("job-b")):
                return Resp(json_data={{"data": {{"state": "failed", "errorMsg": "boom"}}}})
            return Resp(json_data={{"data": {{"state": "done",
                "resultUrl": {{"jsonUrl": "https://example.com/result.jsonl"}}}}}})
        raise AssertionError(url)
    if url.startswith("https://cdn.example.invalid/"):
        _record("IMG " + url)
        return Resp(content=_png(url))
    raise AssertionError(f"unexpected {{method}} {{url}}")

def _request(self, method, url, **kwargs):
    return _handle(method, url, **kwargs)

def _get(self, url, **kwargs):
    return _handle("GET", url, **kwargs)

requests.Session.request = _request
requests.Session.get = _get
"""


class OcrWorkspace(Workspace):
    """A Workspace whose doc 2 carries images and whose OCR boundary is doubled."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        mode: str = "success",
        image_url: str = IMAGE_URL,
        image_urls: list[str] | None = None,
        budget_seconds: float | None = None,
    ):
        super().__init__(tmp_path, DOCUMENTS)
        self.mode = mode
        self.image_url = image_url
        self.image_urls = image_urls or [image_url]
        self.ocr_log = tmp_path / "ocr-calls.log"
        self.ocr_cache = tmp_path / "ocr-cache"
        self.image_file = tmp_path / "serve-image.png"
        self.write_image(200)
        # Give doc 2 images so the plan produces OCR work items.
        remote = read_json(tmp_path / "remote.json")
        embeds = "\n".join(f"![img]({url})" for url in self.image_urls)
        remote["details"]["2"]["body"] = f"body 2\n\n{embeds}\n"
        write_json(tmp_path / "remote.json", remote)
        # Persist the cache outside wash via the config system.
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "[paths]\n", f'[paths]\nocr_cache="{self.ocr_cache}"\n', 1
            ),
            encoding="utf-8",
        )
        if budget_seconds is not None:
            self.config.write_text(
                self.config.read_text(encoding="utf-8")
                + f"\n[ocr]\nbudget_seconds={budget_seconds}\n",
                encoding="utf-8",
            )
        shim = tmp_path / "python-shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(
            _shim_source(self.ocr_log, mode, self.image_file, self.image_url),
            encoding="utf-8",
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
            _shim_source(self.ocr_log, mode, self.image_file, self.image_url),
            encoding="utf-8",
        )

    def bump_version(self, doc_id: int, stamp: str) -> None:
        """A new remote version: listing and details must agree on the instant."""
        remote = read_json(self.root / "remote.json")
        for document in remote["documents"]:
            if document["id"] == doc_id:
                document["content_updated_at"] = stamp
        remote["details"][str(doc_id)]["content_updated_at"] = stamp
        write_json(self.root / "remote.json", remote)

    def latest_run(self) -> Path:
        return self.runs()[-1]

    def ocr_reference(self, run: Path | None = None) -> dict[str, Any]:
        return read_json((run or self.only_run()) / "ocr" / "ocr.json")

    def ocr_envelope(self, run: Path | None = None) -> dict[str, Any]:
        return read_json((run or self.only_run()) / "ocr" / "result.json")

    def registry(self) -> dict[str, Any]:
        return read_json(self.root / "sync-results" / "fund" / "anomalies.json")


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


def test_a_single_image_failure_is_recorded_and_the_others_continue(tmp_path: Path):
    workspace = OcrWorkspace(
        tmp_path, mode="isolation", image_urls=[IMAGE_URL, SECOND_IMAGE_URL]
    )
    completed = workspace.sync("--stop-after", "ocr")
    assert completed.returncode == 0, completed.stderr

    envelope = workspace.ocr_envelope()
    assert envelope["status"] == "partial"
    counts = envelope["counts"]
    assert counts["total"] == 2
    assert counts["success"] == 1
    assert counts["failed"] == 1
    assert counts["unattempted"] == 0
    # Both images were attempted; the failure did not stop the batch.
    assert workspace.submissions() == 2

    reference = workspace.ocr_reference()
    row = next(r for r in reference["documents"] if r["document_id"] == 2)
    assert len(row["ocr"]["images"]) == 1
    assert row["ocr"]["images"][0]["resource_id"] == IMAGE_URL
    missing = row["execution"]["missing_items"]
    assert [m["resource_id"] for m in missing] == [SECOND_IMAGE_URL]

    failures = [
        a
        for a in workspace.registry()["anomalies"].values()
        if a["code"] == "OCR_FAILED"
    ]
    assert len(failures) == 1
    assert failures[0]["resource_id"] == SECOND_IMAGE_URL


def _batch_stop_assertions(workspace: OcrWorkspace, code: str) -> None:
    completed = workspace.sync("--stop-after", "ocr")
    assert completed.returncode == 0, completed.stderr
    envelope = workspace.ocr_envelope()
    assert envelope["status"] == "partial"
    counts = envelope["counts"]
    assert counts["total"] == 2
    assert counts["success"] == 0
    assert counts["reused"] == 0
    assert counts["failed"] == 0
    # The shared cause stops the batch: nothing executed is counted success.
    assert counts["unattempted"] == 2
    # Only the triggering submission was sent; the remaining image was not.
    assert workspace.submissions() == 1

    registry = workspace.registry()
    causes = list(registry["batch_causes"].values())
    assert len(causes) == 1
    assert causes[0]["code"] == code
    assert causes[0]["stage"] == "ocr"
    assert len(causes[0]["anomaly_ids"]) == 2
    affected = [
        a
        for a in registry["anomalies"].values()
        if a.get("batch_cause") == causes[0]["id"]
    ]
    assert len(affected) == 2
    assert {a["resource_id"] for a in affected} == {IMAGE_URL, SECOND_IMAGE_URL}


def test_auth_unavailable_stops_the_whole_batch(tmp_path: Path):
    workspace = OcrWorkspace(
        tmp_path, mode="auth", image_urls=[IMAGE_URL, SECOND_IMAGE_URL]
    )
    _batch_stop_assertions(workspace, "OCR_AUTH_UNAVAILABLE")


def test_quota_exhaustion_stops_the_whole_batch(tmp_path: Path):
    workspace = OcrWorkspace(
        tmp_path, mode="quota", image_urls=[IMAGE_URL, SECOND_IMAGE_URL]
    )
    _batch_stop_assertions(workspace, "OCR_QUOTA_EXHAUSTED")


def test_budget_exhaustion_archives_unfinished_and_proceeds_to_compile(tmp_path: Path):
    workspace = OcrWorkspace(
        tmp_path,
        image_urls=[IMAGE_URL, SECOND_IMAGE_URL],
        budget_seconds=0,
    )
    # A full sync reaches compile (still unregistered) even though OCR ran out of
    # budget: OCR completion is never a prerequisite for compile.
    completed = workspace.sync()
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["stages"]["ocr"] == "partial"
    assert summary["stages"]["compile"] == "failed"

    envelope = workspace.ocr_envelope()
    counts = envelope["counts"]
    assert counts["total"] == 2
    assert counts["success"] == 0
    assert counts["reused"] == 0
    assert counts["unattempted"] == 2
    assert workspace.submissions() == 0

    causes = list(workspace.registry()["batch_causes"].values())
    assert len(causes) == 1
    assert causes[0]["code"] == "OCR_BUDGET_EXHAUSTED"
    assert len(causes[0]["anomaly_ids"]) == 2
    # The reference still lists the document and its unrecognized images.
    row = next(
        r for r in workspace.ocr_reference()["documents"] if r["document_id"] == 2
    )
    assert len(row["execution"]["missing_items"]) == 2


def test_same_version_archived_ocr_failure_is_not_retried_until_new_version(
    tmp_path: Path,
):
    workspace = OcrWorkspace(tmp_path, mode="fail")

    # Batch 1: the image fails; the failure is archived for this source version.
    # A full sync ends failed at the still-unregistered compile stage, which
    # releases the batch claim so the next daily batch may start.
    first = workspace.sync()
    assert first.returncode != 0
    run1 = workspace.latest_run()
    assert workspace.ocr_envelope(run1)["counts"]["failed"] == 1
    assert workspace.submissions() == 1
    failures = [
        a
        for a in workspace.registry()["anomalies"].values()
        if a["code"] == "OCR_FAILED"
    ]
    assert len(failures) == 1
    assert failures[0]["disposition"] == "pending"
    assert failures[0]["attempts"] == 1

    # Batch 2: the remote version is unchanged, so the archived failure is handed
    # off to the post-hoc agent instead of being retried by the daily batch.
    second = workspace.sync()
    assert second.returncode != 0
    run2 = workspace.latest_run()
    assert run2 != run1
    plan2 = read_json(run2 / "plan" / "plan.json")
    assert 2 in {row["document_id"] for row in plan2["suppressed"]}
    assert 2 not in {row["document_id"] for row in plan2["ocr"]}
    counts2 = workspace.ocr_envelope(run2)["counts"]
    assert counts2["total"] == 0
    assert counts2["failed"] == 0
    # No new billable submission and no re-registration of the same failure.
    assert workspace.submissions() == 1
    still = [
        a
        for a in workspace.registry()["anomalies"].values()
        if a["code"] == "OCR_FAILED"
    ]
    assert len(still) == 1
    assert still[0]["attempts"] == 1
    assert still[0]["disposition"] == "pending"

    # Batch 3: a new remote version participates in daily sync normally; the
    # abandonment of the old version never bans the source.
    workspace.bump_version(2, "2026-11-11T09:00:00+08:00")
    workspace.set_mode("success")
    third = workspace.sync()
    assert third.returncode != 0
    run3 = workspace.latest_run()
    plan3 = read_json(run3 / "plan" / "plan.json")
    assert 2 not in {row["document_id"] for row in plan3["suppressed"]}
    assert 2 in {row["document_id"] for row in plan3["ocr"]}
    counts3 = workspace.ocr_envelope(run3)["counts"]
    assert counts3["success"] == 1
    assert counts3["failed"] == 0
    assert workspace.submissions() == 2
