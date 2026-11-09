"""Knowledge-unit behavior, separate from strict file/OCR processing advice."""

import pytest

from mdcompare.knowledge_units import align_units, extract_units


def compare(old, new, **kwargs):
    return align_units(extract_units(old), extract_units(new), **kwargs)


def incoming_kinds(result):
    return [item["kind"] for item in result["items"] if item["new"] is not None]


def test_partial_reuse_keeps_new_and_old_only_knowledge_with_locations():
    result = compare(
        "# Limits\n\n- USD limit is 100\n- EUR limit is 200\n",
        "# Limits\n\n- USD limit is 100\n- The account must be active\n",
    )
    assert incoming_kinds(result) == ["reuse", "reuse", "add"]
    assert [
        i["old"].text for i in result["items"] if i["kind"] == "old_only_review"
    ] == ["EUR limit is 200"]
    reused = result["items"][1]
    assert reused["old"].start_line == reused["new"].start_line == 3
    assert reused["old"].end_line == 3
    assert reused["old"].context == ("Limits",)


def test_unordered_independent_facts_can_reorder_without_reextraction():
    result = compare("- USD account\n- EUR account\n", "* EUR account\n* USD account\n")
    assert incoming_kinds(result) == ["reuse", "reuse"]
    assert all(i["kind"] == "reuse" for i in result["items"])


@pytest.mark.parametrize(
    "before,after",
    [
        ("限额为10万元", "限额为100000元"),
        ("限额为1.5亿元", "限额为150000000元"),
        ("限额为100,000.00美元", "限额为10万美元"),
        ("费率为10％", "费率为10%"),
        ("- **限额**为10万元", "* 限额为100000元"),
        ("Payment limit is 10万元.", "Payment limit is 100000元."),
    ],
)
def test_explicit_decimal_quantity_equivalence_is_reusable(before, after):
    assert incoming_kinds(compare(before, after)) == ["reuse"]


@pytest.mark.parametrize(
    "before,after",
    [
        ("限额为10万元", "限额为100001元"),
        ("限额为10万美元", "限额为10万元"),
        ("允许付款", "不允许付款"),
        ("付款人承担费用", "收款人承担费用"),
        ("仅限正常账户付款", "账户付款"),
        ("amount ≤ 100", "amount < 100"),
        ("[API](https://example.invalid/pay)", "[API](https://example.invalid/refund)"),
        ("code=001", "code=1"),
        ("[x](https://host/10万元)", "[x](https://host/100000元)"),
        ("[x](../10万元)", "[x](../100000元)"),
        ("Use `10万元` as ID", "Use `100000元` as ID"),
        ("1234567890123456789012345678901元", "1234567890123456789012345678902元"),
    ],
)
def test_fact_changes_never_become_reuse(before, after):
    assert "reuse" not in incoming_kinds(compare(before, after))


def test_table_cell_bindings_and_ordered_workflow_survive_extraction():
    old = "|账户|限额|\n|---|---|\n|A|100|\n|B|200|\n\n1. 申请\n2. 审批\n"
    new = "|账户|限额|\n|---|---|\n|A|200|\n|B|100|\n\n1. 审批\n2. 申请\n"
    units = extract_units(old)
    assert [u.kind for u in units] == ["table", "ordered"]
    assert "reuse" not in incoming_kinds(compare(old, new))


def test_code_indentation_and_literal_image_syntax_are_preserved():
    old = '```python\nif ready:\n    submit()\nprint("![literal](old.png)")\n```'
    new = '```python\nif ready:\n    submit()\n    print("![literal](new.png)")\n```'
    unit = extract_units(old)[0]
    assert unit.kind == "code"
    assert "old.png" in unit.text
    assert "    submit()" in unit.key
    assert "reuse" not in incoming_kinds(compare(old, new))
    assert (
        "![literal](a.png)" in extract_units("Use `![literal](a.png)` syntax")[0].text
    )


def test_different_heading_and_nested_subject_contexts_do_not_reuse():
    old = "# USD\n\n- limit\n  - 100\n"
    new = "# EUR\n\n- limit\n  - 100\n"
    assert "reuse" not in incoming_kinds(compare(old, new))
    units = extract_units("- USD\n  - limit is 100\n- EUR\n  - limit is 100\n")
    assert units[1].context == ("USD",)
    assert units[3].context == ("EUR",)


def test_images_ocr_and_reference_targets_do_not_affect_body_results():
    old = """# Body

Existing knowledge ![old [alt]](old(image).png).

![diagram][image]
[image]: /old.png

<picture><source srcset='old.webp'><img src='old.png'></picture>
<svg><text>old OCR-like diagram text</text></svg>

<!-- mdocr:ocr-begin id=old -->
Unstable old OCR text
<!-- mdocr:ocr-end -->
"""
    new = "# Body\n\nExisting knowledge ![new alt](new.png).\n"
    result = compare(old, new)
    assert result["complete"] is True
    assert all(item["kind"] == "reuse" for item in result["items"])
    assert "OCR" not in repr(result)
    assert "old.png" not in repr(result)
    assert "new.png" not in repr(result)


def test_only_image_ocr_changes_produce_no_knowledge_tasks():
    old = "![a](old.png)\n<!-- mdocr:ocr-begin -->old<!-- mdocr:ocr-end -->"
    new = "![b](new.png)\n<!-- mdocr:ocr-begin -->new<!-- mdocr:ocr-end -->"
    assert compare(old, new) == {"items": [], "complete": True}
    assert compare("", "![new](new.png)") == {"items": [], "complete": True}


def test_unclosed_excluded_region_is_not_a_reusable_prefix():
    old = "Known body\n\n<!-- mdocr:ocr-begin -->\nold extraction"
    with pytest.raises(ValueError, match="BODY_SCOPE_BOUNDARY_UNCLOSED"):
        extract_units(old)
    assert compare("<svg>old text", "<svg>new text") == {"items": [], "complete": True}


def test_inline_html_image_removal_preserves_sibling_body_and_locations():
    old = "prefix <img alt='old > text' src='old.png'> suffix\n\nRule"
    new = "prefix <img src='new.png'> suffix\n\nRule"
    result = compare(old, new)
    assert incoming_kinds(result) == ["reuse", "reuse"]
    assert result["items"][1]["new"].start_line == 3


def test_shared_ordinary_link_reference_still_retains_target_evidence():
    old = "![diagram][x]\n\n[Download][x]\n\n[x]: /old\n"
    new = "![diagram][x]\n\n[Download][x]\n\n[x]: /new\n"
    result = compare(old, new)
    assert any(i["kind"] != "reuse" for i in result["items"])
    assert result["items"][0]["kind"] != "reuse"


def test_multiline_image_reference_declarations_and_source_tags_are_excluded():
    old = "正文相同\n\n![a][id]\n\n[id]:\n  a.png\n  'old title'\n\n<source srcset='a.png'>"
    new = "正文相同\n\n![b][id]\n\n[id]:\n  b.png\n  'new title'\n\n<source srcset='b.png'>"
    result = compare(old, new)
    assert incoming_kinds(result) == ["reuse"]
    assert all(i["kind"] == "reuse" for i in result["items"])


def test_image_escape_parity_excludes_real_images_after_escaped_backslash():
    assert incoming_kinds(compare(r"正文 \\![a](a.png)", r"正文 \\![b](b.png)")) == [
        "reuse"
    ]
    assert "a.png" in extract_units(r"literal \![a](a.png)")[0].text


def test_preceding_scope_and_actor_paragraphs_bind_following_facts():
    for old_scope, new_scope in [
        ("适用主体：企业A。", "适用主体：企业B。"),
        ("Applicable to company A.", "Applicable to company B."),
        ("For company A", "For company B"),
    ]:
        result = compare(
            old_scope + "\n\n- 单笔限额10万元。", new_scope + "\n\n- 单笔限额10万元。"
        )
        assert "reuse" not in incoming_kinds(result)


def test_workflow_heading_preserves_order_even_for_unordered_markup():
    result = compare("# 流程\n\n- 申请\n- 审批", "# 流程\n\n- 审批\n- 申请")
    assert incoming_kinds(result) == ["reuse", "revise"]


def test_nested_list_subject_survives_intervening_blank_line():
    result = compare("- 企业A\n\n  - 限额100元", "- 企业B\n\n  - 限额100元")
    assert "reuse" not in incoming_kinds(result)


def test_image_insert_does_not_split_an_existing_body_paragraph():
    old = "正文第一句。\n正文第二句。"
    new = "正文第一句。\n![x](a.png)\n正文第二句。"
    assert incoming_kinds(compare(old, new)) == ["reuse"]


def test_image_wrapper_presentation_changes_are_ignored():
    old = '正文相同\n<div><img src="a.png"></div>'
    new = '正文相同\n<div class="new"><img src="b.png"></div>'
    assert incoming_kinds(compare(old, new)) == ["reuse"]


def test_shortcut_link_shared_with_image_keeps_changed_target():
    old = "正文\n[下载]\n![a][下载]\n[下载]: a.png"
    new = "正文\n[下载]\n![a][下载]\n[下载]: b.png"
    assert "reuse" not in incoming_kinds(compare(old, new))


def test_multiline_link_target_and_first_reference_definition_are_bound():
    old = "[API][id]\n\n[id]:\n  https://example.test/v1"
    new = "[API][id]\n\n[id]:\n  https://example.test/v2"
    assert compare(old, new)["items"][0]["kind"] != "reuse"
    old = "[API][id]\n\n[id]: /v1\n[id]: /v2"
    new = "[API][id]\n\n[id]: /v3\n[id]: /v2"
    assert compare(old, new)["items"][0]["kind"] != "reuse"


@pytest.mark.parametrize(
    "before,after",
    [
        ("1. 企业A规则\n2. 适用账户", "1. 企业B规则\n2. 适用账户"),
        ("```text\n企业A\n```", "```text\n企业B\n```"),
        (
            "|主体|条件|\n|---|---|\n|企业A|正常|",
            "|主体|条件|\n|---|---|\n|企业B|正常|",
        ),
    ],
)
def test_preceding_structured_scope_binds_following_list(before, after):
    result = compare(before + "\n\n- 单笔限额10万元", after + "\n\n- 单笔限额10万元")
    assert "reuse" not in incoming_kinds(result)


def test_cosmetic_markup_and_entities_share_body_and_heading_context_keys():
    old = '# <font color="red">Limits</font>\n\n<font style="font-size:14px"><span>Amount&nbsp;is 10万元.</span></font>\n\n- Rule A'
    new = "# Limits\n\nAmount is 100000元.\n\n- Rule A"
    assert all(i["kind"] == "reuse" for i in compare(old, new)["items"])


@pytest.mark.parametrize(
    "old,new",
    [
        ('<span style="display:none">Allowed</span>', "Allowed"),
        ('<span style="text-decoration:line-through">Allowed</span>', "Allowed"),
        ("<s>Allowed</s>", "Allowed"),
        (
            '<rule amount="10万元">Allowed</rule>',
            '<rule amount="100000元">Allowed</rule>',
        ),
        ('`<font color="red">ID</font>`', "`ID`"),
        ("[API](https://host/<font>10万元</font>)", "[API](https://host/100000元)"),
        ("[API](https://host/<font>pay</font>)", "[API](https://host/pay)"),
        ('<rule value="<font>pay</font>">X</rule>', '<rule value="pay">X</rule>'),
    ],
)
def test_cosmetic_normalization_retains_semantic_markup_and_literal_payload(old, new):
    assert "reuse" not in incoming_kinds(compare(old, new))


@pytest.mark.parametrize(
    "before,after",
    [
        ('<div class="rich">付款需要审批。</div>', "付款需要审批。"),
        ('<p style="text-align:center">付款需要审批。</p>', "付款需要审批。"),
        ("<strong>付款需要审批。</strong>", "付款需要审批。"),
        ("<em>付款需要审批。</em>", "付款需要审批。"),
    ],
)
def test_safe_rich_text_wrappers_match_plain_body(before, after):
    assert incoming_kinds(compare(before, after)) == ["reuse"]


@pytest.mark.parametrize(
    "before,after",
    [
        (
            '<div data-subject="A">允许付款</div>',
            '<div data-subject="B">允许付款</div>',
        ),
        ('<p style="display:none">允许付款</p>', "允许付款"),
        ('<strong title="condition A">允许付款</strong>', "允许付款"),
    ],
)
def test_rich_wrapper_normalization_keeps_unknown_semantics(before, after):
    assert "reuse" not in incoming_kinds(compare(before, after))


def test_empty_table_layout_rows_do_not_create_knowledge_changes():
    old = "| Name | Limit |\n| --- | --- |\n| A | 100 |"
    new = "| Name | Limit |\n| --- | --- |\n|   |   |\n| A | 100 |\n| <br/> | &nbsp; |"
    assert incoming_kinds(compare(old, new)) == ["reuse"]
    html = "<table><tr><th>Name</th><th>Limit</th></tr><tr><td></td><td><br/></td></tr><tr><td>A</td><td>100</td></tr></table>"
    assert incoming_kinds(compare(html, old)) == ["reuse"]


@pytest.mark.parametrize(
    "changed",
    [
        "| Name | Limit |\n| --- | --- |\n| A | 200 |",
        "| Name | Limit |\n| --- | --- |\n| 100 | A |",
        "| Name | Limit |\n| --- | --- |\n| A | 100 |\n| B |  |",
    ],
)
def test_empty_row_normalization_preserves_nonempty_bindings_and_empty_cells(changed):
    old = "| Name | Limit |\n| --- | --- |\n| A | 100 |"
    assert "reuse" not in incoming_kinds(compare(old, changed))


def test_simple_html_table_matches_markdown_without_losing_cell_bindings():
    old = '<table>\n<thead><tr><th>Account</th><th>Limit</th></tr></thead>\n<tbody><tr><td><font color="red">USD</font></td><td>10万元</td></tr></tbody>\n</table>'
    new = "| Account | Limit |\n| --- | --- |\n| USD | 100000元 |"
    result = compare(old, new)
    assert incoming_kinds(result) == ["reuse"]
    assert result["items"][0]["old"].kind == "table"
    assert result["items"][0]["old"].end_line == 4
    swapped = "| Account | Limit |\n| --- | --- |\n| 100000元 | USD |"
    assert "reuse" not in incoming_kinds(compare(old, swapped))


def test_html_table_links_code_and_line_breaks_match_markdown_cells():
    old = '<table><tr><th>API</th><th>Code</th></tr><tr><td><a href="https://host/pay">Pay</a><br />now</td><td><code>10万元</code></td></tr></table>'
    new = "|API|Code|\n|---|---|\n|[Pay](https://host/pay)<br />now|`10万元`|"
    assert incoming_kinds(compare(old, new)) == ["reuse"]
    changed = new.replace("`10万元`", "`100000元`")
    assert "reuse" not in incoming_kinds(compare(old, changed))


@pytest.mark.parametrize(
    "html_table,markdown",
    [
        ("<table><tr><td>A|B</td></tr></table>", "|A|B|\n|---|---|"),
        ("<table><tr><td>A</td><td>B</td></tr></table>", "|A\\|B|\n|---|"),
        ('<table><tr><td colspan="2">A</td></tr></table>', "|A||\n|---|---|"),
        (
            '<table><tr><td rowspan="2">A</td></tr><tr><td>B</td></tr></table>',
            "|A|\n|---|\n|B|",
        ),
        (
            "<table><tr><td><table><tr><td>A</td></tr></table></td></tr></table>",
            "|A|\n|---|",
        ),
        ("<table><tr><td><s>A</s></td></tr></table>", "|A|\n|---|"),
    ],
)
def test_unsupported_table_shapes_and_separators_never_collapse(html_table, markdown):
    assert "reuse" not in incoming_kinds(compare(html_table, markdown))


def test_explicit_legacy_generated_regions_are_excluded_without_content_judgment():
    old = """Known body

<!-- 这是一张图片，ocr 内容为：old unstable content -->

> **图片转写：** generated-file — source
old unstable content
> 转写说明：generated

<div align="center">[PaddleOCR image region: untranscribed]</div>
"""
    assert incoming_kinds(compare(old, "Known body")) == ["reuse"]


def test_unpaired_legacy_label_does_not_reuse_unmarked_following_business_section():
    with pytest.raises(ValueError, match="BODY_SCOPE_BOUNDARY_UNCLOSED"):
        extract_units("> **图片转写：** generated\n\nBusiness rule is 100 USD")


def test_old_only_non_scope_paragraph_does_not_cascade_into_new_rule():
    old = "Generated explanatory note\n\n- Existing rule remains"
    new = "- Existing rule remains"
    result = compare(old, new)
    assert incoming_kinds(result) == ["reuse"]
    assert [i["kind"] for i in result["items"]] == ["reuse", "old_only_review"]


@pytest.mark.parametrize(
    "scope", ["actor Alice", "付款方", "收款方", "商户", "MerchantId", "仅适用规则"]
)
def test_explicit_subject_and_condition_frames_continue_to_bind_rules(scope):
    result = compare(scope + " A\n\n- Limit 100", scope + " B\n\n- Limit 100")
    assert "reuse" not in incoming_kinds(result)


def test_zero_fuzzy_budget_keeps_exact_reuse_and_marks_only_unmatched_units():
    result = compare("- Same\n- Before\n", "- After\n- Same\n", max_work=0)
    assert incoming_kinds(result) == ["unresolved", "reuse"]
    assert result["complete"] is False
    assert result["items"][0]["basis"] == "BODY_MATCH_BUDGET_EXCEEDED"


def test_similarity_is_revision_candidate_never_equivalence_proof():
    result = compare("Payment limit is 100 USD", "Payment limit is 200 USD")
    assert incoming_kinds(result) == ["revise"]
    assert result["items"][0]["basis"] == "SIMILAR_BODY_REVISION_CANDIDATE"


def test_repeated_incoming_fact_reuses_single_existing_knowledge_unit():
    result = compare("- Same fact", "- Same fact\n- Same fact")
    assert incoming_kinds(result) == ["reuse", "reuse"]
    assert all(i["kind"] == "reuse" for i in result["items"])


@pytest.mark.parametrize(
    "tag,attribute,before,after",
    [
        ("table", "data-subject", "merchant-a", "merchant-b"),
        ("tr", "data-subject", "merchant-a", "merchant-b"),
        ("td", "data-currency", "USD", "EUR"),
        ("th", "data-field", "amount", "fee"),
    ],
)
def test_semantic_table_attributes_are_not_discarded(tag, attribute, before, after):
    template = "<table><tr><th>Field</th><th>Value</th></tr><tr><td>Amount</td><td>100</td></tr></table>"
    old = template.replace("<" + tag + ">", f'<{tag} {attribute}="{before}">', 1)
    new = template.replace("<" + tag + ">", f'<{tag} {attribute}="{after}">', 1)
    assert "reuse" not in incoming_kinds(compare(old, new))
