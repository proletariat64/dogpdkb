"""PaddleOCR job API client, adapted from scripts/paddleocr_convert.py."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from ._http import HttpClient

_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
_MODEL = "PaddleOCR-VL-1.6"

_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+['\"][^'\"]*['\"])?\)"
)
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_REQUEST_TIMEOUT = 60
_POLL_INTERVAL = 5
_JOB_TIMEOUT = 900
_REQUEST_RETRIES = 3
_RETRY_DELAY = 2


class OcrSharedError(RuntimeError):
    """A shared cause (auth/quota) that should stop the whole batch's OCR.

    Subclasses RuntimeError so existing per-image handlers keep working; the OCR
    stage catches it specifically to stop the batch and record one batch cause
    instead of retrying every remaining image against the same exhausted token
    or quota. ``code`` is the stable anomaly/batch-cause code.
    """

    code = "OCR_SHARED_UNAVAILABLE"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class OcrAuthError(OcrSharedError):
    """The token is missing, rejected or expired (HTTP 401)."""

    code = "OCR_AUTH_UNAVAILABLE"


class OcrQuotaError(OcrSharedError):
    """The shared quota/rate allowance is exhausted (HTTP 403/429)."""

    code = "OCR_QUOTA_EXHAUSTED"


def _ocr_error(status: int | None, message: str) -> RuntimeError:
    """Classify a terminal request failure into a shared or generic error."""
    if status == 401:
        return OcrAuthError(message)
    if status in (403, 429):
        return OcrQuotaError(message)
    return RuntimeError(message)


def _text_only(markdown: str) -> str:
    """Remove image embeds emitted by PaddleOCR while retaining useful labels."""

    def replace(match: re.Match[str]) -> str:
        alt = match.group(0).split("]", 1)[0][2:].strip()
        return f"[PaddleOCR image region: {alt or 'untranscribed'}]"

    markdown = _IMAGE_RE.sub(replace, markdown)
    markdown = _HTML_IMAGE_RE.sub("[PaddleOCR image region: untranscribed]", markdown)
    return markdown.strip()


def _extract_markdown(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        # ValueError, not TypeError: the OCR per-image handler catches ValueError
        # so a malformed result line fails one image instead of aborting the batch.
        raise ValueError("result line was not a JSON object")  # noqa: TRY004
    blocks: list[str] = []
    result = payload.get("result", payload)
    pages = result.get("layoutParsingResults", []) if isinstance(result, dict) else []
    for page in pages:
        markdown = page.get("markdown", {}) if isinstance(page, dict) else {}
        text = markdown.get("text", "") if isinstance(markdown, dict) else ""
        if text and text.strip():
            blocks.append(text.strip())
            continue
        # Some successful image jobs return an empty aggregate Markdown string
        # while retaining OCR text in the ordered layout blocks.
        pruned = page.get("prunedResult", {}) if isinstance(page, dict) else {}
        parsing = pruned.get("parsing_res_list", []) if isinstance(pruned, dict) else []
        fallback = [
            item.get("block_content", "").strip()
            for item in parsing
            if isinstance(item, dict) and item.get("block_content", "").strip()
        ]
        if fallback:
            blocks.append("\n\n".join(fallback))
    if not blocks:
        raise ValueError("result contained no layoutParsingResults[].markdown.text")
    return "\n\n".join(blocks)


def _parse_result_jsonl(text: str) -> str:
    blocks: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            blocks.append(_extract_markdown(json.loads(raw)))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
            # Normalize every structural failure of an untrusted result line
            # (null pages/parsing lists, numeric text/block_content) to ValueError
            # so the OCR per-image handler fails one image instead of aborting the
            # whole batch on a malformed-200 nested schema (H12).
            raise ValueError(f"invalid result JSONL line {number}: {exc}") from exc
    if not blocks:
        raise ValueError("downloaded result JSONL was empty")
    return "\n\n".join(blocks)


class PaddleClient:
    """Convert local images to Markdown through PaddleOCR."""

    def __init__(
        self,
        token: str,
        *,
        session: HttpClient | None = None,
        endpoint: str | None = None,
    ):
        self.endpoint = (endpoint or _JOB_URL).rstrip("/")
        self.headers = {"Authorization": f"bearer {token}"}
        self.session = session or requests.Session()

    def _request(
        self, method: str, url: str, *, max_retries: int | None = None, **kwargs: Any
    ) -> requests.Response:
        delay = _RETRY_DELAY
        retries = _REQUEST_RETRIES if max_retries is None else max_retries
        for attempt in range(retries + 1):
            status = None
            try:
                response = self.session.request(
                    method, url, timeout=_REQUEST_TIMEOUT, **kwargs
                )
                status = response.status_code
                if status not in _TRANSIENT_STATUS:
                    response.raise_for_status()
                    return response
                error = f"HTTP {status}: {response.text[:300]}"
            except requests.RequestException as exc:
                error = str(exc)
                resp = getattr(exc, "response", None)
                if resp is not None:
                    status = getattr(resp, "status_code", status)
            if attempt >= retries:
                raise _ocr_error(status, error)
            time.sleep(delay * (2**attempt))
        raise AssertionError("unreachable")

    def _submit(self, image_path: Path) -> str:
        # Matches the defaults used by scripts/paddleocr_convert.py; layout
        # detection and prettify are left to the server's own defaults.
        payload = {
            "model": _MODEL,
            "optionalPayload": json.dumps(
                {
                    "useDocOrientationClassify": False,
                    "useDocUnwarping": False,
                    "useChartRecognition": False,
                }
            ),
        }
        with image_path.open("rb") as stream:
            # Never automatically retry a submission: a lost response may still
            # have created a billable job, and POST is not idempotent.
            response = self._request(
                "POST",
                self.endpoint,
                max_retries=0,
                headers=self.headers,
                data=payload,
                files={"file": (image_path.name, stream)},
            )
        try:
            job_id = response.json()["data"]["jobId"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "malformed PaddleOCR submit response: missing data.jobId"
            ) from exc
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("malformed PaddleOCR submit response: empty data.jobId")
        return job_id

    def _poll(self, job_id: str) -> str:
        deadline = time.monotonic() + _JOB_TIMEOUT
        while time.monotonic() < deadline:
            try:
                payload = self._request(
                    "GET", f"{self.endpoint}/{job_id}", headers=self.headers
                ).json()["data"]
                state = payload["state"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "malformed PaddleOCR job response: missing data.state"
                ) from exc
            if state == "done":
                try:
                    url = payload["resultUrl"]["jsonUrl"]
                except (KeyError, TypeError) as exc:
                    raise ValueError(
                        "malformed PaddleOCR job response: missing resultUrl.jsonUrl"
                    ) from exc
                result = self._request("GET", url).text
                return _text_only(_parse_result_jsonl(result))
            if state == "failed":
                raise RuntimeError(payload.get("errorMsg", "PaddleOCR job failed"))
            if state not in {"pending", "running"}:
                raise RuntimeError(f"unknown PaddleOCR job state: {state!r}")
            time.sleep(_POLL_INTERVAL)
        raise TimeoutError(f"job {job_id} exceeded {_JOB_TIMEOUT}s")

    def convert(self, image_path: Path) -> str:
        """Convert one local image to PaddleOCR Markdown."""
        return self._poll(self._submit(image_path))
