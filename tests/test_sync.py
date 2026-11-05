from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts import apply_sync_updates as apply_sync
from scripts import get_sync_updates as get_sync


def test_build_lists_detects_same_day_update_with_exact_local_timestamp():
    docs = [{"id": 1, "content_updated_at": "2026-10-31T12:00:00+08:00"}]
    local = {
        1: (
            date(2026, 10, 31),
            "doc-2026-10-31-[1].md",
            get_sync._parse_ts("2026-10-31T11:59:00+08:00"),
        )
    }

    _, updated = get_sync.build_lists(docs, local)

    assert [doc["id"] for _, _, doc in updated] == [1]


def test_build_lists_does_not_repeat_unchanged_remote_timestamp():
    stamp = "2026-10-31T12:00:00+08:00"
    docs = [{"id": 1, "content_updated_at": stamp}]
    local = {
        1: (
            date(2026, 10, 31),
            "doc-2026-10-31-[1].md",
            get_sync._parse_ts(stamp),
        )
    }

    _, updated = get_sync.build_lists(docs, local)

    assert updated == []


def test_build_lists_treats_legacy_same_day_file_as_stale_once():
    docs = [{"id": 1, "content_updated_at": "2026-10-31T12:00:00+08:00"}]
    local = {1: (date(2026, 10, 31), "doc-2026-10-31-[1].md", None)}

    _, updated = get_sync.build_lists(docs, local)

    assert len(updated) == 1


def test_scan_local_reads_exact_timestamp_from_sync_state(tmp_path: Path):
    stamp = "2026-10-31T12:00:00+08:00"
    name = "doc-2026-10-31-[1].md"
    (tmp_path / name).write_text("content")
    (tmp_path / ".sync-state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "documents": {"1": {"file": name, "content_updated_at": stamp}},
            }
        )
    )

    local = get_sync.scan_local(tmp_path)

    assert local[1][2] == get_sync._parse_ts(stamp)


def test_select_delta_keeps_existing_updates_older_than_corpus_cutoff():
    sync = {
        "new": [],
        "updated": [
            {
                "id": 2,
                "title": "legitimate delta",
                "content_updated_at": "2026-10-26T12:00:00+08:00",
                "local_date": "2026-08-01",
            }
        ],
    }

    delta = apply_sync.select_delta(sync, date(2026, 10, 27))

    assert [doc["id"] for doc in delta] == [2]


def test_select_delta_still_defers_historical_missing_documents():
    sync = {
        "new": [
            {
                "id": 1,
                "title": "historical",
                "content_updated_at": "2026-01-01T00:00:00+08:00",
            },
            {
                "id": 2,
                "title": "recent",
                "content_updated_at": "2026-10-27T00:00:00+08:00",
            },
        ],
        "updated": [],
    }

    delta = apply_sync.select_delta(sync, date(2026, 10, 27))

    assert [doc["id"] for doc in delta] == [2]


def test_latest_sync_json_filters_by_book(tmp_path: Path):
    other = tmp_path / "sync-updates-other-20261102.json"
    other.write_text("{}")
    expected = tmp_path / "sync-updates-gom7xx-20261101.json"
    expected.write_text("{}")
    other.touch()

    assert apply_sync.latest_sync_json(tmp_path, "gom7xx") == expected


def test_validate_sync_book_rejects_mismatch():
    with pytest.raises(ValueError, match="different book"):
        apply_sync.validate_sync_book({"book": {"book_id": 123}}, {"book_id": 456})


def test_fetch_doc_list_reads_extra_pages_until_short_page(monkeypatch):
    pages = {
        0: [{"id": i} for i in range(50)],
        50: [{"id": i} for i in range(50, 100)],
        100: [{"id": i} for i in range(100, 125)],
    }

    def fake_call(tool, args):
        assert tool == "skylark_doc_list"
        return pages[args["offset"]]

    monkeypatch.setattr(get_sync, "call_tool", fake_call)

    docs = get_sync.fetch_doc_list(123, 50)

    assert len(docs) == 125
    assert docs[-1]["id"] == 124


def test_fetch_doc_list_rejects_duplicate_remote_ids(monkeypatch):
    monkeypatch.setattr(
        get_sync, "call_tool", lambda tool, args: [{"id": 1}, {"id": 1}]
    )

    with pytest.raises(get_sync.SkylarkError, match="duplicate document ID 1"):
        get_sync.fetch_doc_list(123, 2)


def test_publish_documents_copies_assets_and_backs_up_old_document(tmp_path: Path):
    corpus = tmp_path / "corpus"
    raw_dir = tmp_path / "raw"
    ocr_dir = tmp_path / "ocr"
    backup = tmp_path / "backup"
    corpus.mkdir()
    raw_dir.mkdir()
    ocr_dir.mkdir()
    old = corpus / "old-2026-01-01-[1].md"
    old.write_text("old")
    name = "new-2026-11-02-[1].md"
    raw = raw_dir / name
    raw.write_text("raw")
    (ocr_dir / name).write_text("![image](assets/new/image.jpg)")
    asset = ocr_dir / "assets" / "new" / "image.jpg"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"jpeg")

    result = apply_sync.publish_documents(
        [(raw, {"id": 1, "kind": "updated"})], ocr_dir, corpus, backup
    )

    assert (corpus / name).read_text() == "![image](assets/new/image.jpg)"
    assert not old.exists()
    assert (backup / old.name).read_text() == "old"
    assert (corpus / "assets" / "new" / "image.jpg").read_bytes() == b"jpeg"
    assert result == {"added": 1, "replaced": 1, "assets": 1}


def test_publish_resume_preserves_original_same_name_backup(tmp_path: Path):
    corpus = tmp_path / "corpus"
    raw_dir = tmp_path / "raw"
    ocr_dir = tmp_path / "ocr"
    backup = tmp_path / "backup"
    for directory in (corpus, raw_dir, ocr_dir, backup):
        directory.mkdir()
    name = "doc-2026-01-01-[1].md"
    (corpus / name).write_text("new")
    (raw_dir / name).write_text("new")
    (ocr_dir / name).write_text("new")
    (backup / name).write_text("original")

    apply_sync.publish_documents(
        [(raw_dir / name, {"id": 1, "kind": "updated"})],
        ocr_dir,
        corpus,
        backup,
    )

    assert (backup / name).read_text() == "original"


def test_publish_documents_preserves_old_document_when_source_is_missing(
    tmp_path: Path,
):
    corpus = tmp_path / "corpus"
    raw_dir = tmp_path / "raw"
    ocr_dir = tmp_path / "ocr"
    corpus.mkdir()
    raw_dir.mkdir()
    ocr_dir.mkdir()
    old = corpus / "old-2026-01-01-[1].md"
    old.write_text("old")
    missing = raw_dir / "new-2026-11-02-[1].md"

    with pytest.raises(FileNotFoundError):
        apply_sync.publish_documents(
            [(missing, {"id": 1, "kind": "updated"})],
            ocr_dir,
            corpus,
            tmp_path / "backup",
        )

    assert old.read_text() == "old"


def test_sync_state_round_trip(tmp_path: Path):
    records = {
        "1": {
            "file": "doc-2026-10-31-[1].md",
            "content_updated_at": "2026-10-31T12:00:00+08:00",
        }
    }

    get_sync.save_sync_state(tmp_path, records)

    assert get_sync.load_sync_state(tmp_path) == records
    assert (tmp_path / ".sync-state.json").is_file()


def test_build_structure_lists_detects_rename_and_move():
    stamp = "2026-10-31T12:00:00+08:00"
    docs = [
        {"id": 1, "title": "New title", "content_updated_at": stamp},
        {"id": 2, "title": "Same title", "content_updated_at": stamp},
    ]
    local = {
        1: (
            date(2026, 10, 31),
            "parent__Old title-2026-10-31-[1].md",
            get_sync._parse_ts(stamp),
        ),
        2: (
            date(2026, 10, 31),
            "old-parent__Same title-2026-10-31-[2].md",
            get_sync._parse_ts(stamp),
        ),
    }

    renamed, moved = get_sync.build_structure_lists(
        docs, local, {1: ["parent"], 2: ["new-parent"]}
    )

    assert [item["id"] for item in renamed] == [1]
    assert renamed[0]["file"] == "parent__New-title-2026-10-31-[1].md"
    assert [item["id"] for item in moved] == [2]
    assert moved[0]["file"] == "new-parent__Same-title-2026-10-31-[2].md"


def test_build_structure_lists_ignores_documents_missing_from_toc():
    stamp = "2026-10-31T12:00:00+08:00"
    docs = [{"id": 1, "title": "New title", "content_updated_at": stamp}]
    local = {1: (date(2026, 10, 31), "old-2026-10-31-[1].md", None)}

    renamed, moved = get_sync.build_structure_lists(docs, local, {})

    assert renamed == []
    assert moved == []


def test_build_deletions_finds_only_local_documents_absent_remotely():
    docs = [{"id": 1}]
    local = {
        1: (date(2026, 1, 1), "keep-2026-01-01-[1].md", None),
        2: (date(2026, 1, 2), "remove-2026-01-02-[2].md", None),
    }

    deleted = get_sync.build_deletions(docs, local)

    assert deleted == [
        {
            "id": 2,
            "local_file": "remove-2026-01-02-[2].md",
            "local_date": "2026-01-02",
        }
    ]


def test_select_delta_includes_rename_and_move_regardless_of_cutoff():
    sync = {
        "new": [],
        "updated": [],
        "renamed": [
            {
                "id": 1,
                "title": "renamed",
                "content_updated_at": "2026-01-01T00:00:00+08:00",
                "local_date": "2026-01-01",
            }
        ],
        "moved": [
            {
                "id": 2,
                "title": "moved",
                "content_updated_at": "2026-01-02T00:00:00+08:00",
                "local_date": "2026-01-02",
            }
        ],
    }

    delta = apply_sync.select_delta(sync, date(2026, 10, 27))

    assert [(doc["id"], doc["kind"]) for doc in delta] == [
        (1, "renamed"),
        (2, "moved"),
    ]


def complete_snapshot(remote_ids, deleted):
    return {
        "snapshot": {
            "complete": True,
            "document_ids": remote_ids,
            "document_count": len(remote_ids),
        },
        "deleted": deleted,
    }


def test_remove_documents_backs_up_deletion_and_is_idempotent(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    name = "remove-2026-01-02-[2].md"
    path = corpus / name
    path.write_text("remove me")
    sync = complete_snapshot([], [{"id": 2, "local_file": name}])

    planned = apply_sync.plan_deletions(sync, corpus)
    result = apply_sync.remove_documents(planned, tmp_path / "backup")

    assert result == {"deleted": 1, "already_absent": 0}
    assert not path.exists()
    assert (tmp_path / "backup" / name).read_text() == "remove me"
    assert apply_sync.remove_documents(planned, tmp_path / "backup") == {
        "deleted": 0,
        "already_absent": 1,
    }


def test_plan_deletions_requires_complete_snapshot(tmp_path: Path):
    with pytest.raises(ValueError, match="complete remote snapshot"):
        apply_sync.plan_deletions(
            {"deleted": [{"id": 2, "local_file": "remove-[2].md"}]}, tmp_path
        )


def test_plan_deletions_rejects_duplicate_remote_ids(tmp_path: Path):
    sync = complete_snapshot([1, 1], [{"id": 2, "local_file": "remove-[2].md"}])

    with pytest.raises(ValueError, match="duplicate remote document IDs"):
        apply_sync.plan_deletions(sync, tmp_path)


def test_plan_deletions_rejects_document_still_present_remotely(tmp_path: Path):
    sync = complete_snapshot([2], [{"id": 2, "local_file": "remove-[2].md"}])

    with pytest.raises(ValueError, match="still present in remote snapshot"):
        apply_sync.plan_deletions(sync, tmp_path)


def test_plan_deletions_preserves_locally_changed_file(tmp_path: Path):
    name = "remove-2026-01-02-[2].md"
    path = tmp_path / name
    path.write_text("old")
    stat = path.stat()
    path.write_text("locally changed")
    sync = complete_snapshot(
        [],
        [
            {
                "id": 2,
                "local_file": name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        ],
    )

    with pytest.raises(RuntimeError, match="changed since sync plan"):
        apply_sync.plan_deletions(sync, tmp_path)

    assert path.read_text() == "locally changed"


def test_apply_main_handles_rename_move_delete_and_state(tmp_path: Path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    stamp = "2026-11-01T12:00:00+08:00"
    old_names = {
        1: "parent__Old-one-2026-11-01-[1].md",
        2: "old-parent__Two-2026-11-01-[2].md",
        3: "deleted-2026-11-01-[3].md",
    }
    for doc_id, name in old_names.items():
        (corpus / name).write_text(f"old {doc_id}")
    get_sync.save_sync_state(
        corpus,
        {
            str(doc_id): {"file": name, "content_updated_at": stamp}
            for doc_id, name in old_names.items()
        },
    )
    deleted_path = corpus / old_names[3]
    deleted_stat = deleted_path.stat()
    sync = complete_snapshot(
        [1, 2],
        [
            {
                "id": 3,
                "local_file": old_names[3],
                "size": deleted_stat.st_size,
                "mtime_ns": deleted_stat.st_mtime_ns,
            }
        ],
    )
    sync.update(
        book={"book_id": 123},
        new=[],
        updated=[],
        renamed=[
            {
                "id": 1,
                "title": "One",
                "content_updated_at": stamp,
                "local_date": "2026-11-01",
            }
        ],
        moved=[
            {
                "id": 2,
                "title": "Two",
                "content_updated_at": stamp,
                "local_date": "2026-11-01",
            }
        ],
    )
    sync_path = tmp_path / "sync.json"
    sync_path.write_text(json.dumps(sync))
    staging = tmp_path / "staging"
    monkeypatch.setattr(
        apply_sync, "resolve_book", lambda url: {"book_id": 123, "slug": "uat"}
    )
    monkeypatch.setattr(
        apply_sync,
        "export_doc",
        lambda doc_id, doc_format=None: (
            f"new {doc_id}",
            {
                "title": {1: "One", 2: "Two"}[doc_id],
                "content_updated_at": stamp,
            },
        ),
    )
    monkeypatch.setattr(
        apply_sync, "toc_paths", lambda book_id: {1: ["parent"], 2: ["new-parent"]}
    )

    def fake_ocr(files, out_dir):
        for path in files:
            (out_dir / path.name).write_text(path.read_text())
        return 0

    monkeypatch.setattr(apply_sync, "run_mdocr", fake_ocr)
    monkeypatch.setattr(
        "sys.argv",
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

    assert (corpus / "parent__One-2026-11-01-[1].md").read_text() == "new 1"
    assert (corpus / "new-parent__Two-2026-11-01-[2].md").read_text() == "new 2"
    assert all(not (corpus / name).exists() for name in old_names.values())
    assert (staging / "backup" / old_names[3]).read_text() == "old 3"
    state = get_sync.load_sync_state(corpus)
    assert set(state) == {"1", "2"}
    assert state["1"]["file"] == "parent__One-2026-11-01-[1].md"
    manifest = json.loads((staging / "manifest.json").read_text())
    assert {doc["kind"] for doc in manifest["exported"]} == {"renamed", "moved"}
    assert manifest["published"]["deleted"] == 1

    def unexpected_export(doc_id, doc_format=None):
        raise AssertionError("completed export was not reused")

    monkeypatch.setattr(apply_sync, "export_doc", unexpected_export)
    apply_sync.main()
    assert get_sync.load_sync_state(corpus) == state


def test_get_main_writes_structure_changes_and_complete_snapshot(
    tmp_path: Path, monkeypatch
):
    corpus = tmp_path / "corpus"
    output = tmp_path / "out"
    corpus.mkdir()
    stamp = "2026-11-01T12:00:00+08:00"
    old_names = {
        1: "parent__Old-one-2026-11-01-[1].md",
        2: "old-parent__Two-2026-11-01-[2].md",
        3: "deleted-2026-11-01-[3].md",
    }
    for name in old_names.values():
        (corpus / name).write_text("old")
    get_sync.save_sync_state(
        corpus,
        {
            str(doc_id): {"file": name, "content_updated_at": stamp}
            for doc_id, name in old_names.items()
        },
    )
    docs = [
        {
            "id": 1,
            "title": "One",
            "slug": "one",
            "format": "lake",
            "content_updated_at": stamp,
        },
        {
            "id": 2,
            "title": "Two",
            "slug": "two",
            "format": "lake",
            "content_updated_at": stamp,
        },
    ]
    monkeypatch.setattr(
        get_sync,
        "resolve_book",
        lambda url: {
            "book_id": 123,
            "slug": "uat",
            "name": "UAT",
            "items_count": 2,
        },
    )
    monkeypatch.setattr(
        get_sync, "fetch_doc_list", lambda book_id, count, cache_dir=None: docs
    )
    monkeypatch.setattr(
        get_sync, "toc_paths", lambda book_id: {1: ["parent"], 2: ["new-parent"]}
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "get_sync_updates.py",
            "-i",
            str(corpus),
            "-s",
            "https://example.invalid/book",
            "-o",
            str(output),
            "--json",
        ],
    )

    get_sync.main()

    result_path = output / f"sync-updates-uat-{date.today():%Y%m%d}.json"
    payload = json.loads(result_path.read_text())
    assert payload["snapshot"] == {
        "complete": True,
        "document_count": 2,
        "document_ids": [1, 2],
    }
    assert [item["id"] for item in payload["renamed"]] == [1]
    assert [item["id"] for item in payload["moved"]] == [2]
    assert [item["id"] for item in payload["deleted"]] == [3]
    assert payload["deleted"][0]["local_file"] == old_names[3]
    assert payload["deleted"][0]["size"] == 3

    state = get_sync.load_sync_state(corpus)
    state["1"]["content_updated_at"] = "2026-10-31T12:00:00+08:00"
    get_sync.save_sync_state(corpus, state)
    monkeypatch.setattr(
        get_sync,
        "resolve_book",
        lambda url: {
            "book_id": 123,
            "slug": "uat",
            "name": "UAT",
            "items_count": 0,
        },
    )
    get_sync.main()
    payload = json.loads(result_path.read_text())
    assert payload["snapshot"]["complete"] is False
    assert payload["updated"][0]["format"] == "lake"


def test_fetch_doc_list_resumes_from_page_cache(tmp_path: Path, monkeypatch):
    calls = []

    def fake_call(tool, args):
        calls.append(args["offset"])
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(get_sync, "call_tool", fake_call)

    assert len(get_sync.fetch_doc_list(123, 2, tmp_path / "pages")) == 2
    assert len(get_sync.fetch_doc_list(123, 2, tmp_path / "pages")) == 2
    assert calls == [0]


def test_ocr_resume_retries_only_incomplete_documents(tmp_path: Path, monkeypatch):
    raw = tmp_path / "raw"
    output = tmp_path / "ocr"
    raw.mkdir()
    output.mkdir()
    files = [raw / "a.md", raw / "b.md"]
    for path in files:
        path.write_text(path.stem)
    attempts = []

    def fake_ocr(batch, out_dir):
        path = batch[0]
        attempts.append(path.name)
        (out_dir / path.name).write_text("partial" if path.name == "b.md" else "done")
        return 1 if path.name == "b.md" else 0

    monkeypatch.setattr(apply_sync, "run_mdocr", fake_ocr)
    with pytest.raises(RuntimeError, match="mdocr exited 1"):
        apply_sync.run_ocr_with_resume(files, output, tmp_path / "state")
    assert attempts == ["a.md", "b.md"]

    attempts.clear()

    def complete_ocr(batch, out_dir):
        attempts.append(batch[0].name)
        (out_dir / batch[0].name).write_text("done")
        return 0

    monkeypatch.setattr(apply_sync, "run_mdocr", complete_ocr)
    apply_sync.run_ocr_with_resume(files, output, tmp_path / "state")

    assert attempts == ["b.md"]
