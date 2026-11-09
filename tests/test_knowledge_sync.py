"""Black-box acceptance for the read-only knowledge planning branch."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def sync(*arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "dogpdkb", "sync", *arguments],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def snapshot(documents: list[dict[str, Any]], *, status: str = "initialized"):
    ids = [document["id"] for document in documents]
    return {
        "schema_version": "knowledge-capture-1.0",
        "status": status,
        "book": {
            "book_id": 123,
            "slug": "uat",
            "name": "UAT",
            "items_count": len(documents),
        },
        "observation": {
            "started_at": "2026-11-09T00:00:00Z",
            "ended_at": "2026-11-09T00:01:00Z",
            "timezone": "UTC",
        },
        "listing": {
            "complete": True,
            "document_count": len(documents),
            "document_ids": ids,
            "pages": [{"offset": 0, "limit": 50, "item_count": len(documents)}],
        },
        "baseline": None,
        "changes": {
            "new": [],
            "updated": [],
            "renamed": [],
            "moved": [],
            "absent": [],
        },
        "selected": [],
        "counts": {},
        "documents": {
            str(document["id"]): {
                "id": document["id"],
                "title": document["title"],
                "slug": str(document["id"]),
                "format": "markdown",
                "toc_path": [],
                "version": {
                    "field": "content_updated_at",
                    "value": document["version"],
                    "utc": document["version"],
                },
            }
            for document in documents
        },
        "exports": {},
        "pending": [],
        "anomalies": [],
    }


def write_raw_baseline(
    root: Path, documents: list[dict[str, Any]]
) -> tuple[Path, dict[str, Path]]:
    raw_paths = {}
    rows = []
    for document in documents:
        raw = root / "baseline-raw" / f"{document['id']}.md"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(document["body"])
        raw_paths[str(document["id"])] = raw
        rows.append(
            {
                "knowledge_base": "123",
                "document_id": str(document["id"]),
                "content_kind": "raw",
                "path": f"baseline-raw/{document['id']}.md",
                "metadata": {
                    "name": document["title"],
                    "logical_path": "Funds",
                    "source_ref": f"123:{document['id']}",
                    "sync_timestamp": document["version"],
                },
            }
        )
    baseline = root / "baseline.json"
    write_json(baseline, {"schema_version": "1.0", "documents": rows})
    return baseline, raw_paths


def captured_update(root: Path, documents: list[dict[str, Any]]):
    observed = snapshot(documents, status="captured")
    for document in documents:
        doc_id = document["id"]
        raw = root / "current-raw" / f"{doc_id}.md"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(document["body"])
        observed["changes"]["updated"].append(doc_id)
        observed["selected"].append(doc_id)
        observed["exports"][str(doc_id)] = {
            "status": "captured",
            "labels": ["updated"],
            "raw": {"path": f"current-raw/{doc_id}.md", "stage": "raw_export"},
            "listing_version": observed["documents"][str(doc_id)]["version"],
            "detail_version": observed["documents"][str(doc_id)]["version"],
            "original": {"status": "available"},
        }
    return observed


def test_initial_observation_without_graph_baseline_stays_uncovered(
    tmp_path: Path,
):
    observed = snapshot([{"id": 1, "title": "One", "version": "2026-11-09T00:00:00Z"}])
    snapshot_path = tmp_path / "snapshot.json"
    state_path = tmp_path / "private" / "state.json"
    staging = tmp_path / "private" / "planning"
    write_json(snapshot_path, observed)

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    state = json.loads(state_path.read_text())
    assert plan["schema_version"] == "knowledge-plan-2.0"
    assert plan["execution"]["automatic_extraction_allowed"] is False
    assert plan["counts"]["external_calls"] == 0
    assert plan["results"][0]["action"] == "HOLD_EXISTING"
    assert plan["results"][0]["reason_codes"] == ["GRAPH_BASELINE_NOT_REGISTERED"]
    record = state["documents"]["1"]
    assert record["observation"]["version"]["utc"] == "2026-11-09T00:00:00Z"
    assert record["corpus"]["status"] == "not_captured"
    assert record["graph"]["status"] == "uncovered"
    assert record["pending"] == ["GRAPH_BASELINE_NOT_REGISTERED"]
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert (staging / "plan.json").stat().st_mode & 0o777 == 0o600
    assert staging.stat().st_mode & 0o777 == 0o700
    assert not (tmp_path / "private" / "planning" / "graphify-out").exists()


def test_raw_graph_baseline_allows_verified_body_reuse_without_extraction(
    tmp_path: Path,
):
    old_raw = tmp_path / "baseline-raw" / "old.md"
    new_raw = tmp_path / "current-raw" / "new.md"
    old_raw.parent.mkdir(parents=True)
    new_raw.parent.mkdir(parents=True)
    old_raw.write_text("# Limits\n\nRefund requires approval.\n")
    new_raw.write_text("# Limits\n\nRefund requires approval.\n")
    baseline_path = tmp_path / "baseline.json"
    write_json(
        baseline_path,
        {
            "schema_version": "1.0",
            "documents": [
                {
                    "knowledge_base": "123",
                    "document_id": "1",
                    "content_kind": "raw",
                    "path": "baseline-raw/old.md",
                    "metadata": {
                        "name": "Limits",
                        "logical_path": "Funds",
                        "source_ref": "123:1",
                        "sync_timestamp": "2026-11-07T00:00:00Z",
                    },
                }
            ],
        },
    )
    observed = snapshot(
        [{"id": 1, "title": "Limits", "version": "2026-11-09T00:00:00Z"}],
        status="captured",
    )
    observed["changes"]["updated"] = [1]
    observed["selected"] = [1]
    observed["exports"]["1"] = {
        "status": "captured",
        "labels": ["updated"],
        "raw": {"path": "current-raw/new.md", "stage": "raw_export"},
        "listing_version": observed["documents"]["1"]["version"],
        "detail_version": observed["documents"]["1"]["version"],
        "original": {"status": "available"},
    }
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline_path),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    row = plan["results"][0]
    assert row["action"] == "KEEP_EXISTING"
    assert row["comparison"]["status"] == "REUSE"
    assert row["pending"] == []
    assert plan["counts"]["external_calls"] == 0
    state = json.loads(state_path.read_text())
    graph = state["documents"]["1"]["graph"]
    assert graph["status"] == "covered"
    assert graph["coverage"] == "body_reuse"
    assert graph["version"]["utc"] == "2026-11-09T00:00:00Z"
    assert state["documents"]["1"]["corpus"]["status"] == "captured"
    assert (
        old_raw.read_text()
        == new_raw.read_text()
        == ("# Limits\n\nRefund requires approval.\n")
    )


def test_body_candidate_freezes_full_raw_but_is_not_execution_permission(
    tmp_path: Path,
):
    old_raw = tmp_path / "baseline-raw" / "old.md"
    new_raw = tmp_path / "current-raw" / "new.md"
    old_raw.parent.mkdir(parents=True)
    new_raw.parent.mkdir(parents=True)
    old_raw.write_text("# Limits\n\nRefund requires approval.\n")
    new_raw.write_text("# Limits\n\nRefund requires approval.\n\nFee is 100 USD.\n")
    baseline_path = tmp_path / "baseline.json"
    write_json(
        baseline_path,
        {
            "schema_version": "1.0",
            "documents": [
                {
                    "knowledge_base": "123",
                    "document_id": "1",
                    "content_kind": "raw",
                    "path": "baseline-raw/old.md",
                    "metadata": {
                        "name": "Limits",
                        "logical_path": "Funds",
                        "source_ref": "123:1",
                        "sync_timestamp": "2026-11-07T00:00:00Z",
                    },
                }
            ],
        },
    )
    observed = snapshot(
        [{"id": 1, "title": "Limits", "version": "2026-11-09T00:00:00Z"}],
        status="captured",
    )
    observed["changes"]["updated"] = [1]
    observed["selected"] = [1]
    observed["exports"]["1"] = {
        "status": "captured",
        "labels": ["updated"],
        "raw": {"path": "current-raw/new.md", "stage": "raw_export"},
        "listing_version": observed["documents"]["1"]["version"],
        "detail_version": observed["documents"]["1"]["version"],
        "original": {"status": "available"},
    }
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline_path),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    row = plan["results"][0]
    assert row["action"] == "BODY_DELTA_CANDIDATE"
    assert row["approval"] == {"required": True, "status": "required"}
    assert row["execution"]["source_scope"] == "whole_document"
    assert row["execution"]["source_version"]["utc"] == "2026-11-09T00:00:00Z"
    frozen_raw = staging / row["execution"]["raw"]["path"]
    assert frozen_raw.read_bytes() == new_raw.read_bytes()
    assert frozen_raw.stat().st_mode & 0o777 == 0o600
    assert plan["execution"]["automatic_extraction_allowed"] is False
    assert plan["approval_contract"] == {
        "schema_version": "knowledge-approvals-2.0",
        "binding": ["book_id", "document_id", "source_version", "raw"],
        "source_scope": "whole_document",
        "review_dispositions_required": True,
        "quality_cases_required": True,
    }
    assert plan["counts"]["external_calls"] == 0
    state = json.loads(state_path.read_text())
    graph = state["documents"]["1"]["graph"]
    assert graph["version"]["utc"] == "2026-11-07T00:00:00Z"
    assert (
        state_path.parent / graph["raw"]["path"]
    ).read_bytes() == old_raw.read_bytes()
    assert state["documents"]["1"]["pending"] == ["APPROVAL_REQUIRED"]
    assert state["documents"]["1"]["corpus"]["version"]["utc"] == (
        "2026-11-09T00:00:00Z"
    )


def test_pending_candidate_survives_a_later_unchanged_observation(tmp_path: Path):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, old_raw = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": old_version,
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    updated = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": new_version,
                "body": "# Limits\n\nRefund requires approval.\n\nFee is 100 USD.\n",
            }
        ],
    )
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, updated)
    state_path = tmp_path / "state.json"
    first_staging = tmp_path / "first-plan"
    arguments = [
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--baseline",
        str(baseline),
    ]
    first = sync(*arguments, "--staging", str(first_staging))
    assert first.returncode == 0, first.stderr

    unchanged = snapshot(
        [{"id": 1, "title": "Limits", "version": new_version}],
        status="captured",
    )
    unchanged["documents"]["1"]["raw"] = {
        "path": "current-raw/1.md",
        "stage": "raw_export",
    }
    unchanged_path = tmp_path / "unchanged.json"
    write_json(unchanged_path, unchanged)
    second_staging = tmp_path / "second-plan"

    second = sync(
        "--knowledge",
        "--snapshot",
        str(unchanged_path),
        "--state",
        str(state_path),
        "--staging",
        str(second_staging),
    )

    assert second.returncode == 0, second.stderr
    plan = json.loads((second_staging / "plan.json").read_text())
    row = plan["results"][0]
    assert row["selected"] is False
    assert row["action"] == "BODY_DELTA_CANDIDATE"
    assert row["pending"] == ["APPROVAL_REQUIRED"]
    assert row["execution"]["source_version"]["utc"] == new_version
    assert plan["counts"]["compared"] == 1
    state = json.loads(state_path.read_text())
    assert state["documents"]["1"]["pending"] == ["APPROVAL_REQUIRED"]
    assert state["documents"]["1"]["graph"]["version"]["utc"] == old_version
    assert old_raw["1"].read_text() == "# Limits\n\nRefund requires approval.\n"


def test_incomplete_capture_keeps_normal_document_results_visible(tmp_path: Path):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": doc_id,
                "title": title,
                "version": old_version,
                "body": f"# {title}\n\nRefund requires approval.\n",
            }
            for doc_id, title in ((1, "One"), (2, "Two"))
        ],
    )
    observed = captured_update(
        tmp_path,
        [
            {
                "id": doc_id,
                "title": title,
                "version": new_version,
                "body": f"# {title}\n\nRefund requires approval.\n",
            }
            for doc_id, title in ((1, "One"), (2, "Two"))
        ],
    )
    observed["status"] = "incomplete"
    observed["anomalies"] = [{"id": 2, "code": "detail_newer_than_listing"}]
    observed["pending"] = [{"id": 2, "reasons": ["detail_newer_than_listing"]}]
    observed["exports"]["2"]["detail_version"] = {
        "field": "content_updated_at",
        "value": "2026-11-09T00:01:00Z",
        "utc": "2026-11-09T00:01:00Z",
    }
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    assert plan["snapshot_status"] == "incomplete"
    assert len(plan["results"]) == 2
    normal, anomalous = plan["results"]
    assert normal["action"] == "KEEP_EXISTING"
    assert normal["comparison"]["status"] == "REUSE"
    assert anomalous["action"] == "HOLD_EXISTING"
    assert anomalous["comparison"] is None
    assert anomalous["pending"] == ["detail_newer_than_listing"]
    state = json.loads(state_path.read_text())
    assert state["documents"]["1"]["graph"]["version"]["utc"] == new_version
    assert state["documents"]["2"]["graph"]["version"]["utc"] == old_version
    assert state["documents"]["2"]["corpus"]["status"] == "pending"
    assert plan["counts"]["pending"] == 1


def test_new_id_is_a_separate_candidate_not_existing_graph_coverage(tmp_path: Path):
    old_version = "2026-11-07T00:00:00Z"
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Existing",
                "version": old_version,
                "body": "# Existing\n\nRefund requires approval.\n",
            }
        ],
    )
    observed = snapshot(
        [
            {
                "id": 1,
                "title": "Existing",
                "version": old_version,
                "body": "# Existing\n\nRefund requires approval.\n",
            },
            {
                "id": 2,
                "title": "New",
                "version": "2026-11-09T00:00:00Z",
                "body": "# New\n\nFee is 100 USD.\n",
            },
        ],
        status="captured",
    )
    observed["changes"]["new"] = [2]
    observed["selected"] = [2]
    raw = tmp_path / "current-raw" / "2.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("# New\n\nFee is 100 USD.\n")
    observed["exports"]["2"] = {
        "status": "captured",
        "labels": ["new"],
        "raw": {"path": "current-raw/2.md", "stage": "raw_export"},
        "listing_version": observed["documents"]["2"]["version"],
        "detail_version": observed["documents"]["2"]["version"],
        "original": {"status": "not_applicable"},
    }
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    new_row = plan["results"][1]
    assert new_row["labels"] == ["new"]
    assert new_row["action"] == "BODY_DELTA_CANDIDATE"
    assert new_row["reason_codes"] == [
        "APPROVAL_REQUIRED",
        "NEW_DOCUMENT_NOT_IN_GRAPH",
    ]
    assert new_row["execution"]["previous_graph_version"] is None
    assert plan["counts"]["new_documents"] == 1
    state = json.loads(state_path.read_text())
    assert state["documents"]["2"]["graph"]["status"] == "uncovered"
    assert state["documents"]["2"]["graph"]["version"] is None
    assert state["documents"]["1"]["graph"]["status"] == "covered"

    unchanged = snapshot(
        [
            {"id": 1, "title": "Existing", "version": old_version},
            {
                "id": 2,
                "title": "New",
                "version": "2026-11-09T00:00:00Z",
            },
        ],
        status="captured",
    )
    unchanged["documents"]["2"]["raw"] = {
        "path": "current-raw/2.md",
        "stage": "raw_export",
    }
    unchanged_path = tmp_path / "unchanged.json"
    write_json(unchanged_path, unchanged)
    second_staging = tmp_path / "second-plan"
    repeated = sync(
        "--knowledge",
        "--snapshot",
        str(unchanged_path),
        "--state",
        str(state_path),
        "--staging",
        str(second_staging),
    )
    assert repeated.returncode == 0, repeated.stderr
    repeated_row = json.loads((second_staging / "plan.json").read_text())["results"][1]
    assert repeated_row["action"] == "BODY_DELTA_CANDIDATE"
    assert repeated_row["execution"]["source_scope"] == "whole_document"
    assert repeated_row["pending"] == [
        "APPROVAL_REQUIRED",
        "NEW_DOCUMENT_NOT_IN_GRAPH",
    ]


def test_candidate_with_revision_exposes_mandatory_review_issues(tmp_path: Path):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": old_version,
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    observed = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": new_version,
                "body": "# Limits\n\nRefund requires manager approval.\n",
            }
        ],
    )
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    row = plan["results"][0]
    assert row["action"] == "BODY_DELTA_CANDIDATE"
    assert row["comparison"]["status"] == "REVIEW"
    assert row["execution"]["review_issues"]
    assert "REVISION_REVIEW_REQUIRED" in {
        issue["code"] for issue in row["execution"]["review_issues"]
    }


def test_missing_raw_file_is_not_a_corpus_success(tmp_path: Path):
    old_version = "2026-11-07T00:00:00Z"
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": old_version,
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    observed = snapshot(
        [{"id": 1, "title": "Limits", "version": "2026-11-09T00:00:00Z"}],
        status="captured",
    )
    observed["changes"]["updated"] = [1]
    observed["selected"] = [1]
    observed["exports"]["1"] = {
        "status": "captured",
        "labels": ["updated"],
        "raw": {"path": "missing.md", "stage": "raw_export"},
        "listing_version": observed["documents"]["1"]["version"],
        "detail_version": observed["documents"]["1"]["version"],
        "original": {"status": "available"},
    }
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    row = plan["results"][0]
    assert row["action"] == "HOLD_EXISTING"
    assert row["comparison"] is None
    assert row["pending"] == ["CAPTURE_RAW_UNAVAILABLE"]
    state = json.loads(state_path.read_text())
    assert state["documents"]["1"]["corpus"]["status"] == "pending"
    assert state["documents"]["1"]["corpus"]["version"] is None
    assert state["documents"]["1"]["graph"]["version"]["utc"] == old_version


def test_historical_enriched_manifest_cannot_register_a_raw_graph_baseline(
    tmp_path: Path,
):
    enriched = tmp_path / "enriched.md"
    enriched.write_text("historical body")
    baseline = tmp_path / "baseline.json"
    write_json(
        baseline,
        {
            "schema_version": "1.0",
            "documents": [
                {
                    "knowledge_base": "123",
                    "document_id": "1",
                    "content_kind": "historical_enriched",
                    "path": "enriched.md",
                    "metadata": {
                        "name": "One",
                        "logical_path": "Funds",
                        "source_ref": "123:1",
                    },
                }
            ],
        },
    )
    observed = snapshot([{"id": 1, "title": "One", "version": "2026-11-09T00:00:00Z"}])
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"
    before = enriched.read_bytes()

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )

    assert result.returncode != 0
    assert "explicit raw snapshots" in result.stderr
    assert enriched.read_bytes() == before
    assert not state_path.exists()
    assert not (staging / "plan.json").exists()


def test_noncanonical_snapshot_identity_is_rejected(tmp_path: Path):
    observed = snapshot([{"id": 1, "title": "One", "version": "2026-11-09T00:00:00Z"}])
    observed["documents"]["1.0"] = observed["documents"].pop("1")
    observed["listing"]["document_ids"] = ["1.0"]
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
    )

    assert result.returncode != 0
    assert not state_path.exists()
    assert not (staging / "plan.json").exists()


def test_incomplete_listing_cannot_create_a_zero_change_plan(tmp_path: Path):
    observed = snapshot(
        [{"id": 1, "title": "One", "version": "2026-11-09T00:00:00Z"}],
        status="incomplete",
    )
    observed["listing"]["complete"] = False
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
    )

    assert result.returncode != 0
    assert "complete listing" in result.stderr
    assert not state_path.exists()
    assert not (staging / "plan.json").exists()


def test_initial_observation_does_not_equate_a_newer_version_with_old_graph(
    tmp_path: Path,
):
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-07T00:00:00Z",
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    observed = snapshot(
        [{"id": 1, "title": "Limits", "version": "2026-11-09T00:00:00Z"}]
    )
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    row = plan["results"][0]
    assert row["action"] == "HOLD_EXISTING"
    assert row["comparison"] is None
    assert row["pending"] == ["CAPTURE_RAW_UNAVAILABLE"]
    state = json.loads(state_path.read_text())
    assert state["documents"]["1"]["corpus"]["status"] == "not_captured"
    assert state["documents"]["1"]["graph"]["version"]["utc"] == (
        "2026-11-07T00:00:00Z"
    )


def test_existing_plan_artifact_is_not_overwritten(tmp_path: Path):
    observed = snapshot([{"id": 1, "title": "One", "version": "2026-11-09T00:00:00Z"}])
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"
    arguments = [
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
    ]
    first = sync(*arguments)
    assert first.returncode == 0, first.stderr
    plan_path = staging / "plan.json"
    plan_path.write_text("retained review artifact\n")
    state_before = state_path.read_bytes()

    second = sync(*arguments)

    assert second.returncode != 0
    assert "plan output already exists" in second.stderr
    assert plan_path.read_text() == "retained review artifact\n"
    assert state_path.read_bytes() == state_before


def test_metadata_work_stays_separate_from_verified_body_reuse(tmp_path: Path):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": old_version,
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    observed = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": new_version,
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    observed["changes"] = {
        "new": [],
        "updated": [],
        "renamed": [],
        "moved": [1],
        "absent": [],
    }
    observed["exports"]["1"]["labels"] = ["moved"]
    observed["documents"]["1"]["toc_path"] = ["New parent"]
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, observed)
    state_path = tmp_path / "state.json"
    staging = tmp_path / "planning"

    result = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads((staging / "plan.json").read_text())
    row = plan["results"][0]
    assert row["action"] == "KEEP_EXISTING"
    assert row["comparison"]["status"] == "REUSE"
    assert row["metadata_tasks"] == ["moved"]
    assert row["pending"] == ["METADATA_MAINTENANCE_REQUIRED"]
    assert plan["counts"]["external_calls"] == 0
    state = json.loads(state_path.read_text())
    assert state["documents"]["1"]["corpus"]["version"]["utc"] == new_version
    assert state["documents"]["1"]["graph"]["version"]["utc"] == old_version


def test_successful_capture_retry_recomputes_transient_failure(tmp_path: Path):
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-07T00:00:00Z",
                "body": "# Limits\n\nRefund requires approval.\n",
            }
        ],
    )
    good = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Limits",
                "version": "2026-11-09T00:00:00Z",
                "body": ("# Limits\n\nRefund requires approval.\n\nFee is 100 USD.\n"),
            }
        ],
    )
    failed = json.loads(json.dumps(good))
    failed["status"] = "incomplete"
    failed["anomalies"] = [{"id": 1, "code": "export_failed"}]
    failed["exports"]["1"] = {"status": "failed"}
    failed_path = tmp_path / "failed.json"
    write_json(failed_path, failed)
    state_path = tmp_path / "state.json"
    first = sync(
        "--knowledge",
        "--snapshot",
        str(failed_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "failed-plan"),
        "--baseline",
        str(baseline),
    )
    assert first.returncode == 0, first.stderr

    good["changes"]["updated"] = []
    good["selected"] = []
    good_path = tmp_path / "good.json"
    write_json(good_path, good)
    second = sync(
        "--knowledge",
        "--snapshot",
        str(good_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "good-plan"),
    )

    assert second.returncode == 0, second.stderr
    row = json.loads((tmp_path / "good-plan" / "plan.json").read_text())["results"][0]
    assert row["action"] == "BODY_DELTA_CANDIDATE"
    assert row["pending"] == ["APPROVAL_REQUIRED"]
    assert "export_failed" not in row["pending"]
    assert "CAPTURE_EXPORT_FAILED" not in row["pending"]


def test_unregistered_graph_source_becomes_a_review_candidate_when_raw_arrives(
    tmp_path: Path,
):
    version = "2026-11-09T00:00:00Z"
    baseline, _ = write_raw_baseline(
        tmp_path,
        [{"id": 1, "title": "Existing", "version": version, "body": "# Existing\n"}],
    )
    observed = snapshot(
        [
            {"id": 1, "title": "Existing", "version": version},
            {"id": 2, "title": "New", "version": version},
        ]
    )
    snapshot_path = tmp_path / "snapshot.json"
    state_path = tmp_path / "state.json"
    write_json(snapshot_path, observed)
    initial = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "first-plan"),
        "--baseline",
        str(baseline),
    )
    assert initial.returncode == 0, initial.stderr
    first = json.loads((tmp_path / "first-plan" / "plan.json").read_text())
    assert first["results"][1]["pending"] == ["GRAPH_SOURCE_NOT_REGISTERED"]
    assert first["results"][1]["action"] == "HOLD_EXISTING"
    unchanged = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "unchanged-plan"),
    )
    assert unchanged.returncode == 0, unchanged.stderr
    unchanged_row = json.loads((tmp_path / "unchanged-plan" / "plan.json").read_text())[
        "results"
    ][1]
    assert unchanged_row["action"] == "HOLD_EXISTING"
    assert unchanged_row["pending"] == ["GRAPH_SOURCE_NOT_REGISTERED"]

    raw = tmp_path / "current.md"
    raw.write_text("# New\n\nFee is 100 USD.\n")
    observed["documents"]["2"]["raw"] = {"path": "current.md", "stage": "raw_export"}
    write_json(snapshot_path, observed)
    retry = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "second-plan"),
    )

    assert retry.returncode == 0, retry.stderr
    second = json.loads((tmp_path / "second-plan" / "plan.json").read_text())
    row = second["results"][1]
    assert row["action"] == "BODY_DELTA_CANDIDATE"
    assert row["pending"] == ["APPROVAL_REQUIRED", "NEW_DOCUMENT_NOT_IN_GRAPH"]
    assert row["approval"]["required"] is True
    assert row["execution"]["previous_raw"] is None
    assert row["graph"]["status"] == "uncovered"
    assert second["counts"]["approved"] == 0
    state = json.loads(state_path.read_text())
    assert state["documents"]["2"]["pending"] == row["pending"]

    state["documents"]["2"]["pending"].append("OPERATOR_REVIEW_REQUIRED")
    write_json(state_path, state)
    manual = sync(
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "manual-plan"),
    )
    assert manual.returncode == 0, manual.stderr
    manual_row = json.loads((tmp_path / "manual-plan" / "plan.json").read_text())[
        "results"
    ][1]
    assert manual_row["action"] == "HOLD_EXISTING"
    assert "OPERATOR_REVIEW_REQUIRED" in manual_row["pending"]
    assert manual_row["graph"]["status"] == "uncovered"
    assert manual_row["execution"] is None
    assert manual_row["approval"]["required"] is False


@pytest.mark.parametrize(
    "failure", ["missing", "unreadable", "unreadable_parent", "over_budget"]
)
def test_unavailable_registered_graph_raw_holds_only_its_document_and_can_retry(
    tmp_path: Path,
    failure: str,
):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, old_raw = write_raw_baseline(
        tmp_path,
        [
            {
                "id": doc_id,
                "title": title,
                "version": old_version,
                "body": f"# {title}\n\nRefund requires approval.\n",
            }
            for doc_id, title in ((1, "One"), (2, "Two"))
        ],
    )
    if failure == "unreadable_parent":
        private = tmp_path / "private-graph"
        private.mkdir()
        old_raw["1"] = old_raw["1"].rename(private / "1.md")
        manifest = json.loads(baseline.read_text())
        manifest["documents"][0]["path"] = "private-graph/1.md"
        write_json(baseline, manifest)
    initial_snapshot = tmp_path / "initial.json"
    write_json(
        initial_snapshot,
        snapshot(
            [
                {"id": doc_id, "title": title, "version": old_version}
                for doc_id, title in ((1, "One"), (2, "Two"))
            ]
        ),
    )
    state_path = tmp_path / "state.json"
    initial = sync(
        "--knowledge",
        "--snapshot",
        str(initial_snapshot),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "initial-plan"),
        "--baseline",
        str(baseline),
    )
    assert initial.returncode == 0, initial.stderr
    previous_graph = json.loads(state_path.read_text())["documents"]["1"]["graph"]
    old_body = old_raw["1"].read_bytes()
    if failure == "missing":
        old_raw["1"].unlink()
    elif failure == "unreadable":
        old_raw["1"].chmod(0)
        if os.access(old_raw["1"], os.R_OK):
            pytest.skip("process can read mode-000 files")
    elif failure == "unreadable_parent":
        old_raw["1"].parent.chmod(0)
        if os.access(old_raw["1"], os.R_OK):
            pytest.skip("process can traverse mode-000 directories")
    else:
        with old_raw["1"].open("wb") as raw:
            raw.truncate(16 * 1024 * 1024 + 1)

    current = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "One",
                "version": new_version,
                "body": "# One\n\nRefund requires approval.\n\nFee is 100 USD.\n",
            },
            {
                "id": 2,
                "title": "Two",
                "version": new_version,
                "body": "# Two\n\nRefund requires approval.\n",
            },
        ],
    )
    current_path = tmp_path / "current.json"
    write_json(current_path, current)
    failed = sync(
        "--knowledge",
        "--snapshot",
        str(current_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "failed-plan"),
    )

    assert failed.returncode == 0, failed.stderr
    plan = json.loads((tmp_path / "failed-plan" / "plan.json").read_text())
    broken, healthy = plan["results"]
    assert broken["action"] == "HOLD_EXISTING"
    assert broken["comparison"] is None
    assert broken["pending"] == ["GRAPH_RAW_UNAVAILABLE"]
    assert broken["graph"] == previous_graph
    assert broken["execution"] is None
    assert broken["approval"]["required"] is False
    assert healthy["action"] == "KEEP_EXISTING"
    assert healthy["comparison"]["status"] == "REUSE"
    assert healthy["graph"]["version"]["utc"] == new_version
    assert plan["counts"]["compared"] == 1
    assert (
        json.loads(state_path.read_text())["documents"]["1"]["graph"] == previous_graph
    )

    old_raw["1"].parent.chmod(0o700)
    if old_raw["1"].exists():
        old_raw["1"].chmod(0o600)
    old_raw["1"].write_bytes(old_body)
    retry_plan = tmp_path / "retry-plan"
    retry = sync(
        "--knowledge",
        "--snapshot",
        str(current_path),
        "--state",
        str(state_path),
        "--staging",
        str(retry_plan),
    )
    assert retry.returncode == 0, retry.stderr
    row = json.loads((retry_plan / "plan.json").read_text())["results"][0]
    assert row["action"] == "BODY_DELTA_CANDIDATE"
    assert row["pending"] == ["APPROVAL_REQUIRED"]
    assert row["approval"]["required"] is True
    assert (
        retry_plan / row["execution"]["previous_raw"]["path"]
    ).read_bytes() == old_body
    assert (retry_plan / row["execution"]["raw"]["path"]).read_bytes() == (
        tmp_path / "current-raw" / "1.md"
    ).read_bytes()
    assert row["graph"] == previous_graph


def test_explicit_baselines_recompute_pending_for_each_registered_source(
    tmp_path: Path,
):
    version = "2026-11-09T00:00:00Z"
    documents = [
        {"id": doc_id, "title": title, "version": version}
        for doc_id, title in ((1, "One"), (2, "Two"))
    ]
    snapshot_path = tmp_path / "snapshot.json"
    state_path = tmp_path / "state.json"
    write_json(snapshot_path, snapshot(documents))
    arguments = [
        "--knowledge",
        "--snapshot",
        str(snapshot_path),
        "--state",
        str(state_path),
    ]
    initial = sync(*arguments, "--staging", str(tmp_path / "initial-plan"))
    assert initial.returncode == 0, initial.stderr
    rows = json.loads((tmp_path / "initial-plan" / "plan.json").read_text())["results"]
    assert [row["pending"] for row in rows] == [
        ["GRAPH_BASELINE_NOT_REGISTERED"],
        ["GRAPH_BASELINE_NOT_REGISTERED"],
    ]

    baseline, _ = write_raw_baseline(
        tmp_path, [{**documents[0], "body": "# One\n\nA rule.\n"}]
    )
    first = sync(
        *arguments,
        "--staging",
        str(tmp_path / "one-plan"),
        "--baseline",
        str(baseline),
    )
    assert first.returncode == 0, first.stderr
    rows = json.loads((tmp_path / "one-plan" / "plan.json").read_text())["results"]
    assert rows[0]["action"] == "KEEP_EXISTING"
    assert rows[0]["pending"] == []
    assert rows[1]["action"] == "HOLD_EXISTING"
    assert rows[1]["pending"] == ["GRAPH_SOURCE_NOT_REGISTERED"]

    baseline, _ = write_raw_baseline(
        tmp_path, [{**documents[1], "body": "# Two\n\nAnother rule.\n"}]
    )
    second = sync(
        *arguments,
        "--staging",
        str(tmp_path / "two-plan"),
        "--baseline",
        str(baseline),
    )
    assert second.returncode == 0, second.stderr
    plan = json.loads((tmp_path / "two-plan" / "plan.json").read_text())
    row = plan["results"][1]
    assert row["action"] == "KEEP_EXISTING"
    assert row["pending"] == []
    assert row["graph"]["coverage"] == "registered_raw_baseline"
    assert row["approval"]["required"] is False
    assert plan["counts"]["approved"] == 0


@pytest.mark.parametrize("failure", ["unreadable", "unreadable_parent", "over_budget"])
def test_unreadable_captured_raw_holds_only_its_document_and_can_retry(
    tmp_path: Path,
    failure: str,
):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, old_raw = write_raw_baseline(
        tmp_path,
        [
            {
                "id": doc_id,
                "title": title,
                "version": old_version,
                "body": f"# {title}\n\nRefund requires approval.\n",
            }
            for doc_id, title in ((1, "One"), (2, "Two"))
        ],
    )
    current = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "One",
                "version": new_version,
                "body": "# One\n\nRefund requires approval.\n\nFee is 100 USD.\n",
            },
            {
                "id": 2,
                "title": "Two",
                "version": new_version,
                "body": "# Two\n\nRefund requires approval.\n",
            },
        ],
    )
    raw = tmp_path / "current-raw" / "1.md"
    new_body = raw.read_bytes()
    if failure == "unreadable":
        raw.chmod(0)
        if os.access(raw, os.R_OK):
            pytest.skip("process can read mode-000 files")
    elif failure == "unreadable_parent":
        private = tmp_path / "private-current"
        private.mkdir()
        raw = raw.rename(private / "1.md")
        current["exports"]["1"]["raw"]["path"] = "private-current/1.md"
        private.chmod(0)
        if os.access(raw, os.R_OK):
            pytest.skip("process can traverse mode-000 directories")
    else:
        with raw.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
    current_path = tmp_path / "current.json"
    state_path = tmp_path / "state.json"
    write_json(current_path, current)
    failed = sync(
        "--knowledge",
        "--snapshot",
        str(current_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "failed-plan"),
        "--baseline",
        str(baseline),
    )

    assert failed.returncode == 0, failed.stderr
    plan = json.loads((tmp_path / "failed-plan" / "plan.json").read_text())
    broken, healthy = plan["results"]
    assert broken["action"] == "HOLD_EXISTING"
    assert broken["comparison"] is None
    assert broken["pending"] == ["CAPTURE_RAW_UNAVAILABLE"]
    assert broken["corpus"]["status"] == "pending"
    assert broken["corpus"]["version"] is None
    assert broken["graph"]["version"]["utc"] == old_version
    assert broken["execution"] is None
    assert healthy["action"] == "KEEP_EXISTING"
    assert healthy["comparison"]["status"] == "REUSE"
    assert healthy["graph"]["version"]["utc"] == new_version
    assert plan["counts"]["compared"] == 1

    raw.parent.chmod(0o700)
    raw.chmod(0o600)
    raw.write_bytes(new_body)
    retry_plan = tmp_path / "retry-plan"
    retry = sync(
        "--knowledge",
        "--snapshot",
        str(current_path),
        "--state",
        str(state_path),
        "--staging",
        str(retry_plan),
    )
    assert retry.returncode == 0, retry.stderr
    row = json.loads((retry_plan / "plan.json").read_text())["results"][0]
    assert row["action"] == "BODY_DELTA_CANDIDATE"
    assert row["pending"] == ["APPROVAL_REQUIRED"]
    assert row["approval"]["required"] is True
    assert (retry_plan / row["execution"]["previous_raw"]["path"]).read_bytes() == (
        old_raw["1"].read_bytes()
    )
    assert (retry_plan / row["execution"]["raw"]["path"]).read_bytes() == new_body
    assert row["graph"]["version"]["utc"] == old_version


@pytest.mark.parametrize(
    "fault", ["old_source_collision", "frozen_target_mismatch", "output_directory"]
)
def test_planning_output_failures_remain_fatal(tmp_path: Path, fault: str):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, raw = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "One",
                "version": old_version,
                "body": "# One\n\nRefund requires approval.\n",
            }
        ],
    )
    staging = tmp_path / "blocked-plan"
    colliding_source = staging / "inputs" / "123-1-previous.md"
    target = staging / "inputs" / "123-1.md"
    if fault == "old_source_collision":
        colliding_source.parent.mkdir(parents=True)
        colliding_source.write_bytes(raw["1"].read_bytes())
        manifest = json.loads(baseline.read_text())
        manifest["documents"][0]["path"] = str(colliding_source)
        write_json(baseline, manifest)
    elif fault == "frozen_target_mismatch":
        target.parent.mkdir(parents=True)
        target.write_text("Existing frozen bytes must not be overwritten.\n")
    else:
        staging.mkdir()
        (staging / "inputs").write_text("This file blocks the output directory.\n")

    initial_path = tmp_path / "initial.json"
    state_path = tmp_path / "state.json"
    write_json(
        initial_path, snapshot([{"id": 1, "title": "One", "version": old_version}])
    )
    initial = sync(
        "--knowledge",
        "--snapshot",
        str(initial_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "initial-plan"),
        "--baseline",
        str(baseline),
    )
    assert initial.returncode == 0, initial.stderr
    state_before = state_path.read_bytes()
    current = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "One",
                "version": new_version,
                "body": "# One\n\nNew rule.\n",
            }
        ],
    )
    current_path = tmp_path / "current.json"
    write_json(current_path, current)
    result = sync(
        "--knowledge",
        "--snapshot",
        str(current_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
    )

    assert result.returncode != 0
    assert not (staging / "plan.json").exists()
    assert state_path.read_bytes() == state_before
    if fault == "old_source_collision":
        assert "REPORT_INPUT_COLLISION" in result.stderr
        assert colliding_source.read_bytes() == raw["1"].read_bytes()
    elif fault == "frozen_target_mismatch":
        assert "frozen planning input already exists" in result.stderr
        assert target.read_text() == "Existing frozen bytes must not be overwritten.\n"


@pytest.mark.parametrize("field", ["raw", "metadata"])
def test_invalid_registered_graph_schema_is_not_a_new_document(
    tmp_path: Path, field: str
):
    old_version = "2026-11-07T00:00:00Z"
    new_version = "2026-11-09T00:00:00Z"
    baseline, _ = write_raw_baseline(
        tmp_path,
        [
            {
                "id": 1,
                "title": "One",
                "version": old_version,
                "body": "# One\n\nA previous rule.\n",
            }
        ],
    )
    initial_path = tmp_path / "initial.json"
    state_path = tmp_path / "state.json"
    write_json(
        initial_path, snapshot([{"id": 1, "title": "One", "version": old_version}])
    )
    initial = sync(
        "--knowledge",
        "--snapshot",
        str(initial_path),
        "--state",
        str(state_path),
        "--staging",
        str(tmp_path / "initial-plan"),
        "--baseline",
        str(baseline),
    )
    assert initial.returncode == 0, initial.stderr
    state = json.loads(state_path.read_text())
    state["documents"]["1"]["graph"][field] = None
    write_json(state_path, state)
    state_before = state_path.read_bytes()
    current = captured_update(
        tmp_path,
        [
            {
                "id": 1,
                "title": "One",
                "version": new_version,
                "body": "# One\n\nNew rule.\n",
            }
        ],
    )
    current_path = tmp_path / "current.json"
    write_json(current_path, current)
    staging = tmp_path / "invalid-plan"
    result = sync(
        "--knowledge",
        "--snapshot",
        str(current_path),
        "--state",
        str(state_path),
        "--staging",
        str(staging),
    )

    assert result.returncode != 0
    assert "registered graph source or metadata is invalid" in result.stderr
    assert state_path.read_bytes() == state_before
    assert not (staging / "plan.json").exists()
