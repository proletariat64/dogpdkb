"""PaddleOCR job API client, adapted from scripts/paddleocr_convert.py."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from ._http import HttpClient

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.6"

IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+['\"][^'\"]*['\"])?\)"
)
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)

TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def text_only(markdown: str) -> str:
    """Remove image embeds emitted by PaddleOCR while retaining useful labels."""

    def replace(match: re.Match[str]) -> str:
        alt = match.group(0).split("]", 1)[0][2:].strip()
        return f"[PaddleOCR image region: {alt or 'untranscribed'}]"

    markdown = IMAGE_RE.sub(replace, markdown)
    markdown = HTML_IMAGE_RE.sub("[PaddleOCR image region: untranscribed]", markdown)
    return markdown.strip()


def extract_markdown(payload: dict[str, Any]) -> str:
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


def parse_result_jsonl(text: str) -> str:
    blocks: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            blocks.append(extract_markdown(json.loads(raw)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid result JSONL line {number}: {exc}") from exc
    if not blocks:
        raise ValueError("downloaded result JSONL was empty")
    return "\n\n".join(blocks)


class PaddleClient:
    """Submit image files to the PaddleOCR job API and poll for Markdown."""

    def __init__(
        self,
        token: str,
        *,
        job_url: str = JOB_URL,
        model: str = MODEL,
        session: HttpClient | None = None,
        timeout: float = 60,
        poll_interval: float = 5,
        job_timeout: float = 900,
        retries: int = 3,
        retry_delay: float = 2,
    ):
        self.headers = {"Authorization": f"bearer {token}"}
        self.job_url = job_url
        self.model = model
        self.session = session or requests.Session()
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.job_timeout = job_timeout
        self.retries = retries
        self.retry_delay = retry_delay

    def _request(
        self, method: str, url: str, *, max_retries: int | None = None, **kwargs: Any
    ) -> requests.Response:
        delay = self.retry_delay
        retries = self.retries if max_retries is None else max_retries
        for attempt in range(retries + 1):
            try:
                response = self.session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
                if response.status_code not in TRANSIENT_STATUS:
                    response.raise_for_status()
                    return response
                error = f"HTTP {response.status_code}: {response.text[:300]}"
            except requests.RequestException as exc:
                error = str(exc)
            if attempt >= retries:
                raise RuntimeError(error)
            time.sleep(delay * (2**attempt))
        raise AssertionError("unreachable")

    def submit(self, image_path: Path) -> str:
        # Matches the defaults used by scripts/paddleocr_convert.py; layout
        # detection and prettify are left to the server's own defaults.
        payload = {
            "model": self.model,
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
                self.job_url,
                max_retries=0,
                headers=self.headers,
                data=payload,
                files={"file": (image_path.name, stream)},
            )
        return response.json()["data"]["jobId"]

    def poll(self, job_id: str) -> str:
        deadline = time.monotonic() + self.job_timeout
        while time.monotonic() < deadline:
            payload = self._request(
                "GET", f"{self.job_url}/{job_id}", headers=self.headers
            ).json()["data"]
            state = payload["state"]
            if state == "done":
                url = payload["resultUrl"]["jsonUrl"]
                result = self._request("GET", url).text
                return text_only(parse_result_jsonl(result))
            if state == "failed":
                raise RuntimeError(payload.get("errorMsg", "PaddleOCR job failed"))
            if state not in {"pending", "running"}:
                raise RuntimeError(f"unknown PaddleOCR job state: {state!r}")
            time.sleep(self.poll_interval)
        raise TimeoutError(f"job {job_id} exceeded {self.job_timeout}s")

    def convert(self, image_path: Path) -> str:
        return self.poll(self.submit(image_path))
