"""Offline resource decisions through the public CLI boundary."""

import pytest
from PIL import Image, ImageDraw

from tests.test_mdcompare_cli import document, invoke


def resource(resource_id, url, path=None, kind="image", **extra):
    value = {"resource_id": resource_id, "url": url, "kind": kind}
    if path is not None:
        value["path"] = str(path)
    return {**value, **extra}


def test_same_url_replaced_bytes_reports_changed_resource(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("![limit](https://example.invalid/limit.png)")
    old_image = tmp_path / "old.png"
    new_image = tmp_path / "new.png"
    old_image.write_bytes(b"100k")
    new_image.write_bytes(b"300k")
    old = document(
        markdown,
        resources=[resource("limit", "https://example.invalid/limit.png", old_image)],
    )
    new = document(
        markdown,
        resources=[resource("limit", "https://example.invalid/limit.png", new_image)],
    )
    completed, report = invoke(tmp_path, [old], [new])
    row = report["results"][0]
    assert row["resources"]["status"] == "CHANGED"
    assert row["resources"]["processing_required"] == ["limit"]
    assert row["resources"]["items"][0]["status"] == "CHANGED"
    assert row["text"]["status"] == "UNCHANGED"
    assert row["ocr"]["status"] != "UNCHANGED"
    assert completed.returncode in (0, 2)


def test_reference_image_and_html_embed_without_declared_evidence_are_unknown(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text(
        '![screen][picture]\n\n[picture]: https://example.invalid/screen.png\n<video src="https://example.invalid/demo.mp4"></video>'
    )
    completed, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    row = report["results"][0]
    assert completed.returncode == 2
    assert row["resources"]["status"] == "UNKNOWN"
    assert "RESOURCE_REFERENCE_UNMAPPED" in row["resources"]["reason_codes"]


def test_version_qualified_s3_capture_can_prove_same_resource_without_bytes(tmp_path):
    url = "https://evidence.s3.amazonaws.com/screen.png?versionId=immutable-1"
    markdown = tmp_path / "document.md"
    markdown.write_text(f"![screen]({url})")
    proof = {
        "contract": "s3-version-id-v1",
        "captured": True,
        "bucket": "evidence",
        "key": "screen.png",
        "version_id": "immutable-1",
    }
    value = document(markdown, resources=[resource("screen", url, immutable=proof)])
    _, report = invoke(tmp_path, [value], [value])
    row = report["results"][0]
    assert row["resources"]["status"] == "UNCHANGED"
    assert row["resources"]["items"][0]["basis"] == "s3-version-id-v1"
    assert row["ocr"]["status"] != "UNCHANGED"


def test_different_urls_same_bytes_preserve_text_and_reference_changes(tmp_path):
    old_md, new_md = tmp_path / "old.md", tmp_path / "new.md"
    old_md.write_text('![old alt](https://example.invalid/old.png "old title")')
    new_md.write_text('![new alt](https://example.invalid/new.png "new title")')
    data = tmp_path / "image.png"
    data.write_bytes(b"same original resource")
    old = document(
        old_md, resources=[resource("picture", "https://example.invalid/old.png", data)]
    )
    new = document(
        new_md, resources=[resource("picture", "https://example.invalid/new.png", data)]
    )
    _, report = invoke(tmp_path, [old], [new])
    row = report["results"][0]
    assert row["resources"]["status"] == "UNCHANGED"
    assert row["text"]["status"] == "CHANGED"
    assert row["resources"]["references_changed"] is True
    assert row["action"] != "SKIP_VERIFIED"


def test_resource_occurrences_preserve_order_duplicates_and_alt_changes(tmp_path):
    old_md, new_md = tmp_path / "old.md", tmp_path / "new.md"
    old_md.write_text("![a](a.png) ![b](b.png)")
    new_md.write_text("![B](b.png) ![a](a.png) ![a](a.png)")
    data = tmp_path / "image.png"
    data.write_bytes(b"same")
    entries = [resource("a", "a.png", data), resource("b", "b.png", data)]
    _, report = invoke(
        tmp_path,
        [document(old_md, resources=entries)],
        [document(new_md, resources=entries)],
    )
    row = report["results"][0]
    assert row["resources"]["status"] == "UNCHANGED"
    assert row["resources"]["references_changed"] is True
    items = {item["resource_id"]: item for item in row["resources"]["items"]}
    assert items["a"]["occurrences_before"] == [0]
    assert items["a"]["occurrences_after"] == [1, 2]
    assert items["b"]["occurrences_after"] == [0]
    assert row["text"]["status"] == "CHANGED"


def test_mixed_changed_and_missing_resource_keeps_known_work_and_unknown(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("![known](known.png) ![missing](missing.png)")
    old_image, new_image = tmp_path / "old.png", tmp_path / "new.png"
    old_image.write_bytes(b"old")
    new_image.write_bytes(b"new")
    old = [
        resource("known", "known.png", old_image),
        resource("missing", "missing.png", tmp_path / "absent-old.png"),
    ]
    new = [
        resource("known", "known.png", new_image),
        resource("missing", "missing.png", tmp_path / "absent-new.png"),
    ]
    completed, report = invoke(
        tmp_path,
        [document(markdown, resources=old)],
        [document(markdown, resources=new)],
    )
    row = report["results"][0]
    assert completed.returncode == 2
    assert row["action"] == "UNKNOWN"
    assert row["resources"]["status"] == "UNKNOWN"
    assert row["resources"]["processing_required"] == ["known"]
    assert {
        item["resource_id"]: item["status"] for item in row["resources"]["items"]
    } == {"known": "CHANGED", "missing": "UNKNOWN"}


def test_one_digit_change_in_a_long_screenshot_is_a_resource_change(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("![limit](limit.png)")
    paths = []
    for name, number in (("old", "100k"), ("new", "300k")):
        path = tmp_path / f"{name}.png"
        image = Image.new("RGB", (100, 2000), "white")
        ImageDraw.Draw(image).text((5, 1700), number, fill="black")
        image.save(path)
        paths.append(path)
    _, report = invoke(
        tmp_path,
        [document(markdown, resources=[resource("limit", "limit.png", paths[0])])],
        [document(markdown, resources=[resource("limit", "limit.png", paths[1])])],
    )
    assert report["results"][0]["resources"]["status"] == "CHANGED"


@pytest.mark.parametrize(
    "markup",
    [
        '<img src="figure.png">',
        "![figure][pic]\n\n[pic]: figure.png",
        "![pic]\n\n[pic]: figure.png",
        "[guide](manual.pdf)",
        '<object data="guide.pdf"></object>',
        '<audio src="recording.wav"></audio>',
    ],
)
def test_unmapped_dependencies_are_never_resource_free(tmp_path, markup):
    markdown = tmp_path / "document.md"
    markdown.write_text(markup)
    completed, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    assert completed.returncode == 2
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"


def test_navigation_links_and_literal_code_do_not_require_downloads(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text(
        "[navigation](https://example.invalid/help)\n```md\n![example](code.png)\n```\n`![inline](code.png)`"
    )
    completed, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    assert completed.returncode == 0
    assert report["results"][0]["action"] == "SKIP_VERIFIED"


@pytest.mark.parametrize(
    "url,proof",
    [
        (
            "https://evidence.s3.amazonaws.com/screen.png",
            {
                "contract": "s3-version-id-v1",
                "captured": True,
                "bucket": "evidence",
                "key": "screen.png",
                "version_id": "v1",
            },
        ),
        (
            "https://evidence.s3.amazonaws.com/screen.png?versionId=null",
            {
                "contract": "s3-version-id-v1",
                "captured": True,
                "bucket": "evidence",
                "key": "screen.png",
                "version_id": "null",
            },
        ),
        (
            "https://evidence.s3.amazonaws.com/screen.png?versionId=v1",
            {
                "contract": "s3-version-id-v1",
                "captured": True,
                "bucket": "other",
                "key": "screen.png",
                "version_id": "v1",
            },
        ),
        (
            "https://example.invalid/screen.png?versionId=v1",
            {
                "contract": "arbitrary",
                "captured": True,
                "bucket": "evidence",
                "key": "screen.png",
                "version_id": "v1",
            },
        ),
    ],
)
def test_untrusted_or_mutable_version_evidence_is_unknown(tmp_path, url, proof):
    markdown = tmp_path / "document.md"
    markdown.write_text(f"![screen]({url})")
    entry = document(markdown, resources=[resource("screen", url, immutable=proof)])
    completed, report = invoke(tmp_path, [entry], [entry])
    assert completed.returncode == 2
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"


def test_mixed_html_and_markdown_occurrence_order_is_preserved(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text('<img src="first.png"> ![second](second.png)')
    data = tmp_path / "image.png"
    data.write_bytes(b"same")
    entries = [
        resource("first", "first.png", data),
        resource("second", "second.png", data),
    ]
    _, report = invoke(
        tmp_path,
        [document(markdown, resources=entries)],
        [document(markdown, resources=entries)],
    )
    items = {
        item["resource_id"]: item for item in report["results"][0]["resources"]["items"]
    }
    assert items["first"]["occurrences_after"] == [0]
    assert items["second"]["occurrences_after"] == [1]


def test_autolink_download_without_evidence_cannot_skip(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("<https://example.invalid/manual.pdf>")
    completed, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    assert completed.returncode == 2
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"


def test_indented_list_image_is_not_mistaken_for_literal_code(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("- item\n    ![screen](screen.png)")
    completed, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    assert completed.returncode == 2
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"


def test_unrecognized_parenthesized_target_cannot_use_a_partial_mapping(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("![screen](https://example.invalid/a(b).png)")
    image = tmp_path / "image.png"
    image.write_bytes(b"same")
    value = document(
        markdown, resources=[resource("screen", "https://example.invalid/a(b", image)]
    )
    completed, report = invoke(tmp_path, [value], [value])
    assert completed.returncode == 2
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"


def test_angle_bracket_image_target_is_one_image_occurrence(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("![screen](<https://example.invalid/screen.png>)")
    image = tmp_path / "image.png"
    image.write_bytes(b"same")
    value = document(
        markdown,
        resources=[resource("screen", "https://example.invalid/screen.png", image)],
    )
    _, report = invoke(tmp_path, [value], [value])
    resources = report["results"][0]["resources"]
    assert resources["status"] == "UNCHANGED"
    assert resources["items"][0]["occurrences_after"] == [0]


def test_resource_budget_excess_is_unknown_not_truncated_equality(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("no inline images")
    entries = [resource(str(n), f"{n}.png") for n in range(10_001)]
    value = document(markdown, resources=entries)
    completed, report = invoke(tmp_path, [value], [value])
    assert completed.returncode == 2
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"
    assert (
        "RESOURCE_BUDGET_EXCEEDED" in report["results"][0]["resources"]["reason_codes"]
    )


@pytest.mark.parametrize(
    "markup",
    [
        "`![screen](screen.png)``\n",
        "``![screen](screen.png)`\n",
        "````![screen](screen.png)```\n",
        "\\\\![screen](screen.png)\n",
        "\\\\![screen](opaque-target)\n",
    ],
)
def test_unmatched_code_runs_and_even_escapes_do_not_hide_dependencies(
    tmp_path, markup
):
    markdown = tmp_path / "document.md"
    markdown.write_text(markup)
    completed, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    assert completed.returncode == 2
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"
    assert report["results"][0]["action"] == "UNKNOWN"


@pytest.mark.parametrize(
    "markup",
    [
        "\\`![screen](opaque-target)\\`\n",
        "```\n```not-a-close\n```\n![screen](opaque-target)\n",
        "```invalid`info\n![screen](opaque-target)\n",
        "```\nexample\n   ```\n![screen](opaque-target)\n",
        "`unmatched paragraph\n\n![screen](opaque-target)\n`\n",
        '<a title="`">![screen](opaque-target)`',
    ],
)
def test_unproven_or_ended_code_scope_does_not_hide_real_images(tmp_path, markup):
    markdown = tmp_path / "document.md"
    markdown.write_text(markup)
    completed, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    assert completed.returncode == 2
    assert report["results"][0]["resources"]["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "markup",
    [
        "`![screen](opaque-target)`\n",
        "``![screen](opaque-target)``\n",
        "example ````![screen](opaque-target)````\n",
        "`![screen](opaque-target)\\`\n",
        "```md\n![screen](opaque-target)\n```\n",
        "```md\n![screen](opaque-target)\n",
        "~~~~\n![screen](opaque-target)\n~~~\n",
        "\\![screen](opaque-target)\n",
    ],
)
def test_proven_code_and_escaped_image_marker_remain_resource_free(tmp_path, markup):
    markdown = tmp_path / "document.md"
    markdown.write_text(markup)
    completed, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    assert completed.returncode == 0
    assert report["results"][0]["action"] == "SKIP_VERIFIED"
