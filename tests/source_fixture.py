"""Construct source bindings for serialized CLI fixtures."""

from dogpdkb.settings import document_identity


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
