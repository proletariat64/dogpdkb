"""Deletion safety when discovery resumes after remote membership changes."""

import json

from scripts import get_sync_updates as get_sync


def test_refresh_does_not_mix_cached_and_live_pages(tmp_path, monkeypatch):
    cache = tmp_path / "pages"
    cache.mkdir()
    (cache / "page-123-0000000.json").write_text(
        json.dumps(
            {
                "book_id": 123,
                "offset": 0,
                "items": [{"id": i} for i in range(1, 51)],
            }
        )
    )
    # ID 10 was removed and ID 101 added after the interrupted first page.
    current_ids = [i for i in range(1, 102) if i != 10]
    calls = []

    def fake_call(tool, args):
        calls.append(args["offset"])
        start = args["offset"]
        return [{"id": i} for i in current_ids[start : start + 50]]

    monkeypatch.setattr(get_sync, "call_tool", fake_call)

    docs = get_sync.fetch_doc_list(123, 100, cache)

    assert [doc["id"] for doc in docs] == current_ids
    assert sorted(calls) == [0, 50, 100]
    assert json.loads((cache / "page-123-0000000.json").read_text())["items"] == [
        {"id": i} for i in current_ids[:50]
    ]
