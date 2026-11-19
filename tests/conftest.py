"""Shared fixtures for mdocr tests. No network access, no real API token."""

from __future__ import annotations

import io
import json
from collections.abc import Callable

import pytest
from PIL import Image


def make_image(fmt: str = "PNG", color=(200, 30, 30), size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


class FakeSession:
    """Handler-based fake requests.Session recording all calls."""

    def __init__(self, handler: Callable[..., object]):
        self.handler = handler
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.handler(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


def paddle_handler(result_markdown: str = "OCR output line", job_id: str = "job-1"):
    """Return (handler, result_url) simulating a successful PaddleOCR job."""
    result_url = "https://example.com/result.jsonl"
    payload = {
        "result": {"layoutParsingResults": [{"markdown": {"text": result_markdown}}]}
    }

    def handler(method, url, **kwargs):
        if method == "POST" and url.endswith("/jobs"):
            return FakeResponse(json_data={"data": {"jobId": job_id}})
        if method == "GET" and url.endswith(f"/jobs/{job_id}"):
            return FakeResponse(
                json_data={
                    "data": {"state": "done", "resultUrl": {"jsonUrl": result_url}}
                }
            )
        if method == "GET" and url == result_url:
            return FakeResponse(text=json.dumps(payload))
        raise AssertionError(f"unexpected request: {method} {url}")

    return handler


def ref_of(markdown: str):
    """Parse the first inline image in a Markdown snippet."""
    from mdocr.markdown import IMAGE_RE, parse_image_match

    match = IMAGE_RE.search(markdown)
    assert match is not None
    return parse_image_match(match)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PADDLEOCR_TOKEN", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "user-state"))
    monkeypatch.delenv("DOGPDKB_UPDATE_LOCK_FD", raising=False)
