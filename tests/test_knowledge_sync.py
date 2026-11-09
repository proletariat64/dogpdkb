"""Black-box acceptance for the read-only knowledge planning branch."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

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
