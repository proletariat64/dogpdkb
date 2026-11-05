"""An emptied tracked document must replace searchable stale content."""

import json
import sys

import pytest

from scripts import apply_sync_updates as apply_sync
from scripts import get_sync_updates as get_sync


@pytest.mark.parametrize("legacy_checkpoint", [False, True])
def test_tracked_empty_body_is_published_and_in_graph_manifest(
    tmp_path, monkeypatch, legacy_checkpoint
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    old = corpus / "Doc-2026-01-01-[1].md"
    old.write_text("removed searchable text")
    stamp = "2026-11-01T12:00:00+08:00"
    sync_path = tmp_path / "sync.json"
    sync_path.write_text(
        json.dumps(
            {
                "book": {"book_id": 123},
                "new": [{"id": 2, "title": "New", "content_updated_at": stamp}],
                "updated": [
                    {
                        "id": 1,
                        "title": "Doc",
                        "content_updated_at": stamp,
                        "local_date": "2026-01-01",
                    }
                ],
            }
        )
    )
    staging = tmp_path / "staging"
    if legacy_checkpoint:
        staging.mkdir()
        (staging / "export-progress.json").write_text(
            json.dumps(
                {
                    "source": {
                        "path": str(sync_path.resolve()),
                        "mtime_ns": sync_path.stat().st_mtime_ns,
                    },
                    "documents": {"1": {"status": "skipped_empty"}},
                }
            )
        )
    monkeypatch.setattr(
        apply_sync, "resolve_book", lambda url: {"book_id": 123, "slug": "test"}
    )
    monkeypatch.setattr(apply_sync, "toc_paths", lambda book_id: {})
    monkeypatch.setattr(
        apply_sync,
        "export_doc",
        lambda doc_id, doc_format=None: ("", {"content_updated_at": stamp}),
    )

    def fake_ocr(files, out_dir):
        for path in files:
            (out_dir / path.name).write_text(path.read_text())
        return 0

    monkeypatch.setattr(apply_sync, "run_mdocr", fake_ocr)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_sync_updates.py",
            "-i",
            str(corpus),
            "-s",
            "https://example.invalid/book",
            "--sync-json",
            str(sync_path),
            "--staging",
            str(staging),
            "--cutoff",
            "2026-01-01",
        ],
    )

    apply_sync.main()

    assert not old.exists()
    assert (staging / "backup" / old.name).read_text() == "removed searchable text"
    state = get_sync.load_sync_state(corpus)
    assert state["1"]["content_updated_at"] == stamp
    assert (corpus / state["1"]["file"]).read_text() == ""
    manifest = json.loads((staging / "manifest.json").read_text())
    assert [record["id"] for record in manifest["exported"]] == [1]
    assert manifest["skipped"] == [{"id": 2, "reason": "empty document body"}]
