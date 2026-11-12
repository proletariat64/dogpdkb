"""Wash cleanup, historical migration and deployment, through the installed CLI.

Black-box seams: ``dogpdkb sync`` (automatic cleanup at a finished batch),
``dogpdkb wash`` (inventory/migration of historical work directories) and
``dogpdkb anomalies`` (necessary inputs stay readable after cleanup). Every wash
directory a cleanup test deletes is constructed under ``tmp_path``; the real
corpus, graph and private artifacts are never touched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.test_unified_compile import CompileWorkspace
from tests.test_unified_sync import DOCUMENTS, Workspace, read_json, write_json


class WashWorkspace(CompileWorkspace):
    """A full batch workspace with explicit durable results and OCR cache."""

    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path, DOCUMENTS)
        text = self.config.read_text(encoding="utf-8").replace(
            'web="web"', 'web="web"\nresults="results"\nocr_cache="ocr-cache"'
        )
        self.config.write_text(text, encoding="utf-8")
        self.results = tmp_path / "results" / "fund"
        self.ocr_cache = tmp_path / "ocr-cache" / "fund"


@pytest.fixture
def workspace(tmp_path: Path) -> WashWorkspace:
    return WashWorkspace(tmp_path)


def finished_batch(workspace: Workspace) -> Path:
    """One batch that reaches a terminal result; return its work directory."""
    completed = workspace.sync()
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["status"] in {"completed", "partial"}, summary
    return Path(summary["run_dir"])


def test_a_finished_batch_cleans_earlier_finished_work_directories(
    workspace: Workspace,
):
    first = finished_batch(workspace)
    assert first.is_dir()
    first_result = workspace.results / "runs" / f"{first.name}.json"
    assert first_result.is_file()

    second = finished_batch(workspace)

    # Only the most recent finished batch keeps its wash work directory.
    assert not first.exists()
    assert second.is_dir()
    assert workspace.runs() == [second]
    # The durable history of both batches survives the wash cleanup.
    assert read_json(first_result)["run_id"] == first.name
    assert (workspace.results / "runs" / f"{second.name}.json").is_file()


def canonical_fingerprints(workspace: Workspace) -> tuple[dict, dict]:
    """Published corpus text plus graph structure and success versions.

    Timestamps and stub report churn are excluded: the invariant is that cleanup
    removes no published knowledge and advances no success version.
    """
    files = {
        str(path.relative_to(workspace.corpus)): path.read_bytes()
        for path in sorted(workspace.corpus.glob("*.md"))
    }
    graph = read_json(workspace.corpus / "graphify-out" / "graph.json")
    files["graph-nodes"] = sorted(
        (node["id"], node.get("source_file")) for node in graph["nodes"]
    )
    files["graph-links"] = sorted(
        (link["source"], link["target"], link.get("relation"))
        for link in graph.get("links", [])
    )
    state = read_json(workspace.corpus / ".sync-state.json")
    published = {
        doc_id: {
            "corpus": {
                key: (row.get("corpus") or {}).get(key)
                for key in ("status", "version", "file")
            },
            "graph": {
                key: (row.get("graph") or {}).get(key) for key in ("status", "version")
            },
        }
        for doc_id, row in state["books"]["fund"]["documents"].items()
    }
    return files, published


def state_path_references(workspace: Workspace) -> list[Path]:
    """Every absolute file reference the canonical state still holds."""
    state = read_json(workspace.corpus / ".sync-state.json")
    found = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value.startswith("/"):
            path = Path(value)
            if path.suffix or path.is_dir():
                found.append(path)

    walk(state)
    return found


def test_cleanup_leaves_the_canonical_corpus_graph_and_state_intact(
    workspace: Workspace,
):
    finished_batch(workspace)
    # A no-update batch reaches the same steady state and cleans the first run.
    finished_batch(workspace)
    before = canonical_fingerprints(workspace)
    assert before[0], "expected a published corpus before cleanup"

    finished_batch(workspace)

    assert canonical_fingerprints(workspace) == before
    state = read_json(workspace.corpus / ".sync-state.json")
    assert state["active_run"] is None
    # No canonical reference is left pointing into a removed work directory.
    for path in state_path_references(workspace):
        assert path.exists(), path


def write_run_state(run: Path, **changes: Any) -> dict[str, Any]:
    state = read_json(run / "run.json")
    state.update(changes)
    write_json(run / "run.json", state)
    return state


def clone_run(source: Path, target: Path, **changes: Any) -> Path:
    """A constructed wash work directory copied from a real finished batch."""
    import shutil

    shutil.copytree(source, target)
    state = read_json(target / "run.json")
    state.update(run_id=target.name, run_dir=str(target))
    state.update(changes)
    write_json(target / "run.json", state)
    return target


def archive_clone(workspace: Workspace, run: Path) -> Path:
    """Give a constructed run the durable result a real finish would archive."""
    source = workspace.results / "runs" / f"{run.name}.json"
    if not source.is_file():
        existing = sorted((workspace.results / "runs").glob("*.json"))
        assert existing, "expected a durable batch result to copy"
        source = existing[0]
    payload = read_json(source)
    payload.update(run_id=run.name, run_dir=str(run))
    target = workspace.results / "runs" / f"{run.name}.json"
    write_json(target, payload)
    return target


def cleanup_record(workspace: Workspace) -> dict[str, Any]:
    return read_json(workspace.results / "cleanup.json")


def actions(workspace: Workspace) -> dict[str, str]:
    return {row["run_id"]: row["action"] for row in cleanup_record(workspace)["runs"]}


def test_cleanup_keeps_paused_and_unfinished_workspaces(workspace: Workspace):
    paused = workspace.sync("--stop-after", "capture")
    assert paused.returncode == 0, paused.stderr
    original = workspace.only_run()
    book = original.parent
    older = clone_run(
        original,
        book / "run-20260101-000000",
        status="completed",
        ended_at="2026-01-01T00:00:10+08:00",
    )
    archive_clone(workspace, older)
    held = clone_run(original, book / "run-20260101-000001", status="paused")
    crashed = clone_run(
        original, book / "run-20260101-000002", status="running", pid=999999
    )

    resumed = workspace.sync("--resume", original.name)
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout.strip().splitlines()[-1])["status"] == "completed"

    # The earlier finished workspace is gone; paused/interrupted ones stay.
    assert not older.exists()
    assert held.is_dir()
    assert crashed.is_dir()
    assert original.is_dir()
    assert actions(workspace) == {
        older.name: "removed",
        held.name: "kept",
        crashed.name: "kept",
        original.name: "kept",
    }
    record = cleanup_record(workspace)
    assert record["schema_version"] == "unified-wash-cleanup-1.0"
    assert record["counts"]["removed"] == 1
    assert record["trigger_run"] == original.name
    # Durable history is never cleaned with wash.
    assert (workspace.results / "runs" / f"{older.name}.json").is_file()
    assert (workspace.results / "runs" / f"{original.name}.json").is_file()


def test_cleanup_defers_a_workspace_whose_result_is_not_archived(
    workspace: Workspace,
):
    assert workspace.sync("--stop-after", "capture").returncode == 0
    original = workspace.only_run()
    older = clone_run(
        original,
        original.parent / "run-20260101-000000",
        status="completed",
        ended_at="2026-01-01T00:00:10+08:00",
    )
    # No durable result for the constructed run: archiving must come first.

    resumed = workspace.sync("--resume", original.name)
    assert resumed.returncode == 0, resumed.stderr
    assert older.is_dir()
    row = next(
        item
        for item in cleanup_record(workspace)["runs"]
        if item["run_id"] == older.name
    )
    assert row["action"] == "kept"
    assert "not archived" in row["reason"]

    # The next finished batch retries once the result is durable.
    archive_clone(workspace, older)
    assert workspace.sync().returncode == 0
    assert not older.exists()
    assert actions(workspace)[older.name] == "removed"


def test_cleanup_failure_is_recorded_and_retried_without_failing_the_batch(
    workspace: Workspace,
):
    assert workspace.sync("--stop-after", "capture").returncode == 0
    original = workspace.only_run()
    older = clone_run(
        original,
        original.parent / "run-20260101-000000",
        status="completed",
        ended_at="2026-01-01T00:00:10+08:00",
    )
    archive_clone(workspace, older)
    locked = [older, *sorted(older.rglob("*"))]
    for path in locked:
        if path.is_dir():
            path.chmod(0o500)
    try:
        resumed = workspace.sync("--resume", original.name)
        assert resumed.returncode == 0, resumed.stderr
        summary = json.loads(resumed.stdout.strip().splitlines()[-1])
        assert summary["status"] == "completed"
        assert older.is_dir()
        record = cleanup_record(workspace)
        assert record["counts"]["failed"] == 1
        assert any(row["reason"] == "removal-failed" for row in record["failures"])
        assert actions(workspace)[older.name] == "failed"
        # A valid knowledge base is untouched by the failed cleanup.
        assert (workspace.corpus / ".sync-state.json").is_file()
        assert (workspace.results / "runs" / f"{original.name}.json").is_file()
    finally:
        for path in locked:
            if path.is_dir():
                path.chmod(0o700)

    assert workspace.sync().returncode == 0
    assert not older.exists()
    assert actions(workspace)[older.name] == "removed"


def legacy_run(workspace: Workspace, name: str = "sync-20260101-000000") -> Path:
    """A constructed work directory from the superseded pipeline implementation."""
    run = workspace.staging / "fund" / name
    (run / "raw").mkdir(parents=True)
    raw = run / "raw" / "1-20260101T000000+0800.md"
    raw.write_text("# Legacy original\n\nBody.\n", encoding="utf-8")
    write_json(
        run / "export-progress.json",
        {
            "configuration": {"source_url": "https://example.invalid/team/uat"},
            "items": [
                {
                    "id": 1,
                    "file": raw.name,
                    "status": "exported",
                    "raw": {"path": str(raw), "stage": "raw_export"},
                }
            ],
        },
    )
    write_json(
        run / "pipeline-state.json",
        {"version": 1, "stages": {"corpus": "retry_required"}, "status": "running"},
    )
    return run


def wash_cli(workspace: Workspace, *args: str):
    return workspace.cli(
        "wash", "--config", str(workspace.config), "--book", "fund", *args
    )


def point_state_at_legacy(workspace: Workspace, raw: Path) -> None:
    """The canonical download-reuse reference still points into historical wash."""
    path = workspace.corpus / ".sync-state.json"
    state = read_json(path)
    row = state["books"]["fund"]["documents"]["1"]
    row.setdefault("steps", {})["download"] = {
        "status": "completed",
        "version": {
            "field": "content_updated_at",
            "value": "2026-01-01T00:00:00+08:00",
            "utc": "2026-01-01T00:00:00+08:00",
        },
        "file": raw.name,
        "raw": {"path": str(raw), "stage": "raw_export"},
    }
    write_json(path, state)


def test_wash_inventory_reports_legacy_references_without_deleting(
    workspace: Workspace,
):
    finished_batch(workspace)
    legacy = legacy_run(workspace)
    raw = legacy / "raw" / "1-20260101T000000+0800.md"
    point_state_at_legacy(workspace, raw)
    before = {
        path: path.read_bytes() for path in sorted(legacy.rglob("*")) if path.is_file()
    }
    state_before = (workspace.corpus / ".sync-state.json").read_bytes()

    listed = wash_cli(workspace, "--json")
    assert listed.returncode == 0, listed.stderr
    view = json.loads(listed.stdout)
    assert view["schema_version"] == "unified-wash-inventory-1.0"
    row = next(item for item in view["runs"] if item["run_id"] == legacy.name)
    assert row["kind"] == "legacy"
    assert row["resume_verified"] is False
    assert "validation" in row["annotation"]
    assert str(raw) in [ref["path"] for ref in row["references"]]
    assert row["action"] == "migrate"

    # Inventory is read-only: no deletion, no migration, no state rewrite.
    assert {
        path: path.read_bytes() for path in sorted(legacy.rglob("*")) if path.is_file()
    } == before
    assert (workspace.corpus / ".sync-state.json").read_bytes() == state_before
    assert not (workspace.results / "wash-migration.json").exists()
    assert not (workspace.results / "migrated").exists()


def test_wash_migrate_persists_necessary_artifacts_and_rewrites_old_references(
    workspace: Workspace,
):
    finished_batch(workspace)
    legacy = legacy_run(workspace)
    raw = legacy / "raw" / "1-20260101T000000+0800.md"
    expected = raw.read_bytes()
    point_state_at_legacy(workspace, raw)

    migrated = wash_cli(workspace, "--migrate", "--json")
    assert migrated.returncode == 0, migrated.stderr
    manifest = json.loads(migrated.stdout)
    assert manifest["schema_version"] == "unified-wash-migration-1.0"

    # The necessary original is durable outside wash and byte-identical.
    durable = workspace.results / "migrated" / legacy.name / "raw" / raw.name
    assert durable.is_file()
    assert durable.read_bytes() == expected
    assert not durable.is_relative_to(workspace.staging)
    # The old canonical reference now resolves to the durable copy.
    state = read_json(workspace.corpus / ".sync-state.json")
    reference = state["books"]["fund"]["documents"]["1"]["steps"]["download"]["raw"]
    assert reference["path"] == str(durable)
    # Nothing was bulk-deleted: the historical directory keeps its evidence.
    assert raw.is_file()
    assert (legacy / "pipeline-state.json").is_file()
    annotation = read_json(legacy / "wash-migration.json")
    assert annotation["resume_verified"] is False
    assert "validation" in annotation["annotation"]
    assert (workspace.results / "wash-migration.json").is_file()

    # Repeating the migration is idempotent.
    again = wash_cli(workspace, "--migrate", "--json")
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["counts"]["migrated"] == 0
    assert durable.read_bytes() == expected
    assert read_json(workspace.corpus / ".sync-state.json")["books"]["fund"][
        "documents"
    ]["1"]["steps"]["download"]["raw"]["path"] == str(durable)


def test_wash_migrate_non_json_report_is_readable(workspace: Workspace):
    finished_batch(workspace)
    legacy = legacy_run(workspace)
    raw = legacy / "raw" / "1-20260101T000000+0800.md"
    point_state_at_legacy(workspace, raw)

    migrated = wash_cli(workspace, "--migrate")

    # The human-readable migration summary must not raise on migration rows,
    # which carry references_migrated/artifacts/migrated, not inventory keys.
    assert migrated.returncode == 0, migrated.stderr
    assert "book fund" in migrated.stdout
    assert legacy.name in migrated.stdout
    assert "references_migrated=" in migrated.stdout
    assert "action=" not in migrated.stdout


def make_capture_anomaly(workspace: Workspace) -> None:
    """doc 2 detail is newer than its listing: a capture anomaly with raw present."""
    remote = read_json(workspace.root / "remote.json")
    remote["details"]["2"]["content_updated_at"] = "2026-11-10T12:00:00+08:00"
    write_json(workspace.root / "remote.json", remote)


def only_anomaly(workspace: Workspace) -> dict[str, Any]:
    registry = read_json(workspace.results / "anomalies.json")
    records = registry["anomalies"]
    assert len(records) == 1, records
    return next(iter(records.values()))


def cache_entry(workspace: Workspace, key: str = "image-0123456789abcdef") -> Path:
    """A constructed long-term OCR cache entry, independent of wash."""
    entry = workspace.ocr_cache / key
    entry.mkdir(parents=True)
    (entry / "artifact.md").write_text("OCR TEXT durable\n", encoding="utf-8")
    write_json(
        entry / "evidence.json",
        {
            "contract": "prepared-jpeg-ocr-v1",
            "images": [
                {
                    "resource_id": "https://example.invalid/a.png",
                    "source_path": str(entry / "original.png"),
                    "prepared_image": str(entry / "prepared.jpg"),
                    "artifact": str(entry / "artifact.md"),
                    "status": "success",
                }
            ],
        },
    )
    return entry


def test_archived_anomaly_inputs_and_ocr_cache_survive_cleanup(
    workspace: Workspace,
):
    make_capture_anomaly(workspace)
    first = workspace.sync()
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout.strip().splitlines()[-1])["status"] == "partial"
    run = workspace.only_run()
    record = only_anomaly(workspace)
    snapshot = read_json(run / "capture" / "snapshot.json")
    raw = (run / "capture" / snapshot["exports"]["2"]["raw"]["path"]).resolve()
    expected = raw.read_bytes()
    entry = cache_entry(workspace)
    cache_before = {
        path: path.read_bytes() for path in sorted(entry.rglob("*")) if path.is_file()
    }

    # The next finished batch cleans the anomaly's own work directory. A newer
    # consistent remote version takes part in daily sync; the archived anomaly
    # stays handed off for its own version.
    remote = read_json(workspace.root / "remote.json")
    stamp = "2026-11-11T10:00:00+08:00"
    remote["documents"][1]["content_updated_at"] = stamp
    remote["details"]["2"] = {"id": 2, "body": "body 2 v2", "content_updated_at": stamp}
    write_json(workspace.root / "remote.json", remote)
    second = workspace.sync()
    assert second.returncode == 0, second.stderr
    assert not run.exists()
    assert actions(workspace)[run.name] == "removed"

    shown = workspace.cli(
        "anomalies",
        "--config",
        str(workspace.config),
        "--book",
        "fund",
        "--show",
        record["id"],
    )
    assert shown.returncode == 0, shown.stderr
    still = json.loads(shown.stdout)
    assert still["disposition"] == "pending"
    durable = Path(still["necessary_inputs"][0]["path"])
    assert durable.is_file()
    assert not durable.is_relative_to(workspace.staging)
    assert durable.read_bytes() == expected
    # The long-term OCR cache is never cleaned with wash.
    assert {
        path: path.read_bytes() for path in sorted(entry.rglob("*")) if path.is_file()
    } == cache_before


def test_cleanup_migrates_an_anomaly_input_that_only_lives_in_wash(
    workspace: Workspace,
):
    finished_batch(workspace)
    run = workspace.only_run()
    older = clone_run(
        run,
        run.parent / "run-20260101-000000",
        status="completed",
        ended_at="2026-01-01T00:00:10+08:00",
    )
    archive_clone(workspace, older)
    # A historical anomaly record whose only input copy is inside the old run.
    raw = older / "capture" / "staging" / "raw" / "1-20261110T100000+0800.md"
    assert raw.is_file()
    expected = raw.read_bytes()
    registry = read_json(workspace.results / "anomalies.json")
    registry["anomalies"]["fund-1-legacy-ocr-image_failed"] = {
        "id": "fund-1-legacy-ocr-image_failed",
        "book_key": "fund",
        "document_id": 1,
        "source_version": None,
        "version_stamp": "unknown",
        "stage": "ocr",
        "code": "image_failed",
        "reason": "image_failed",
        "necessary_inputs": [{"kind": "raw", "path": str(raw), "source": str(raw)}],
        "disposition": "pending",
        "runs": [older.name],
        "last_run": older.name,
        "attempts": 1,
    }
    write_json(workspace.results / "anomalies.json", registry)

    assert workspace.sync().returncode == 0

    assert not older.exists()
    record = only_anomaly(workspace)
    durable = Path(record["necessary_inputs"][0]["path"])
    assert durable.is_file()
    assert not durable.is_relative_to(workspace.staging)
    assert durable.read_bytes() == expected
    # Provenance is preserved; only the durable copy location is rewritten.
    assert record["necessary_inputs"][0]["source"] == str(raw)


DEPLOYMENT_DOC = Path(__file__).resolve().parents[1] / "docs" / "unattended-sync.md"
CRON_LINE = re.compile(
    r"^(?:\S+\s+){5}\S*dogpdkb\s+sync\b.*--config\s+\S+.*--book\s+\S+.*"
    r">>\s*\S+\s+2>&1\s*$"
)


def fenced_blocks(text: str) -> list[str]:
    blocks = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def test_deployment_doc_offers_copyable_crontab_and_logrotate_examples():
    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    assert text.startswith("# ")
    blocks = fenced_blocks(text)

    cron = [
        line
        for block in blocks
        for line in block.splitlines()
        if CRON_LINE.match(line.strip())
    ]
    assert cron, "expected a copyable crontab line with program, config and log"
    assert "--config" in cron[0] and "--book" in cron[0]
    # No credential is embedded in the scheduled command.
    assert "token=" not in cron[0].lower()
    assert "PADDLEOCR_TOKEN=" not in cron[0]

    rotation = [
        block
        for block in blocks
        if "{" in block
        and re.search(r"(?m)^\s*(daily|weekly|monthly)\s*$", block)
        and re.search(r"(?m)^\s*rotate\s+\d+\s*$", block)
    ]
    assert rotation, "expected a copyable system logrotate stanza"
    log = re.search(r">>\s*(\S+)\s+2>&1", cron[0]).group(1)
    assert log in rotation[0], "logrotate must rotate the log the cron line appends to"

    # The tool neither installs cron nor runs a rotation daemon.
    lowered = text.lower()
    assert "does not install" in lowered
    assert "logrotate" in lowered
    assert "PADDLEOCR_TOKEN" in text
    # Deployment also documents the durable stores and the historical tidy.
    assert "dogpdkb wash" in text
    assert "sync-results" in text or "[paths].results" in text
    assert "ocr-cache" in text or "[paths].ocr_cache" in text
