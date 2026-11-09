"""Body-knowledge CLI acceptance, separate from strict processing advice."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def document(path, document_id="1", **updates):
    value = {
        "knowledge_base": "book",
        "document_id": document_id,
        "content_kind": "raw",
        "path": str(path),
        "metadata": {"name": "Limits", "logical_path": "Funds", "source_ref": "book:1"},
        "resources": [],
    }
    value.update(updates)
    return value


def invoke(tmp_path, baseline, incoming, *extra):
    old = tmp_path / "baseline.json"
    new = tmp_path / "incoming.json"
    out = tmp_path / "knowledge.json"
    for path, documents in ((old, baseline), (new, incoming)):
        path.write_text(json.dumps({"schema_version": "1.0", "documents": documents}))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "knowledge",
            "--baseline",
            str(old),
            "--incoming",
            str(new),
            "--output",
            str(out),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src")),
    )
    return completed, json.loads(out.read_text()) if out.is_file() else None


def test_partial_knowledge_reuse_survives_new_fact(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("# Limits\n\n- 单笔限额为10万元。\n")
    new.write_text("# Limits\n\n- 单笔限额为100000元。\n- 退款需要审批。\n")
    original = old.read_bytes(), new.read_bytes()
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 0, result.stderr
    assert report["schema_version"] == "knowledge-1.0"
    assert report["scope"] == "body_only"
    row = report["results"][0]
    assert row["status"] == "DELTA"
    assert row["counts"]["reuse"] >= 1
    assert row["counts"]["add"] >= 1
    assert original == (old.read_bytes(), new.read_bytes())
    assert (tmp_path / "knowledge.json").stat().st_mode & 0o777 == 0o600
    assert result.stdout == ""


def test_images_and_ocr_do_not_generate_tasks_or_gate_historical_body(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(
        "正文规则：退款需要审批。\n\n![old](missing-old.png)\n"
        "<!-- mdocr:ocr-begin -->\n随机旧识别结果\n<!-- mdocr:ocr-end -->\n"
    )
    new.write_text(
        "正文规则：退款需要审批。\n\n![new](missing-new.png)\n"
        "<!-- mdocr:ocr-begin -->\n完全不同的识别结果\n<!-- mdocr:ocr-end -->\n"
    )
    media = {
        "resources": [{"resource_id": "image", "kind": "image", "path": "missing.jpg"}],
        "ocr": {
            "images": [
                {"prepared_image": "missing-prepared.jpg", "artifact": "missing-ocr.md"}
            ]
        },
        "provenance": {"raw_path": "missing-raw.md"},
    }
    result, report = invoke(
        tmp_path,
        [document(old, content_kind="historical_enriched", **media)],
        [document(new, **media)],
    )
    assert result.returncode == 0, result.stderr
    row = report["results"][0]
    assert row["status"] == "REUSE"
    assert not report["errors"]
    assert not {"resources", "ocr", "media_pending"} & row.keys()
    serialized = json.dumps(report, ensure_ascii=False)
    assert "HISTORICAL_RAW_UNAVAILABLE" not in serialized
    assert "随机旧识别结果" not in serialized
    assert "完全不同的识别结果" not in serialized


def test_no_evidence_by_default_and_private_optional_report(tmp_path):
    raw = tmp_path / "body.md"
    secret = "private-body-marker-613"
    raw.write_text(secret)
    human = tmp_path / "review.md"
    result, report = invoke(
        tmp_path, [document(raw)], [document(raw)], "--report", str(human)
    )
    assert result.returncode == 0, result.stderr
    assert secret not in json.dumps(report) + human.read_text()
    assert human.stat().st_mode & 0o777 == 0o600
    assert result.stdout == ""
    result, report = invoke(
        tmp_path, [document(raw)], [document(raw)], "--include-evidence"
    )
    assert result.returncode == 0, result.stderr
    assert secret in json.dumps(report)


def test_empty_body_is_no_body_without_media_review(tmp_path):
    raw = tmp_path / "images.md"
    raw.write_text("![anything](unavailable.png)\n")
    result, report = invoke(tmp_path, [], [document(raw)])
    assert result.returncode == 0, result.stderr
    assert report["results"][0]["status"] == "NO_BODY"
    assert not report["errors"]


def test_old_only_knowledge_requires_review_without_deleting_source(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("- 付款需要审批。\n- 退款需要复核。\n")
    new.write_text("- 付款需要审批。\n")
    original = old.read_bytes()
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    assert report["results"][0]["status"] == "REVIEW"
    assert report["results"][0]["counts"]["old_only_review"] >= 1
    assert old.read_bytes() == original


def test_item_failure_is_unknown_and_does_not_leak_path(tmp_path):
    result, report = invoke(
        tmp_path, [], [document(tmp_path / "private-path-missing.md")]
    )
    assert result.returncode == 2
    assert report["results"][0]["status"] == "UNKNOWN"
    assert report["errors"][0]["reason_code"] == "INPUT_READ_FAILED"
    assert "private-path" not in result.stdout + result.stderr


def test_duplicate_identity_preserves_prior_report(tmp_path):
    raw = tmp_path / "body.md"
    raw.write_text("规则正文")
    output = tmp_path / "knowledge.json"
    output.write_text('{"previous": true}')
    result, report = invoke(tmp_path, [], [document(raw), document(raw)])
    assert result.returncode == 1
    assert "DUPLICATE_DOCUMENT_ID" in result.stderr
    assert report == {"previous": True}


@pytest.mark.parametrize("target_name", ["body.md", "baseline.json", "incoming.json"])
def test_report_cannot_replace_input(tmp_path, target_name):
    raw = tmp_path / "body.md"
    raw.write_text("保留正文")
    result, _ = invoke(
        tmp_path,
        [document(raw)],
        [document(raw)],
        "--output",
        str(tmp_path / target_name),
    )
    assert result.returncode == 1
    assert "REPORT_INPUT_COLLISION" in result.stderr
    assert raw.read_text() == "保留正文"


def test_empty_manifests_are_complete_and_counted(tmp_path):
    result, report = invoke(tmp_path, [], [])
    assert result.returncode == 0, result.stderr
    assert report["summary"]["total"] == 0
    assert report["results"] == []
    assert sum(report["summary"]["statuses"].values()) == 0


def test_global_publication_failure_is_private(tmp_path):
    result, report = invoke(
        tmp_path, [], [], "--output", str(tmp_path / "absent" / "knowledge.json")
    )
    assert result.returncode == 1
    assert report is None
    assert result.stderr.strip() == "mdcompare: REPORT_WRITE_FAILED"


@pytest.mark.parametrize(
    "ignored",
    [
        {"resources": None, "ocr": "invalid", "provenance": []},
        {"resources": [{"bad": True}], "ocr": {"configuration": "invalid"}},
    ],
)
def test_ignored_image_and_ocr_configuration_does_not_fail_body_comparison(
    tmp_path, ignored
):
    raw = tmp_path / "body.md"
    raw.write_text("正文规则：付款需要审批。\n")
    result, report = invoke(tmp_path, [document(raw, **ignored)], [document(raw)])
    assert result.returncode == 0, result.stderr
    assert report["results"][0]["status"] == "REUSE"
    assert not report["errors"]


def test_ignored_media_is_still_protected_from_report_overwrite(tmp_path):
    raw = tmp_path / "body.md"
    raw.write_text("正文规则：付款需要审批。\n")
    image = tmp_path / "image.jpg"
    image.write_bytes(b"untouched-media")
    source = document(raw, resources=[{"path": str(image)}])
    result, _ = invoke(tmp_path, [source], [document(raw)], "--output", str(image))
    assert result.returncode == 1
    assert "REPORT_INPUT_COLLISION" in result.stderr
    assert image.read_bytes() == b"untouched-media"


def test_graphify_plan_counts_only_new_body_units_not_reused_or_headings(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    existing = "# 规则\n\n付款必须审批。\n\n到账后发送通知。\n"
    added_fact = "退款必须复核。"
    old.write_text(existing)
    new.write_text(existing + "\n" + added_fact + "\n")
    completed, report = invoke(tmp_path, [document(old)], [document(new)])
    assert completed.returncode == 0, completed.stderr
    row = report["results"][0]
    plan = row["graphify_plan"]
    assert plan["action"] == "BODY_DELTA_CANDIDATE"
    assert plan["automatic_extraction_allowed"] is False
    assert plan["full_document_fallback"] is False
    assert plan["candidate_body_chars"] == len(added_fact)
    selected = [row["units"][index] for index in plan["candidate_unit_indexes"]]
    assert selected and all(unit["kind"] == "add" for unit in selected)
    assert not any(unit["unit_type"] == "heading" for unit in selected)
    assert report["summary"]["graphify_plan"]["candidate_body_chars"] == len(added_fact)
    assert (
        report["summary"]["graphify_plan"]["automatic_full_document_extractions"] == 0
    )


@pytest.mark.parametrize(
    "scenario", ["old_only", "title_uncertain", "budget", "read_failure"]
)
def test_graphify_plan_uncertainty_never_schedules_whole_document(tmp_path, scenario):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("付款必须审批。\n\n退款必须复核。\n")
    new.write_text(old.read_text())
    baseline = document(old)
    incoming = document(new)
    extra = []
    if scenario == "old_only":
        new.write_text("付款必须审批。\n")
    elif scenario == "title_uncertain":
        incoming["metadata"] = {
            "name": "Another subject",
            "logical_path": "Funds",
            "source_ref": "book:1",
        }
    elif scenario == "budget":
        new.write_text("付款无需审批。\n\n退款必须复核。\n")
        extra = ["--max-diff-work", "0"]
    else:
        incoming["path"] = str(tmp_path / "unavailable.md")
    completed, report = invoke(tmp_path, [baseline], [incoming], *extra)
    assert completed.returncode == 2, completed.stderr
    row = report["results"][0]
    plan = row["graphify_plan"]
    assert plan["action"] == "HOLD_EXISTING"
    assert plan["candidate_unit_indexes"] == []
    assert plan["candidate_body_chars"] == 0
    assert plan["automatic_extraction_allowed"] is False
    assert plan["full_document_fallback"] is False
    assert (
        report["summary"]["graphify_plan"]["automatic_full_document_extractions"] == 0
    )


def test_human_report_keeps_old_only_review_separate_from_extraction_work(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("付款必须审批。\n\n退款必须复核。\n")
    new.write_text("付款必须审批。\n")
    human = tmp_path / "knowledge-review.md"
    completed, report = invoke(
        tmp_path, [document(old)], [document(new)], "--report", str(human)
    )
    assert completed.returncode == 2, completed.stderr
    assert report["results"][0]["status"] == "REVIEW"
    text = human.read_text()
    assert "Status: REVIEW" in text
    assert "HOLD_EXISTING" in text
    assert "'candidate_body_chars': 0" in text
    assert "not an extraction queue" in text
    assert "No automatic extraction or full-document fallback" in text
    assert "cannot execute these as patches" in text
    assert (
        report["rules"]["graphify_execution"] == "plan_only_no_full_document_fallback"
    )
    assert human.stat().st_mode & 0o777 == 0o600
