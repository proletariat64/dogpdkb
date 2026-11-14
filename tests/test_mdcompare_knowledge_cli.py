"""Body-knowledge CLI acceptance, separate from strict processing advice."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest  # pyright: ignore[reportMissingImports] -- installed in the test environment


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


def invoke(
    tmp_path, baseline, incoming, *extra
) -> tuple[subprocess.CompletedProcess, Any]:
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


@pytest.mark.parametrize(
    "before,after",
    [
        (
            '正文相同\n<div class="old"><span><img src="missing-a.png"></span></div>',
            '正文相同\n<div><span class="new">![图片](missing-b.png)</span></div>',
        ),
        (
            "正文相同\n<div><span>[![图片](missing-a.png)](/old)</span></div>",
            '正文相同\n<div class="new">![图片](missing-b.png)</div>',
        ),
        (
            '正文相同\n<a href="/old"><img src="missing-a.png"></a>',
            '正文相同\n<a href="/new"><img src="missing-b.png"></a>',
        ),
        (
            '[接口][api]\n\n<div><img src="missing-a.png"></div>\n\n[api]: /pay',
            (
                '[接口][api]\n\n<div class="new"><img src="missing-b.png"></div>'
                "\n\n[api]: /pay"
            ),
        ),
    ],
)
def test_excluded_image_wrappers_remain_reusable(tmp_path, before, after):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(before)
    new.write_text(after)
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 0, result.stderr
    row = report["results"][0]
    assert row["status"] == "REUSE"
    assert row["graphify_plan"]["action"] == "KEEP_EXISTING"
    assert not report["errors"]


def test_verified_generated_region_inside_code_fence_is_outside_body(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    template = (
        "正文规则：退款需要审批。\n\n```text\n"
        "<!-- mdocr:ocr-begin -->\n{}\n<!-- mdocr:ocr-end -->\n```\n"
    )
    old.write_text(template.format("旧派生文字"))
    new.write_text(template.format("新派生文字"))
    scope = {
        "version": "generated-regions-v1",
        "regions": [{"kind": "mdocr_ocr", "start_line": 4, "end_line": 6}],
    }
    result, report = invoke(
        tmp_path,
        [document(old, body_scope=scope)],
        [document(new, body_scope=scope)],
    )
    assert result.returncode == 0, result.stderr
    assert report["rules"]["version"] == "body-units-v5"
    assert report["rules"]["generated_regions"] == (
        "outside_code_or_verified_generated_regions_v1"
    )
    row = report["results"][0]
    assert row["status"] == "REUSE"
    assert row["graphify_plan"]["action"] == "KEEP_EXISTING"
    assert "旧派生文字" not in json.dumps(report, ensure_ascii=False)
    assert "新派生文字" not in json.dumps(report, ensure_ascii=False)


def test_verified_generated_region_inside_indented_export_is_outside_body(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    template = (
        "正文规则：退款需要审批。\n\n    <!-- mdocr:ocr-begin -->\n"
        "    {}\n    <!-- mdocr:ocr-end -->\n"
    )
    old.write_text(template.format("旧派生文字"))
    new.write_text(template.format("新派生文字"))
    scope = {
        "version": "generated-regions-v1",
        "regions": [{"kind": "mdocr_ocr", "start_line": 3, "end_line": 5}],
    }
    result, report = invoke(
        tmp_path,
        [document(old, body_scope=scope)],
        [document(new, body_scope=scope)],
    )
    assert result.returncode == 0, result.stderr
    assert report["results"][0]["status"] == "REUSE"
    assert report["results"][0]["graphify_plan"]["action"] == "KEEP_EXISTING"


def test_undeclared_marker_text_in_code_remains_literal(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    template = (
        "```text\n<!-- mdocr:ocr-begin -->\nvalue={}\n<!-- mdocr:ocr-end -->\n```\n"
    )
    old.write_text(template.format("100"))
    new.write_text(template.format("200"))
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "REVIEW"
    assert row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    assert row["graphify_plan"]["candidate_body_chars"] > 0


@pytest.mark.parametrize(
    "before,after",
    [
        (
            "```js\nconst x = '<p id=\"a\"></p>';\n```",
            "```js\nconst x = '<p id=\"b\"></p>';\n```",
        ),
        (
            "    const x = '<p id=\"a\"></p>';",
            "    const x = '<p id=\"b\"></p>';",
        ),
        ('Use `<p id="a"></p>` literally.', 'Use `<p id="b"></p>` literally.'),
        (
            '规则：审批。\n<div data-subject="A"></div>',
            '规则：审批。\n<div data-subject="B"></div>',
        ),
        (
            '规则：审批。\n<div data-subject="![a](a.png)"></div>',
            '规则：审批。\n<div data-subject="![b](b.png)"></div>',
        ),
        (
            '规则：审批。\n<div data-subject="A"><img src="missing-a.png"></div>',
            '规则：审批。\n<div data-subject="B"><img src="missing-b.png"></div>',
        ),
        (
            '规则：审批。\n<div style="display:none"><img src="missing-a.png"></div>',
            '规则：审批。\n<div><img src="missing-b.png"></div>',
        ),
        ('规则：审批。\n<a href="/a"></a>', '规则：审批。\n<a href="/b"></a>'),
        (
            '规则：审批。\n<a href="/image" data-subject="A"><img src="a.png"></a>',
            '规则：审批。\n<a href="/image" data-subject="B"><img src="b.png"></a>',
        ),
        ("```js\nconst x = '[](/a)';\n```", "```js\nconst x = '[](/b)';\n```"),
        ("规则：审批。\n[](/a)", "规则：审批。\n[](/b)"),
    ],
)
def test_image_cleanup_does_not_hide_code_or_semantic_markup_changes(
    tmp_path, before, after
):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(before)
    new.write_text(after)
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "REVIEW"
    assert row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    assert row["graphify_plan"]["candidate_body_chars"] > 0


def test_image_syntax_in_attribute_does_not_authorize_wrapper_removal(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(
        '付款必须经过审批后才能处理。\n<figure class="![x](missing.png)"></figure>'
    )
    new.write_text("付款必须经过审批后才能处理。")
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    assert report["results"][0]["status"] == "REVIEW"


def test_generated_region_declaration_must_match_source_location(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    text = (
        "正文\n\n```text\n<!-- mdocr:ocr-begin -->\n派生\n<!-- mdocr:ocr-end -->\n```\n"
    )
    old.write_text(text)
    new.write_text(text)
    scope = {
        "version": "generated-regions-v1",
        "regions": [{"kind": "mdocr_ocr", "start_line": 5, "end_line": 6}],
    }
    result, report = invoke(
        tmp_path,
        [document(old, body_scope=scope)],
        [document(new, body_scope=scope)],
    )
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "UNKNOWN"
    assert row["reason_codes"] == ["BODY_SCOPE_LOCATION_MISMATCH"]
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"


def test_unclosed_generated_region_is_unknown_and_holds_existing(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("已知正文\n\n<!-- mdocr:ocr-begin -->\n旧尾部")
    new.write_text("已知正文\n\n<!-- mdocr:ocr-begin -->\n新尾部")
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "UNKNOWN"
    assert row["comparison_complete"] is False
    assert row["reason_codes"] == ["BODY_SCOPE_BOUNDARY_UNCLOSED"]
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"
    assert row["graphify_plan"]["candidate_unit_indexes"] == []


def test_literal_code_end_marker_does_not_close_outer_generated_region(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    text = "<!-- mdocr:ocr-begin -->\n```text\n<!-- mdocr:ocr-end -->\n```\n"
    old.write_text(text)
    new.write_text(text)
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "UNKNOWN"
    assert row["reason_codes"] == ["BODY_SCOPE_BOUNDARY_UNCLOSED"]
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"


@pytest.mark.parametrize(
    "text",
    [
        "正文\n\n<!-- mdocr:ocr-end -->\n",
        (
            "<!-- mdocr:ocr-begin -->\n<!-- mdocr:ocr-begin -->\n"
            "<!-- mdocr:ocr-end -->\n<!-- mdocr:ocr-end -->\n"
        ),
    ],
)
def test_orphan_or_nested_generated_boundaries_are_unknown(tmp_path, text):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(text)
    new.write_text(text)
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "UNKNOWN"
    assert row["reason_codes"] == ["BODY_SCOPE_BOUNDARY_INVALID"]
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"


def test_unclosed_legacy_generated_comment_is_unknown(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("已知正文\n\n<!-- 这是一张图片，ocr 内容为：\n旧尾部")
    new.write_text("已知正文\n\n<!-- 这是一张图片，ocr 内容为：\n新尾部")
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "UNKNOWN"
    assert row["reason_codes"] == ["BODY_SCOPE_BOUNDARY_UNCLOSED"]
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"


def test_unpaired_legacy_generated_label_is_unknown_without_tail_reuse(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    text = "> **图片转写：** generated\n\n未标记业务规则：限额100元。"
    old.write_text(text)
    new.write_text(text)
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "UNKNOWN"
    assert row["reason_codes"] == ["BODY_SCOPE_BOUNDARY_UNCLOSED"]
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"


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


def test_unmarked_historical_text_is_not_guessed_to_be_generated(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("未标记历史说明\n\n业务规则：退款需要审批。")
    new.write_text("业务规则：退款需要审批。")
    result, report = invoke(
        tmp_path,
        [document(old, content_kind="historical_enriched")],
        [document(new)],
    )
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "REVIEW"
    assert row["counts"]["old_only_review"] == 1
    assert row["source_limitations"] == ["HISTORICAL_UNMARKED_TEXT_ORIGIN_UNVERIFIED"]
    assert row["graphify_plan"]["action"] == "HOLD_EXISTING"


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


@pytest.mark.parametrize(
    "before,after",
    [
        ("单笔限额100元。", "单笔限额200元。"),
        ("|主体|限额|\n|---|---|\n|企业A|100|", "|主体|限额|\n|---|---|\n|100|企业A|"),
        ("[API](https://example.invalid/pay)", "[API](https://example.invalid/refund)"),
        ("# 流程\n\n1. 申请\n2. 审批", "# 流程\n\n1. 审批\n2. 申请"),
        (
            "```python\nif ready:\n    pay()\nsettle()\n```",
            "```python\nif ready:\n    pay()\n    settle()\n```",
        ),
    ],
)
def test_meaningful_financial_table_url_order_and_code_changes_remain_candidates(
    tmp_path, before, after
):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(before)
    new.write_text(after)
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "REVIEW"
    assert row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    assert row["graphify_plan"]["candidate_body_chars"] > 0


@pytest.mark.parametrize(
    "before,after",
    [
        ('<div style="display:none">允许付款</div>', "<div>允许付款</div>"),
        ('<p style="display:none">允许付款</p>', "<p>允许付款</p>"),
        ('允许<br style="display:none"/>付款', "允许<br/>付款"),
        ("<p hidden>允许付款</p>", "<p>允许付款</p>"),
        (
            '<div data-subject="A">允许付款</div>',
            '<div data-subject="B">允许付款</div>',
        ),
        ('允许<br aria-hidden="true"/>付款', "允许<br/>付款"),
        ('<div style="position:absolute">允许付款</div>', "<div>允许付款</div>"),
    ],
)
def test_html_table_cell_semantic_attributes_require_review(tmp_path, before, after):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    table = "<table><tr><th>规则</th></tr><tr><td>{}</td></tr></table>"
    old.write_text(table.format(before))
    new.write_text(table.format(after))
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 2, result.stderr
    row = report["results"][0]
    assert row["status"] == "REVIEW"
    assert row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    assert row["graphify_plan"]["candidate_body_chars"] > 0


def test_html_table_cell_safe_presentation_still_reuses_knowledge(tmp_path):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text(
        '<table><tr><th>规则</th></tr><tr><td><div style="font-size:14px">'
        '付款</div><br style="color:red"/><p class="note" '
        'style="font-family:serif">审批</p></td></tr></table>'
    )
    new.write_text("<table><tr><th>规则</th></tr><tr><td>付款 审批</td></tr></table>")
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 0, result.stderr
    row = report["results"][0]
    assert row["status"] == "REUSE"
    assert row["graphify_plan"]["action"] == "KEEP_EXISTING"


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


@pytest.mark.parametrize("scenario", ["old_only", "budget", "read_failure"])
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


@pytest.mark.parametrize(
    "old_header,new_header", [(False, True), (True, False), (True, True)]
)
@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_verified_exporter_metadata_is_not_body_knowledge(
    tmp_path, old_header, new_header, ending
):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"

    def exported(header, count):
        metadata = (
            f"doc_id: 1 · book: Example book (book_id 42) · word_count: {count}\n"
            if header
            else "\n"
        )
        return "# Limits\n" + metadata + "Refunds need approval.\n"

    old.write_text(exported(old_header, 20).replace("\n", ending))
    new.write_text(exported(new_header, 21).replace("\n", ending))
    original = old.read_bytes(), new.read_bytes()
    result, report = invoke(tmp_path, [document(old)], [document(new)])
    assert result.returncode == 0, result.stderr
    row = report["results"][0]
    assert row["status"] == "REUSE"
    assert row["graphify_plan"]["action"] == "KEEP_EXISTING"
    assert row["units"][-1]["new"]["start_line"] == 3
    assert original == (old.read_bytes(), new.read_bytes())


@pytest.mark.parametrize(
    "prefix,line",
    [
        ("# Limits\n", "doc_id: 2 · book: Example book (book_id 42) · word_count: 20"),
        (
            "# Other subject\n",
            "doc_id: 1 · book: Example book (book_id 42) · word_count: 20",
        ),
        (
            "# Limits\n",
            "doc_id: 1 · book: Example book (book_id 42) · word_count: 20 extra rule",
        ),
        (
            "# Limits\n",
            "    doc_id: 1 · book: Example book (book_id 42) · word_count: 20",
        ),
        (
            "# Limits\n\nBusiness rule.\n\n",
            "doc_id: 1 · book: Example book (book_id 42) · word_count: 20",
        ),
        ("```text\n", "doc_id: 1 · book: Example book (book_id 42) · word_count: 20"),
    ],
)
def test_exporter_header_lookalikes_remain_observable_body(tmp_path, prefix, line):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("# Limits\n\nRefunds need approval.\n")
    new.write_text(prefix + line + "\n\nRefunds need approval.\n")
    _, report = invoke(tmp_path, [document(old)], [document(new)])
    row = report["results"][0]
    assert row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"


def test_unrelated_bad_baseline_does_not_hide_local_body_candidate(tmp_path):
    old, bad, new = (tmp_path / name for name in ("old.md", "bad.md", "new.md"))
    old.write_text("# Limits\n\nRefunds need approval.\n")
    bad.write_text("# Limits\n<!-- mdocr:ocr-begin -->\nunclosed derived region\n")
    new.write_text(
        "# Limits\n\nRefunds need approval.\n\nDaily settlement requires reconciliation.\n"
    )
    _, report = invoke(tmp_path, [document(old), document(bad, "2")], [document(new)])
    row = report["results"][0]
    assert report["errors"][0]["document_id"] == "2"
    assert row["comparison_complete"]
    assert row["status"] == "DELTA"
    assert row["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"
    assert "BASELINE_BODY_INCOMPLETE" not in row["reason_codes"]


@pytest.mark.parametrize("subject_kind", ["document", "section"])
def test_meaningful_subject_change_remains_extraction_candidate(tmp_path, subject_kind):
    old, new = tmp_path / "old.md", tmp_path / "new.md"
    old.write_text("# USD\n\n- Transfer limit is 100.\n")
    new.write_text("# EUR\n\n- Transfer limit is 100.\n")
    incoming = document(new)
    if subject_kind == "document":
        incoming["metadata"]["name"] = "EUR accounts"
    _, report = invoke(tmp_path, [document(old)], [incoming])
    assert report["results"][0]["graphify_plan"]["action"] == "BODY_DELTA_CANDIDATE"


def test_added_verified_exporter_title_does_not_reextract_existing_body(tmp_path):
    old, new = tmp_path / "old.md", tmp_path / "new.md"
    old.write_text("# USD\n\n- Transfer limit is 100.\n")
    new.write_text(
        "# Limits\ndoc_id: 1 · book: Example (book_id 99) · word_count: 20\n# USD\n\n- Transfer limit is 100.\n"
    )
    _, report = invoke(tmp_path, [document(old)], [document(new)])
    assert report["results"][0]["graphify_plan"]["action"] != "BODY_DELTA_CANDIDATE"


@pytest.mark.parametrize(
    "case,expected",
    [
        ("old_only", "OLD_BODY_ONLY"),
        ("unclosed", "COMPARISON_UNAVAILABLE"),
        ("budget", "COMPARISON_INCOMPLETE"),
    ],
)
def test_hold_reason_codes_distinguish_retention_from_unavailable_body(
    tmp_path, case, expected
):
    old, new = tmp_path / "old.md", tmp_path / "new.md"
    old.write_text("Approve refunds.\n\nReconcile payments.\n")
    new.write_text("Approve refunds.\n")
    extra = []
    if case == "unclosed":
        old.write_text("Approve refunds.\n<!-- mdocr:ocr-begin -->\nunknown tail")
    elif case == "budget":
        new.write_text("Automatically approve refunds.\n\nReconcile payments.\n")
        extra = ["--max-diff-work", "0"]
    _, report = invoke(tmp_path, [document(old)], [document(new)], *extra)
    plan = report["results"][0]["graphify_plan"]
    assert plan["action"] == "HOLD_EXISTING"
    assert expected in plan["hold_reason_codes"]


def test_repeated_identical_body_in_unchanged_sections_stays_reusable(tmp_path):
    old, new = tmp_path / "old.md", tmp_path / "new.md"
    text = "# USD\n\n- Transfer limit is 100.\n\n# EUR\n\n- Transfer limit is 100.\n"
    old.write_text(text)
    new.write_text(text)
    _, report = invoke(tmp_path, [document(old)], [document(new)])
    assert report["results"][0]["graphify_plan"]["action"] == "KEEP_EXISTING"
