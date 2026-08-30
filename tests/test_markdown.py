"""Unit tests for mdocr.markdown parsing and rewriting."""

from __future__ import annotations

from mdocr.markdown import iter_image_matches, render_image, transform_images

from .conftest import ref_of


def test_parse_inline_with_title():
    ref = ref_of('![Architecture](../x/diagram.png "the title")')
    assert ref.alt == "Architecture"
    assert ref.target == "../x/diagram.png"
    assert ref.title == "the title"
    assert not ref.is_remote
    assert not ref.unsupported


def test_parse_angle_bracket_target_with_space():
    ref = ref_of("![a](<some dir/pic.png>)")
    assert ref.target == "some dir/pic.png"
    assert ref.title is None


def test_parse_remote_url():
    ref = ref_of("![a](https://example.com/a.png)")
    assert ref.is_remote
    assert not ref.unsupported


def test_parse_fragment_stripped():
    ref = ref_of("![a](pic.png#frag)")
    assert ref.target == "pic.png"


def test_parse_unsupported_forms():
    assert ref_of("![a](/abs/pic.png)").unsupported
    assert ref_of("![a](//example.com/pic.png)").unsupported
    assert ref_of("![a](data:image/png;base64,AAAA)").unsupported
    assert ref_of("![a](ftp://example.com/pic.png)").unsupported


def test_parse_remote_url_encoding_preserved():
    ref = ref_of("![a](https://host.example/a%20b.png)")
    assert ref.target == "https://host.example/a%20b.png"
    assert ref.is_remote


def test_parse_local_percent_decoded():
    ref = ref_of("![a](my%20pic.png)")
    assert ref.target == "my pic.png"


def test_render_image_preserves_title_and_alt():
    out = render_image("Alt", "assets/in/pic.jpg", "the title")
    assert out == '![Alt](assets/in/pic.jpg "the title")'
    assert render_image("Alt", "assets/in/pic.jpg", None) == "![Alt](assets/in/pic.jpg)"


def test_transform_images_isolates_callback_failure():
    text = "![a](one.png) ![b](two.png)"

    def transform(ref):
        if ref.target == "one.png":
            raise RuntimeError("boom")
        return "OK"

    new, _warnings, errors = transform_images(text, transform)
    assert "![a](one.png)" in new  # failed embed kept
    assert "OK" in new  # later images still processed
    assert len(errors) == 1


def test_transform_images_keeps_original_when_none():
    text = "before ![a](pic.png) after"
    new, warnings, errors = transform_images(text, lambda ref: None)
    assert new == text
    assert warnings == []
    assert errors == []


def test_transform_images_replaces_and_reports_html_ref_forms():
    text = '![a](pic.png)\n\n<img src="x.jpg">\n\n![b][ref]'
    new, warnings, errors = transform_images(
        text, lambda ref: "REPLACED", errors=[], warnings=[]
    )
    assert "REPLACED" in new
    assert "![a]" not in new
    assert len(warnings) == 2
    assert any("HTML <img>" in w for w in warnings)
    assert any("reference-style" in w for w in warnings)
    assert errors == []


def test_iter_image_matches_multiple():
    text = "![a](one.png) middle ![b](two.png 't')"
    refs = [ref for _, ref in iter_image_matches(text)]
    assert [r.target for r in refs] == ["one.png", "two.png"]
