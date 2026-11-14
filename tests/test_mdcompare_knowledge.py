"""Body reuse must retain source scope and local changes independently."""

from dataclasses import replace

import pytest

from mdcompare.io import InputReader
from mdcompare.knowledge import compare_knowledge_documents
from mdcompare.model import CompareOptions, Document


def doc(tmp_path, identifier, text, *, name="Limits", book="book", suffix="old"):
    path = tmp_path / f"{identifier}-{suffix}.md"
    path.write_text(text)
    return Document(
        book,
        identifier,
        "raw",
        path,
        {"name": name, "logical_path": "payments", "source_ref": identifier},
        [],
        tmp_path,
    )


def compare(old, new, **options):
    settings = CompareOptions(**options)
    return compare_knowledge_documents(old, new, settings, InputReader(1_000_000))


def test_partial_reuse_retains_new_fact_and_source_location(tmp_path):
    old = doc(tmp_path, "1", "# Limits\n\n- Payment limit is 10万元.\n")
    new = doc(
        tmp_path,
        "1",
        "# Limits\n\n- Payment limit is 100000元.\n- Settlement is daily.\n",
        suffix="new",
    )
    result = compare([old], [new])["results"][0]
    assert result["counts"]["reuse"] >= 1
    assert result["counts"]["add"] == 1
    assert result["status"] == "DELTA"
    reused = next(item for item in result["units"] if item["kind"] == "reuse")
    assert reused["old"]["document_id"] == "1"
    assert reused["new"]["start_line"] >= 1
    assert reused["unit_type"] in {"heading", "bullet"}
    assert "evidence" not in reused


def test_cross_document_reuse_requires_document_subject_context(tmp_path):
    old = doc(tmp_path, "1", "# Rules\n\nApproval is required.\n", name="Merchant A")
    matching = doc(
        tmp_path, "2", "# Rules\n\nApproval is required.\n", name="Merchant A"
    )
    different = doc(
        tmp_path, "3", "# Rules\n\nApproval is required.\n", name="Merchant B"
    )
    report = compare([old], [matching, different])
    first, second = report["results"]
    assert first["status"] == "REUSE"
    assert all(unit["old"]["document_id"] == "1" for unit in first["units"])
    assert second["counts"]["reuse"] == 0
    assert second["counts"]["add"] > 0


def test_same_id_title_subject_change_does_not_reuse_generic_fact(tmp_path):
    old = doc(tmp_path, "1", "单笔限额10万元。", name="企业A")
    new = doc(tmp_path, "1", "单笔限额10万元。", name="企业B", suffix="new")
    row = compare([old], [new])["results"][0]
    assert row["status"] == "REVIEW"
    assert row["counts"]["reuse"] == 0
    assert row["reason_codes"] == ["DOCUMENT_SUBJECT_CONTEXT_CHANGED"]
    assert row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    assert row["graphify_plan"]["candidate_unit_indexes"]


def test_cross_document_blank_subject_is_not_reuse_evidence(tmp_path):
    old = doc(tmp_path, "a", "允许自动付款。", name="")
    new = doc(tmp_path, "b", "允许自动付款。", name="")
    row = compare([old], [new])["results"][0]
    assert row["counts"]["reuse"] == 0
    assert row["counts"]["add"] == 1


def test_repeated_incoming_knowledge_has_one_add_source(tmp_path):
    a = doc(tmp_path, "a", "A payment requires approval.\n")
    b = doc(tmp_path, "b", "A payment requires approval.\n")
    report = compare([], [b, a])
    first, second = report["results"]
    assert first["counts"]["add"] == 1
    assert second["counts"]["reuse"] == 1
    assert second["units"][0]["old_side"] == "incoming"
    assert second["units"][0]["old"]["document_id"] == "a"


def test_incomplete_baseline_does_not_certify_absence(tmp_path):
    old = doc(tmp_path, "1", "old")
    old.path.unlink()
    new = doc(tmp_path, "2", "A genuinely new fact.")
    report = compare([old], [new])
    row = report["results"][0]
    assert row["status"] == "REVIEW"
    assert row["counts"]["add"] == 0
    assert row["units"][0]["basis"] == "BASELINE_BODY_INCOMPLETE"
    assert report["errors"][0]["side"] == "baseline"


def test_evidence_has_one_shared_budget_per_document(tmp_path):
    old = doc(tmp_path, "1", "First fact.\n\nSecond fact.")
    new = doc(tmp_path, "1", "First fact.\n\nThird fact.", suffix="new")
    row = compare([old], [new], include_evidence=True, max_evidence_chars=7)["results"][
        0
    ]
    assert (
        sum(len(text) for unit in row["units"] for text in unit["evidence"].values())
        <= 7
    )
    assert row["evidence_truncated"]


def test_input_drift_invalidates_cross_document_reuse(tmp_path):
    old = doc(tmp_path, "a", "Shared body knowledge.")
    new = doc(tmp_path, "b", "Shared body knowledge.")

    class DriftingReader(InputReader):
        def changed_paths(self):
            return {old.path.absolute()}

    report = compare_knowledge_documents(
        [old], [new], CompareOptions(), DriftingReader(1_000_000)
    )
    row = report["results"][0]
    assert row["status"] == "UNKNOWN"
    assert row["counts"]["reuse"] == 0
    assert all(unit["kind"] == "unresolved" for unit in row["units"])
    assert report["errors"]


def test_historical_source_limitation_is_not_a_body_gate(tmp_path):
    old = replace(
        doc(tmp_path, "1", "Reusable fact."), content_kind="historical_enriched"
    )
    new = doc(tmp_path, "1", "Reusable fact.", suffix="new")
    row = compare([old], [new])["results"][0]
    assert row["status"] == "REUSE"
    assert row["source_limitations"] == ["HISTORICAL_UNMARKED_TEXT_ORIGIN_UNVERIFIED"]


def test_invalid_encoding_is_local_unknown(tmp_path):
    new = doc(tmp_path, "1", "placeholder")
    new.path.write_bytes(b"\xff\xfe")
    report = compare([], [new])
    assert report["results"][0]["status"] == "UNKNOWN"
    assert report["errors"][0]["reason_code"] == "TEXT_ENCODING_UNSUPPORTED"


def test_old_only_loss_never_triggers_extraction(tmp_path):
    old = doc(tmp_path, "1", "Current business fact.\n\nOld extra passage.")
    new = doc(tmp_path, "1", "Current business fact.", suffix="new")
    row = compare([old], [new])["results"][0]
    assert row["counts"]["old_only_review"] == 1
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"
    assert row["graphify_plan"]["candidate_body_chars"] == 0
    assert not row["graphify_plan"]["automatic_extraction_allowed"]


def test_real_delta_is_bounded_plan_not_whole_file_reextract(tmp_path):
    old = doc(tmp_path, "1", "# Limits\n\n- 单笔限额10万元。\n- 每日结算。")
    new = doc(
        tmp_path, "1", "# Limits\n\n- 单笔限额30万元。\n- 每日结算。", suffix="new"
    )
    report = compare([old], [new])
    row = report["results"][0]
    plan = row["graphify_plan"]
    assert plan["action"] == "BODY_DELTA_CANDIDATE"
    assert len(plan["candidate_unit_indexes"]) == 1
    assert 0 < plan["candidate_body_chars"] < len(new.path.read_text())
    assert plan["full_document_fallback"] is False
    assert (
        report["summary"]["graphify_plan"]["automatic_full_document_extractions"] == 0
    )


def test_budget_exhaustion_holds_existing_knowledge(tmp_path):
    old = doc(tmp_path, "1", "The limit is 100 yuan.")
    new = doc(tmp_path, "1", "The limit is 300 yuan.", suffix="new")
    row = compare([old], [new], max_diff_work=0)["results"][0]
    assert row["comparison_complete"] is False
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"
    assert not row["graphify_plan"]["full_document_fallback"]


def test_existing_body_in_changed_subject_section_remains_a_candidate(tmp_path):
    old = doc(tmp_path, "1", "# USD\n\n- 单笔限额100。")
    new = doc(tmp_path, "1", "# EUR\n\n- 单笔限额100。", suffix="new")
    row = compare([old], [new])["results"][0]
    assert row["status"] == "REVIEW"
    plan = row["graphify_plan"]
    assert plan["action"] == "BODY_DELTA_CANDIDATE"
    assert plan["candidate_unit_indexes"]
    assert plan["context_review_unit_indexes"]
    child = next(
        unit for unit in row["units"] if unit["unit_type"] == "bullet" and unit["new"]
    )
    assert child["body_seen_before"]
    assert child["existing_body"]["document_id"] == "1"
    assert child["kind"] != "reuse"


def test_new_condition_is_reviewed_once_without_reextracting_existing_children(
    tmp_path,
):
    old = doc(tmp_path, "1", "适用主体：企业A。\n\n- 单笔限额100。")
    new = doc(tmp_path, "1", "适用主体：企业B。\n\n- 单笔限额100。", suffix="new")
    row = compare([old], [new])["results"][0]
    plan = row["graphify_plan"]
    assert plan["action"] == "BODY_DELTA_CANDIDATE"
    assert len(plan["candidate_unit_indexes"]) == 1
    assert plan["context_review_unit_indexes"]
    assert all(
        row["units"][ix]["unit_type"] == "paragraph"
        for ix in plan["candidate_unit_indexes"]
    )
    assert not any(unit["kind"] == "reuse" for unit in row["units"] if unit["new"])


@pytest.mark.parametrize(
    "body",
    [
        '<font color="red">单笔限额100元。</font>',
        '| <font color="red">金额</font> | 100元 |',
    ],
)
def test_rich_html_indent_only_difference_stays_review_without_new_body_spend(
    tmp_path, body
):
    old = doc(tmp_path, "1", "    " + body)
    new = doc(tmp_path, "1", "        " + body, suffix="new")
    row = compare([old], [new])["results"][0]
    assert row["status"] == "REVIEW"
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"
    assert row["graphify_plan"]["presentation_review_unit_indexes"]
    assert not row["graphify_plan"]["candidate_unit_indexes"]
    assert any(
        u["presentation_seen_before"] and u["existing_presentation_body"]
        for u in row["units"]
    )
    assert not any(u["kind"] == "reuse" for u in row["units"] if u["new"])


def test_rich_layout_projection_matches_plain_counterpart_without_reuse_claim(
    tmp_path,
):
    old = doc(tmp_path, "1", '    <div class="rich">Existing rule</div>')
    new = doc(tmp_path, "1", "Existing rule", suffix="new")
    row = compare([old], [new])["results"][0]
    assert row["status"] == "REVIEW"
    assert row["counts"]["reuse"] == 0
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"
    assert row["graphify_plan"]["presentation_review_unit_indexes"]


def test_empty_rich_wrappers_are_presentation_not_new_body(tmp_path):
    old = doc(tmp_path, "1", '    <font color="red"></font>')
    new = doc(tmp_path, "1", '        <font color="blue"></font>', suffix="new")
    row = compare([old], [new])["results"][0]
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"
    assert row["graphify_plan"]["presentation_review_unit_indexes"]
    assert not row["graphify_plan"]["candidate_unit_indexes"]


def test_empty_layout_rows_in_indented_rich_table_are_reused(tmp_path):
    old = doc(
        tmp_path,
        "1",
        '    | <font color="red">Name</font> | Limit |\n'
        "    | --- | --- |\n"
        "    | A | 100 |",
    )
    new = doc(
        tmp_path,
        "1",
        "| Name | Limit |\n| --- | --- |\n|  |  |\n| A | 100 |",
        suffix="new",
    )
    row = compare([old], [new])["results"][0]
    assert row["status"] == "REUSE"
    assert row["graphify_plan"]["action"] == "KEEP_EXISTING"
    assert not row["graphify_plan"]["candidate_unit_indexes"]


@pytest.mark.parametrize(
    "before,after",
    [
        ("    <font>100元</font>", "        <font>300元</font>"),
        ("```html\n<font>100元</font>\n```", "```html\n    <font>100元</font>\n```"),
        ("    <pre>100元</pre>", "        <pre>100元</pre>"),
        ("    <Amt>100</Amt>", "        <Amt>100</Amt>"),
        (
            "```python\nif ready:\n    pay()\nsettle()\n```",
            "```python\nif ready:\n    pay()\n    settle()\n```",
        ),
    ],
)
def test_layout_planning_never_suppresses_changed_values_or_literal_code(
    tmp_path, before, after
):
    old = doc(tmp_path, "1", before)
    new = doc(tmp_path, "1", after, suffix="new")
    row = compare([old], [new])["results"][0]
    assert row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    assert not row["graphify_plan"]["presentation_review_unit_indexes"]
