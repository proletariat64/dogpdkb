"""OCR reuse is verified through independently invoked offline CLI."""

from copy import deepcopy

import pytest
from PIL import Image

from tests.test_mdcompare_cli import document, invoke
from tests.test_mdcompare_resources import resource


def captured_pair(tmp_path):
    markdown = tmp_path / "document.md"
    markdown.write_text("![limit](limit.png)")
    original = tmp_path / "original.png"
    Image.new("RGB", (20, 20), "white").save(original)
    prepared = tmp_path / "prepared.jpg"
    Image.new("RGB", (20, 20), "white").save(prepared)
    artifact = tmp_path / "ocr.md"
    artifact.write_text(
        '<!-- mdocr:ocr-begin source="prepared.jpg" -->\n\n'
        "Limit 100k\n\n> Original image: [prepared.jpg](prepared.jpg)\n\n"
        "<!-- mdocr:ocr-end -->"
    )
    record = {
        "resource_id": "limit",
        "source_path": str(original),
        "prepared_image": str(prepared),
        "status": "success",
        "artifact": str(artifact),
    }
    evidence = {
        "contract": "prepared-jpeg-ocr-v1",
        "preprocessing": {"name": "mdocr", "version": "1"},
        "configuration": {"engine": "offline-fixture", "model": "v1"},
        "images": [record],
    }
    baseline = document(
        markdown,
        resources=[resource("limit", "limit.png", original)],
        ocr=evidence,
    )
    incoming = deepcopy(baseline)
    incoming["ocr"]["images"][0].pop("status")
    incoming["ocr"]["images"][0].pop("artifact")
    return baseline, incoming


def test_successful_compatible_ocr_can_skip_without_running_ocr(tmp_path):
    baseline, incoming = captured_pair(tmp_path)
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 0
    assert row["action"] == "SKIP_VERIFIED"
    assert row["ocr"]["status"] == "UNCHANGED"
    assert row["ocr"]["reusable"] == ["limit"]


def test_missing_success_record_requires_processing_not_unknown(tmp_path):
    baseline, incoming = captured_pair(tmp_path)
    baseline.pop("ocr")
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 0
    assert row["action"] == "PROCESS_REQUIRED"
    assert row["ocr"]["processing_required"] == ["limit"]
    assert "OCR_SUCCESS_RECORD_MISSING" in row["reason_codes"]


def test_changed_resource_with_unchanged_missing_ocr_still_processes_all_required(
    tmp_path,
):
    baseline, incoming = captured_pair(tmp_path)
    baseline.pop("ocr")
    second = tmp_path / "second.png"
    second.write_bytes(b"second image")
    for doc in (baseline, incoming):
        doc["resources"].append(resource("second", "second.png", second))
    old_md, new_md = tmp_path / "old.md", tmp_path / "new.md"
    old_md.write_text("![limit](limit.png) ![second](second.png)")
    new_md.write_text("![limit](limit.png) ![second](second.png)")
    baseline["path"], incoming["path"] = str(old_md), str(new_md)
    changed = tmp_path / "changed.png"
    changed.write_bytes(b"changed original")
    incoming["resources"][1]["path"] = str(changed)
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 0
    assert row["action"] == "PROCESS_CHANGED_RESOURCES"
    assert row["resources"]["processing_required"] == ["second"]
    assert row["ocr"]["processing_required"] == ["limit", "second"]


def test_unmapped_image_does_not_claim_ocr_is_not_applicable(tmp_path):
    markdown = tmp_path / "raw.md"
    markdown.write_text("![unknown](unknown.png)")
    result, report = invoke(tmp_path, [document(markdown)], [document(markdown)])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 2
    assert row["ocr"]["status"] == "UNKNOWN"
    assert row["action"] == "UNKNOWN"


@pytest.mark.parametrize(
    "change,action",
    [
        ("metadata", "UPDATE_METADATA_ONLY"),
        ("text", "UPDATE_TEXT_REUSE_OCR"),
        ("resource", "PROCESS_CHANGED_RESOURCES"),
    ],
)
def test_verified_ocr_keeps_independent_changes(tmp_path, change, action):
    baseline, incoming = captured_pair(tmp_path)
    if change == "metadata":
        incoming["metadata"]["name"] = "Renamed"
        incoming["metadata"]["logical_path"] = "moved"
    elif change == "text":
        incoming_path = tmp_path / "new.md"
        incoming_path.write_text("New rule 300k\n![limit](limit.png)")
        incoming["path"] = str(incoming_path)
    else:
        changed = tmp_path / "changed.png"
        changed.write_bytes(b"different original bytes")
        incoming["resources"][0]["path"] = str(changed)
    result, report = invoke(tmp_path, [baseline], [incoming], "--include-evidence")
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 0
    assert row["action"] == action
    if change == "text":
        assert row["text"]["differences"]
        assert row["ocr"]["reusable"] == ["limit"]
    elif change == "resource":
        assert row["resources"]["processing_required"] == ["limit"]
        assert row["ocr"]["reusable"] == []


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("prepared", "OCR_PREPARED_IMAGE_CHANGED"),
        ("invalid-jpeg", "OCR_PREPARED_IMAGE_INVALID"),
        ("preprocessing", "OCR_CONFIGURATION_INCOMPATIBLE"),
        ("configuration", "OCR_CONFIGURATION_INCOMPATIBLE"),
        ("missing-artifact", "OCR_ARTIFACT_UNAVAILABLE"),
        ("empty-artifact", "OCR_ARTIFACT_EMPTY"),
        ("failed", "OCR_SUCCESS_RECORD_UNSUCCESSFUL"),
    ],
)
def test_known_unusable_ocr_requires_processing(tmp_path, mutation, reason):
    baseline, incoming = captured_pair(tmp_path)
    if mutation == "prepared":
        prepared = tmp_path / "other.jpg"
        Image.new("RGB", (20, 20), "black").save(prepared)
        incoming["ocr"]["images"][0]["prepared_image"] = str(prepared)
    elif mutation == "invalid-jpeg":
        prepared = tmp_path / "fake.jpg"
        Image.new("RGB", (20, 20), "white").save(prepared, format="PNG")
        incoming["ocr"]["images"][0]["prepared_image"] = str(prepared)
    elif mutation in ("preprocessing", "configuration"):
        incoming["ocr"][mutation]["version"] = "incompatible"
    elif mutation == "missing-artifact":
        (tmp_path / "ocr.md").unlink()
    elif mutation == "empty-artifact":
        p = tmp_path / "ocr.md"
        p.write_text(p.read_text().replace("Limit 100k", ""))
    else:
        baseline["ocr"]["images"][0]["status"] = "failed"
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 0
    assert row["action"] == "PROCESS_REQUIRED"
    assert reason in row["reason_codes"]


@pytest.mark.parametrize("before,after", [(True, 1), (False, 0), (1, 1.0)])
def test_configuration_types_cannot_authorize_reuse(tmp_path, before, after):
    baseline, incoming = captured_pair(tmp_path)
    baseline["ocr"]["configuration"]["setting"] = before
    incoming["ocr"]["configuration"]["setting"] = after
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    assert result.returncode == 0
    assert report["results"][0]["action"] == "PROCESS_REQUIRED"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing-source", "OCR_SOURCE_MAPPING_INCOMPLETE"),
        ("wrong-source", "OCR_SOURCE_MAPPING_MISMATCH"),
        ("wrong-block-source", "OCR_ARTIFACT_MAPPING_MISMATCH"),
        ("unmarked-artifact", "OCR_ARTIFACT_MAPPING_INVALID"),
        ("missing-config", "OCR_CONFIGURATION_UNVERIFIED"),
        ("duplicate-record", "OCR_PROVENANCE_INCOMPLETE"),
        ("missing-prepared-file", "INPUT_READ_FAILED"),
    ],
)
def test_unverifiable_ocr_stays_unknown(tmp_path, mutation, reason):
    baseline, incoming = captured_pair(tmp_path)
    record = baseline["ocr"]["images"][0]
    if mutation == "missing-source":
        record.pop("source_path")
    elif mutation == "wrong-source":
        other = tmp_path / "other.png"
        other.write_bytes(b"not the captured original")
        record["source_path"] = str(other)
    elif mutation == "wrong-block-source":
        p = tmp_path / "ocr.md"
        p.write_text(p.read_text().replace("prepared.jpg", "unrelated.jpg"))
    elif mutation == "unmarked-artifact":
        (tmp_path / "ocr.md").write_text("Limit 100k")
    elif mutation == "missing-config":
        incoming["ocr"].pop("configuration")
    elif mutation == "duplicate-record":
        baseline["ocr"]["images"].append(deepcopy(record))
    else:
        (tmp_path / "prepared.jpg").unlink()
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 2
    assert row["action"] == "UNKNOWN"
    assert reason in row["reason_codes"]


def test_no_images_does_not_require_a_nonexistent_ocr_record(tmp_path):
    markdown = tmp_path / "raw.md"
    markdown.write_text("No images")
    result, report = invoke(tmp_path, [], [document(markdown)])
    assert report is not None
    assert result.returncode == 0
    assert report["results"][0]["action"] == "PROCESS_REQUIRED"
    assert report["results"][0]["ocr"]["status"] == "NOT_APPLICABLE"


def test_known_attachment_does_not_need_image_ocr(tmp_path):
    markdown = tmp_path / "raw.md"
    markdown.write_text("[guide](guide.pdf)")
    attachment = tmp_path / "guide.pdf"
    attachment.write_bytes(b"captured attachment")
    doc = document(
        markdown,
        resources=[resource("guide", "guide.pdf", attachment, kind="attachment")],
    )
    result, report = invoke(tmp_path, [doc], [doc])
    assert report is not None
    assert result.returncode == 0
    assert report["results"][0]["action"] == "SKIP_VERIFIED"
    assert report["results"][0]["ocr"]["status"] == "NOT_APPLICABLE"


def test_prepared_image_validation_budget_stays_unknown(tmp_path):
    baseline, incoming = captured_pair(tmp_path)
    path = tmp_path / "large.jpg"
    data = bytearray((tmp_path / "prepared.jpg").read_bytes())
    frame = data.index(b"\xff\xc0")
    data[frame + 5 : frame + 9] = b"\x17\x70\x17\x70"  # 6000 x 6000 header
    path.write_bytes(data)
    incoming["ocr"]["images"][0]["prepared_image"] = str(path)
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 2
    assert row["action"] == "UNKNOWN"
    assert "OCR_PREPARED_IMAGE_BUDGET_EXCEEDED" in row["reason_codes"]


def test_mixed_unknown_keeps_verified_per_image_reuse(tmp_path):
    baseline, incoming = captured_pair(tmp_path)
    for doc in (baseline, incoming):
        second = deepcopy(doc["resources"][0])
        second.update(resource_id="second", url="second.png")
        doc["resources"].append(second)
        image = deepcopy(doc["ocr"]["images"][0])
        image["resource_id"] = "second"
        doc["ocr"]["images"].append(image)
    (tmp_path / "document.md").write_text("![limit](limit.png) ![second](second.png)")
    baseline["ocr"]["images"][1].pop("source_path")
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 2
    assert row["action"] == "UNKNOWN"
    assert row["ocr"]["reusable"] == ["limit"]
    assert {item["resource_id"]: item["status"] for item in row["ocr"]["items"]} == {
        "limit": "UNCHANGED",
        "second": "UNKNOWN",
    }


def test_new_image_is_processing_work_not_a_duplicate(tmp_path):
    _, incoming = captured_pair(tmp_path)
    result, report = invoke(tmp_path, [], [incoming])
    assert report is not None
    row = report["results"][0]
    assert result.returncode == 0
    assert row["action"] == "PROCESS_REQUIRED"
    assert row["resources"]["status"] == "NEW"
    assert row["ocr"]["processing_required"] == ["limit"]


def test_cyclic_artifact_image_reference_is_private_unknown(tmp_path):
    baseline, incoming = captured_pair(tmp_path)
    (tmp_path / "loop-a").symlink_to("loop-b")
    (tmp_path / "loop-b").symlink_to("loop-a")
    artifact = tmp_path / "ocr.md"
    artifact.write_text(artifact.read_text().replace("prepared.jpg", "loop-a"))
    result, report = invoke(tmp_path, [baseline], [incoming])
    assert report is not None
    assert result.returncode == 2
    assert report["results"][0]["action"] == "UNKNOWN"
    assert "OCR_ARTIFACT_MAPPING_UNVERIFIED" in report["results"][0]["reason_codes"]
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize("obsolete_records", ["malformed", "oversized"])
def test_changed_image_does_not_validate_obsolete_ocr_inventory(
    tmp_path, obsolete_records
):
    baseline, incoming = captured_pair(tmp_path)
    if obsolete_records == "malformed":
        baseline["ocr"]["images"] = [None]
    else:
        baseline["ocr"]["images"] *= 10_001
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"changed original bytes")
    incoming["resources"][0]["path"] = str(replacement)
    completed, report = invoke(tmp_path, [baseline], [incoming])
    assert completed.returncode == 0
    assert report is not None
    row = report["results"][0]
    assert row["action"] == "PROCESS_CHANGED_RESOURCES"
    assert row["ocr"]["processing_required"] == ["limit"]
    assert row["ocr"]["reusable"] == []
