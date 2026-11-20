"""Construct source bindings for serialized CLI fixtures."""

import base64
import json
import os
import site
from pathlib import Path

from dogpdkb.settings import document_identity


def graphify_offline_env(tmp_path, port):
    """Use synthetic Graphify OAuth credentials that never refresh or leave localhost."""
    auth = tmp_path / "synthetic-oauth.json"
    claim = {"https://api.openai.com/auth": {"chatgpt_account_id": "offline-fixture"}}
    token = base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip("=")
    auth.write_text(
        json.dumps(
            {
                "access": f"fixture.{token}.fixture",
                "refresh": "unused",
                "expires": 4102444800000,
            }
        )
    )
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "PYTHONUSERBASE": site.getuserbase(),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "GRAPHIFY_OPENAI_OAUTH_AUTH_FILE": str(auth),
        "GRAPHIFY_CODEX_BASE_URL": f"http://127.0.0.1:{port}",
        "GRAPHIFY_OPENAI_OAUTH_MODEL": "fixture",
        "GRAPHIFY_API_TIMEOUT": "5",
        "GRAPHIFY_MAX_RETRIES": "0",
        "NO_PROXY": "127.0.0.1,localhost",
    }


def graphify_sse(nodes):
    events = [
        (
            "response.output_text.delta",
            {"delta": json.dumps({"nodes": nodes, "edges": []})},
        ),
        (
            "response.completed",
            {"response": {"usage": {"input_tokens": 1, "output_tokens": 1}}},
        ),
    ]
    return "".join(
        f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events
    ).encode()


def yuque_binding(url, document_ids=None):
    return {"team": {"type": "yuque", "url": url, "document_ids": document_ids}}


def team_id(remote_id):
    return document_identity("team", str(remote_id))


def team_row(remote_id):
    return {
        "document_id": team_id(remote_id),
        "source_key": "team",
        "source_document_id": str(remote_id),
    }
