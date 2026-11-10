"""Apply recovery uses retained staging without publishing incomplete batches."""

import json
import shlex
import sys
from pathlib import Path

import pytest

from scripts import apply_sync_updates as apply_sync
from scripts import get_sync_updates as get_sync


@pytest.fixture
def run(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for doc_id in (1, 2, 3):
        (corpus / f"Old-{doc_id}-2026-01-01-[{doc_id}].md").write_text(f"old {doc_id}")
    get_sync.save_sync_state(corpus, {"3": {"file": "Old-3-2026-01-01-[3].md"}})
    staging = tmp_path / "staging"
    stamp = "2026-11-01T12:00:00+08:00"
    plan = tmp_path / "sync.json"
    plan.write_text(
        json.dumps(
            {
                "book": {"book_id": 123},
                "updated": [
                    {
                        "id": doc_id,
                        "title": f"Doc-{doc_id}",
                        "content_updated_at": stamp,
                        "local_date": "2026-01-01",
                    }
                    for doc_id in (1, 2)
                ],
                "deleted": [{"id": 3, "local_file": "Old-3-2026-01-01-[3].md"}],
                "snapshot": {
                    "complete": True,
                    "document_count": 2,
                    "document_ids": [1, 2],
                },
            }
        )
    )
    monkeypatch.setattr(
        apply_sync, "resolve_book", lambda url: {"book_id": 123, "slug": "test"}
    )
    monkeypatch.setattr(apply_sync, "toc_paths", lambda book_id: {})
    exports = []
    ocr = []
    failures = set()

    def export(doc_id, doc_format=None):
        exports.append(doc_id)
        if doc_id in failures:
            raise apply_sync.SkylarkError("upstream unavailable")
        return f"new {doc_id}", {"content_updated_at": stamp}

    def process(files, out_dir):
        for path in files:
            ocr.append(path.name)
            (out_dir / path.name).write_text(path.read_text())
        return 0

    monkeypatch.setattr(apply_sync, "export_doc", export)
    monkeypatch.setattr(apply_sync, "run_mdocr", process)

    def invoke(*extra):
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
                str(plan),
                "--staging",
                str(staging),
                "--cutoff",
                "2026-01-01",
                *extra,
            ],
        )
        apply_sync.main()

    return invoke, corpus, staging, failures, exports, ocr, plan


def snapshot(corpus):
    return {
        str(path.relative_to(corpus)): path.read_bytes()
        for path in corpus.rglob("*")
        if path.is_file() and path.name != ".sync-state.json"
    }


def test_download_progress_survives_later_export_failure(run):
    invoke, corpus, staging, failures, _exports, _ocr, _plan = run
    failures.add(2)
    with pytest.raises(SystemExit):
        invoke()
    state = json.loads((corpus / ".sync-state.json").read_text())
    records = state["books"]["adhoc"]["documents"]
    assert records["1"]["steps"]["download"]["status"] == "completed"
    assert (
        records["1"]["steps"]["download"]["version"]["utc"]
        == "2026-11-01T12:00:00+08:00"
    )
    assert (staging / "raw" / records["1"]["steps"]["download"]["file"]).is_file()
    assert records["2"]["steps"]["download"]["status"] == "failed"
    assert "graph" not in records["1"]["steps"]
    assert records["1"]["corpus"]["status"] == "captured"
    assert "corpus" not in records["1"]["steps"]
    assert not records["1"].get("graph")
    assert state["active_run"] is not None


def test_download_uses_utc8_filename_date_and_keeps_exact_version(run, monkeypatch):
    invoke, corpus, staging, _failures, _exports, _ocr, _plan = run
    monkeypatch.setattr(
        apply_sync,
        "export_doc",
        lambda doc_id, doc_format=None: (
            "body",
            {"content_updated_at": "2026-11-01T18:30:00Z"},
        ),
    )
    invoke("--no-merge")
    assert (staging / "raw" / "Doc-1-2026-11-02-[1].md").is_file()
    state = json.loads((corpus / ".sync-state.json").read_text())
    download = state["books"]["adhoc"]["documents"]["1"]["steps"]["download"]
    assert download["version"]["utc"] == "2026-11-02T02:30:00+08:00"
    assert download["version"]["value"] == "2026-11-01T18:30:00Z"


@pytest.mark.parametrize("no_merge", [False, True])
def test_export_failure_preserves_corpus_and_successful_staging(run, no_merge, capsys):
    invoke, corpus, staging, failures, _exports, _ocr, plan = run
    before = snapshot(corpus)
    failures.add(2)

    with pytest.raises(SystemExit) as error:
        invoke(*(["--no-merge"] if no_merge else []))

    assert error.value.code != 0
    assert snapshot(corpus) == before
    assert (staging / "raw" / "Doc-1-2026-11-01-[1].md").read_text() == "new 1"
    manifest = json.loads((staging / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["failures"] == [
        {"stage": "export", "id": 2, "reason": "upstream unavailable"}
    ]
    assert "published" not in manifest
    progress = json.loads((staging / "export-progress.json").read_text())
    assert set(progress["documents"]) == {"1"}
    output = capsys.readouterr().err
    assert "Retry:" in output
    assert f"--staging {staging}" in output
    assert f"--sync-json {plan}" in output
    assert "valid export/OCR checkpoints will be reused" in output


def test_export_resume_reuses_success_and_publishes_entire_fixed_plan(run):
    invoke, corpus, staging, failures, exports, ocr, _plan = run
    failures.add(2)
    with pytest.raises(SystemExit):
        invoke()
    exports.clear()
    ocr.clear()
    failures.clear()

    invoke()

    assert exports == [2]
    assert ocr == ["Doc-2-2026-11-01-[2].md"]
    assert (corpus / "Doc-1-2026-11-01-[1].md").read_text() == "new 1"
    assert (corpus / "Doc-2-2026-11-01-[2].md").read_text() == "new 2"
    assert not (corpus / "Old-3-2026-01-01-[3].md").exists()
    assert set(get_sync.load_sync_state(corpus)) == {"1", "2"}
    assert json.loads((staging / "manifest.json").read_text())["status"] == "completed"


def test_missing_raw_export_regenerates_ocr_instead_of_reusing_stale_output(
    run, monkeypatch
):
    invoke, corpus, staging, _failures, exports, ocr, _plan = run
    invoke("--no-merge")
    (staging / "raw" / "Doc-1-2026-11-01-[1].md").unlink()
    exports.clear()
    ocr.clear()

    def changed_export(doc_id, doc_format=None):
        exports.append(doc_id)
        return "regenerated", {"content_updated_at": "2026-11-01T12:00:00+08:00"}

    monkeypatch.setattr(apply_sync, "export_doc", changed_export)
    invoke()

    assert exports == [1]
    assert ocr == ["Doc-1-2026-11-01-[1].md"]
    assert (corpus / "Doc-1-2026-11-01-[1].md").read_text() == "regenerated"


def test_ocr_failure_retains_manifest_and_resumes_only_failed_files(run, monkeypatch):
    invoke, corpus, staging, _failures, exports, ocr, _plan = run
    before = snapshot(corpus)
    process = apply_sync.run_mdocr

    def fail_second(files, out_dir):
        if "[2]" in files[0].name:
            return 1
        return process(files, out_dir)

    monkeypatch.setattr(apply_sync, "run_mdocr", fail_second)
    with pytest.raises(SystemExit) as error:
        invoke()

    assert error.value.code != 0
    assert snapshot(corpus) == before
    manifest = json.loads((staging / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["failures"][0]["stage"] == "ocr"
    assert manifest["failures"][0]["file"] == "Doc-2-2026-11-01-[2].md"
    exports.clear()
    ocr.clear()
    monkeypatch.setattr(apply_sync, "run_mdocr", process)

    invoke()

    assert exports == []
    assert ocr == ["Doc-2-2026-11-01-[2].md"]
    assert (corpus / "Doc-1-2026-11-01-[1].md").read_text() == "new 1"
    assert (corpus / "Doc-2-2026-11-01-[2].md").read_text() == "new 2"


@pytest.mark.parametrize("change", ["cutoff", "settings", "plan"])
def test_direct_apply_rejects_incompatible_checkpoint(run, monkeypatch, change):
    invoke, corpus, _staging, _failures, exports, _ocr, plan = run
    invoke("--no-merge")
    before = snapshot(corpus)
    exports.clear()
    if change == "settings":
        monkeypatch.setenv("SKYLARK_CONCURRENCY", "3")
    if change == "plan":
        plan.write_text(plan.read_text() + " ")

    with pytest.raises(SystemExit, match="different sync plan|configuration changed"):
        invoke(*(["--cutoff", "2026-10-01"] if change == "cutoff" else []))

    assert exports == []
    assert snapshot(corpus) == before


def test_publication_failure_records_failed_manifest_without_sync_state_advance(
    run, monkeypatch
):
    invoke, corpus, staging, _failures, _exports, _ocr, _plan = run
    state = get_sync.load_sync_state(corpus)
    copy = apply_sync.shutil.copy2

    def fail_publication(source, destination):
        if destination.parent == corpus:
            raise OSError("disk unavailable")
        copy(source, destination)

    monkeypatch.setattr(apply_sync.shutil, "copy2", fail_publication)
    with pytest.raises(SystemExit) as error:
        invoke()

    assert error.value.code != 0
    assert get_sync.load_sync_state(corpus) == state
    assert (corpus / "Old-3-2026-01-01-[3].md").exists()
    manifest = json.loads((staging / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["failures"][0]["stage"] == "publish"


def test_partial_corpus_publication_records_only_completed_documents(run, monkeypatch):
    invoke, corpus, _staging, _failures, _exports, _ocr, _plan = run
    copy = apply_sync._atomic_copy

    def fail_second_document(source, destination):
        if destination.parent == corpus and "[2].md" in destination.name:
            raise OSError("second publication failed")
        return copy(source, destination)

    monkeypatch.setattr(apply_sync, "_atomic_copy", fail_second_document)
    with pytest.raises(SystemExit):
        invoke()
    state = json.loads((corpus / ".sync-state.json").read_text())
    records = state["books"]["adhoc"]["documents"]
    assert records["1"]["steps"]["corpus"]["status"] == "completed"
    assert records["1"]["corpus"]["status"] == "published"
    assert records["2"]["corpus"]["status"] == "captured"
    assert "corpus" not in records["2"]["steps"]
    assert not records["1"].get("graph")
    assert not records["2"].get("graph")
    assert (corpus / "Doc-1-2026-11-01-[1].md").read_text() == "new 1"
    assert (corpus / "Old-2-2026-01-01-[2].md").read_text() == "old 2"


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_missing_ocr_regeneration_never_trusts_partial_output(
    run, monkeypatch, cancel
):
    invoke, corpus, staging, _failures, exports, ocr, _plan = run
    invoke("--no-merge")
    (staging / "md" / "Doc-1-2026-11-01-[1].md").unlink()
    before = snapshot(corpus)
    process = apply_sync.run_mdocr

    def interrupted(files, out_dir):
        (out_dir / files[0].name).write_text("partial OCR")
        if cancel:
            raise KeyboardInterrupt()
        return 1

    monkeypatch.setattr(apply_sync, "run_mdocr", interrupted)
    with pytest.raises(KeyboardInterrupt if cancel else SystemExit):
        invoke()
    assert snapshot(corpus) == before
    manifest = json.loads((staging / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    completed = json.loads((staging / "checkpoints" / "ocr-complete.json").read_text())
    assert "Doc-1-2026-11-01-[1].md" not in completed["files"]
    exports.clear()
    ocr.clear()
    monkeypatch.setattr(apply_sync, "run_mdocr", process)

    invoke()

    assert exports == []
    assert ocr == ["Doc-1-2026-11-01-[1].md"]
    assert (corpus / "Doc-1-2026-11-01-[1].md").read_text() == "new 1"


@pytest.mark.parametrize("no_merge", [False, True])
def test_cancelled_export_reports_recovery_and_reuses_finished_checkpoints(
    run, monkeypatch, capsys, no_merge
):
    invoke, corpus, staging, _failures, exports, ocr, plan = run
    before = snapshot(corpus)
    export = apply_sync.export_doc

    def interrupted(doc_id, doc_format=None):
        if doc_id == 2:
            raise KeyboardInterrupt()
        return export(doc_id, doc_format)

    monkeypatch.setattr(apply_sync, "export_doc", interrupted)
    with pytest.raises(KeyboardInterrupt):
        invoke(*(["--no-merge"] if no_merge else []))
    assert snapshot(corpus) == before
    assert ocr == []
    progress = json.loads((staging / "export-progress.json").read_text())
    assert set(progress["documents"]) == {"1"}
    manifest = json.loads((staging / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["failures"] == [{"stage": "export", "id": 2, "reason": "cancelled"}]
    assert "published" not in manifest
    output = capsys.readouterr().err
    assert "[export]" in output and "cancelled" in output
    assert "valid export/OCR checkpoints will be reused" in output
    retry = next(
        line.removeprefix("Retry: ")
        for line in output.splitlines()
        if line.startswith("Retry: ")
    )
    command = shlex.split(retry)
    assert command[command.index("--staging") + 1] == str(staging)
    assert command[command.index("--sync-json") + 1] == str(plan)
    assert ("--no-merge" in command) is no_merge
    exports.clear()
    monkeypatch.setattr(apply_sync, "export_doc", export)
    monkeypatch.setattr(sys, "argv", command[1:])
    apply_sync.main()

    assert exports == [2]
    if no_merge:
        assert snapshot(corpus) == before
    else:
        assert (corpus / "Doc-1-2026-11-01-[1].md").read_text() == "new 1"
        assert (corpus / "Doc-2-2026-11-01-[2].md").read_text() == "new 2"


@pytest.mark.parametrize("state", ["not JSON", '{"version": 2, "documents": {}}'])
def test_invalid_sync_state_fails_before_publication_with_retry_diagnostics(
    run, capsys, state
):
    invoke, corpus, staging, _failures, _exports, _ocr, _plan = run
    (corpus / ".sync-state.json").write_text(state)
    before = snapshot(corpus)

    with pytest.raises(SystemExit) as error:
        invoke()

    assert error.value.code != 0
    assert snapshot(corpus) == before
    assert not staging.exists()
    assert (corpus / ".sync-state.json").read_text() == state
    output = capsys.readouterr().err
    assert "[state] failed" in output and "Retry:" in output


@pytest.mark.parametrize(
    "stage", ["export", "ocr", "publish", "cancel", "export_cancel"]
)
def test_failure_retry_output_survives_unwritable_diagnostic_manifest(
    run, monkeypatch, capsys, stage
):
    invoke, corpus, _staging, failures, _exports, _ocr, _plan = run
    before = snapshot(corpus)
    if stage == "export":
        failures.add(2)
    elif stage == "export_cancel":
        export = apply_sync.export_doc

        def cancel_export(doc_id, doc_format=None):
            if doc_id == 2:
                raise KeyboardInterrupt()
            return export(doc_id, doc_format)

        monkeypatch.setattr(apply_sync, "export_doc", cancel_export)
    elif stage == "ocr":
        monkeypatch.setattr(apply_sync, "run_mdocr", lambda files, out_dir: 1)
    elif stage == "cancel":

        def cancel(files, out_dir):
            raise KeyboardInterrupt()

        monkeypatch.setattr(apply_sync, "run_mdocr", cancel)
    else:
        copy = apply_sync._atomic_copy

        def fail_publication(source, destination):
            if destination.parent == corpus:
                raise OSError("publication disk full")
            return copy(source, destination)

        monkeypatch.setattr(apply_sync, "_atomic_copy", fail_publication)
    replace = Path.replace

    def cannot_save_failure(path, target):
        if (
            path.name == "manifest.json.tmp"
            and json.loads(path.read_text())["status"] == "failed"
        ):
            raise OSError("disk full token=credential-value")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", cannot_save_failure)
    cancelled = stage in {"cancel", "export_cancel"}
    with pytest.raises(KeyboardInterrupt if cancelled else SystemExit) as error:
        invoke()

    if not cancelled:
        assert error.value.code != 0
    assert snapshot(corpus) == before
    output = capsys.readouterr().err
    assert "Retry:" in output
    assert "cannot persist failure manifest" in output
    assert "credential-value" not in output
    expected_stage = {"cancel": "ocr", "export_cancel": "export"}.get(stage, stage)
    assert f"[{expected_stage}]" in output
