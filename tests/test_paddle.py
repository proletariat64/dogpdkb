"""Unit tests for mdocr.paddle PaddleOCR API client."""

from __future__ import annotations

import json

import pytest

from mdocr.paddle import PaddleClient, parse_result_jsonl, text_only

from .conftest import FakeResponse, FakeSession, paddle_handler


def make_client(handler, token):
    session = FakeSession(handler)
    return PaddleClient(token, session=session), session


def test_convert_happy_path(tmp_path):
    image = tmp_path / "pic.jpg"
    image.write_bytes(b"jpeg-bytes")
    handler = paddle_handler(result_markdown="Line one\n\nLine two")
    client, session = make_client(handler, "test-token")

    result = client.convert(image)

    assert result == "Line one\n\nLine two"
    calls = session.calls
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/jobs")
    assert calls[0][2]["files"]["file"][0] == "pic.jpg"
    assert calls[1][0] == "GET"
    assert calls[2][0] == "GET"


def test_submit_never_retries_post(tmp_path):
    image = tmp_path / "pic.jpg"
    image.write_bytes(b"jpeg-bytes")
    client, session = make_client(paddle_handler(), "test-token")

    client.submit(image)

    posts = [c for c in session.calls if c[0] == "POST"]
    assert len(posts) == 1


def test_poll_failed_state_raises(tmp_path):
    image = tmp_path / "pic.jpg"
    image.write_bytes(b"jpeg-bytes")

    def handler(method, url, **kwargs):
        if method == "POST" and url.endswith("/jobs"):
            return FakeResponse(json_data={"data": {"jobId": "bad-job"}})
        if method == "GET" and url.endswith("/jobs/bad-job"):
            return FakeResponse(
                json_data={"data": {"state": "failed", "errorMsg": "boom"}}
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    client = PaddleClient("t", session=FakeSession(handler))
    with pytest.raises(RuntimeError, match="boom"):
        client.poll("bad-job")


def test_poll_unknown_state_raises():
    def handler(method, url, **kwargs):
        return FakeResponse(json_data={"data": {"state": "mystery"}})

    client = PaddleClient("t", session=FakeSession(handler))
    with pytest.raises(RuntimeError, match="mystery"):
        client.poll("j")


def test_text_only_strips_nested_images():
    text = 'hello ![x](https://example.com/a.png) and <img src="b.png"> end'
    assert text_only(text) == (
        "hello [PaddleOCR image region: x] and "
        "[PaddleOCR image region: untranscribed] end"
    )


def test_parse_result_jsonl_multiline():
    lines = [
        {"result": {"layoutParsingResults": [{"markdown": {"text": "a"}}]}},
        {"result": {"layoutParsingResults": [{"markdown": {"text": "b"}}]}},
    ]
    out = parse_result_jsonl("\n".join(json.dumps(line) for line in lines))
    assert out == "a\n\nb"


def test_parse_result_jsonl_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        parse_result_jsonl("")
