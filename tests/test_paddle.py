"""Tests for the PaddleOCR conversion seam."""

from __future__ import annotations

import json

import pytest

from mdocr.paddle import PaddleClient

from .conftest import FakeResponse, FakeSession, paddle_handler


def make_client(handler):
    session = FakeSession(handler)
    return PaddleClient("test-token", session=session), session


def make_image(tmp_path):
    image = tmp_path / "pic.jpg"
    image.write_bytes(b"jpeg-bytes")
    return image


def test_convert_happy_path(tmp_path):
    image = make_image(tmp_path)
    handler = paddle_handler(result_markdown="Line one\n\nLine two")
    client, session = make_client(handler)

    result = client.convert(image)

    assert result == "Line one\n\nLine two"
    calls = session.calls
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/jobs")
    assert calls[0][2]["files"]["file"][0] == "pic.jpg"
    assert calls[1][0] == "GET"
    assert calls[2][0] == "GET"


def test_convert_never_retries_submission(tmp_path):
    image = make_image(tmp_path)

    def handler(method, url, **kwargs):
        assert method == "POST"
        return FakeResponse(status_code=503, text="busy")

    client, session = make_client(handler)

    with pytest.raises(RuntimeError, match="HTTP 503"):
        client.convert(image)

    assert [call[0] for call in session.calls] == ["POST"]


def test_convert_failed_job_raises(tmp_path):
    image = make_image(tmp_path)

    def handler(method, url, **kwargs):
        if method == "POST" and url.endswith("/jobs"):
            return FakeResponse(json_data={"data": {"jobId": "bad-job"}})
        if method == "GET" and url.endswith("/jobs/bad-job"):
            return FakeResponse(
                json_data={"data": {"state": "failed", "errorMsg": "boom"}}
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    client, _session = make_client(handler)

    with pytest.raises(RuntimeError, match="boom"):
        client.convert(image)


def test_convert_unknown_job_state_raises(tmp_path):
    image = make_image(tmp_path)

    def handler(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(json_data={"data": {"jobId": "job-1"}})
        return FakeResponse(json_data={"data": {"state": "mystery"}})

    client, _session = make_client(handler)

    with pytest.raises(RuntimeError, match="mystery"):
        client.convert(image)


def test_convert_replaces_nested_images_with_text(tmp_path):
    image = make_image(tmp_path)
    markdown = 'hello ![x](https://example.com/a.png) and <img src="b.png"> end'
    client, _session = make_client(paddle_handler(result_markdown=markdown))

    result = client.convert(image)

    assert result == (
        "hello [PaddleOCR image region: x] and "
        "[PaddleOCR image region: untranscribed] end"
    )


def test_convert_joins_multiline_result_jsonl(tmp_path):
    image = make_image(tmp_path)
    result_url = "https://example.com/result.jsonl"
    lines = [
        {"result": {"layoutParsingResults": [{"markdown": {"text": "a"}}]}},
        {"result": {"layoutParsingResults": [{"markdown": {"text": "b"}}]}},
    ]

    def handler(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(json_data={"data": {"jobId": "job-1"}})
        if url.endswith("/jobs/job-1"):
            return FakeResponse(
                json_data={
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": result_url},
                    }
                }
            )
        if url == result_url:
            return FakeResponse(text="\n".join(json.dumps(line) for line in lines))
        raise AssertionError(f"unexpected request: {method} {url}")

    client, _session = make_client(handler)

    assert client.convert(image) == "a\n\nb"


def test_convert_rejects_empty_result_jsonl(tmp_path):
    image = make_image(tmp_path)
    result_url = "https://example.com/result.jsonl"

    def handler(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(json_data={"data": {"jobId": "job-1"}})
        if url.endswith("/jobs/job-1"):
            return FakeResponse(
                json_data={
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": result_url},
                    }
                }
            )
        if url == result_url:
            return FakeResponse(text="")
        raise AssertionError(f"unexpected request: {method} {url}")

    client, _session = make_client(handler)

    with pytest.raises(ValueError, match="empty"):
        client.convert(image)
