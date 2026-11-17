"""Product routes and durable evidence through the independent public CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def doc(path, identifier="1", **kwargs):
    return {
        "knowledge_base": "book",
        "document_id": identifier,
        "content_kind": "raw",
        "path": str(path),
        "metadata": {
            "name": "Rules",
            "logical_path": "",
            "source_ref": "book:" + identifier,
        },
        "resources": [],
        **kwargs,
    }


def run(
    tmp, olddocs, newdocs, extra=()
) -> tuple[subprocess.CompletedProcess[str], Any, Any]:
    paths = []
    for name, docs in (("old", olddocs), ("new", newdocs)):
        path = tmp / (name + ".json")
        path.write_text(json.dumps({"schema_version": "1.0", "documents": docs}))
        paths.append(path)
    out, diff = tmp / "report.json", tmp / "diff.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "semantic",
            "--baseline",
            str(paths[0]),
            "--incoming",
            str(paths[1]),
            "--output",
            str(out),
            "--diff-output",
            str(diff),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src")),
    )
    diff_payload = json.loads(diff.read_text()) if diff.exists() else None
    if diff_payload is not None:
        diff_payload["documents"] = [
            json.loads((diff.parent / record["evidence"]).read_text())
            for record in diff_payload["documents"]
        ]
    return result, json.loads(out.read_text()) if out.exists() else None, diff_payload


def pair(tmp, old, new, extra=()):
    a, b = tmp / "before.md", tmp / "after.md"
    a.write_text(old, encoding="utf-8")
    b.write_text(new, encoding="utf-8")
    return run(tmp, [doc(a)], [doc(b)], extra)


def test_new_document_compiles_despite_unrelated_unreadable_baseline(tmp_path):
    incoming = tmp_path / "body.md"
    incoming.write_text("# Rules\n\nRefunds require review.\n")
    result, report, diff = run(
        tmp_path, [doc(tmp_path / "missing.md", "99")], [doc(incoming)]
    )
    assert result.returncode == 0, result.stderr
    assert report["schema_version"] == "semantic-1.0"
    assert report["scope"] == "authored_body"
    assert report["results"][0]["semantic_status"] == "NEW"
    assert report["results"][0]["action"] == "COMPILE"
    assert diff["schema_version"] == "mdcompare-semantic-diff-1"
    assert "Refunds require review." in json.dumps(diff["documents"][0]["new_units"])
    assert report["summary"]["baseline_only_advisory"] == [
        {"knowledge_base": "book", "document_id": "99"}
    ]


@pytest.mark.parametrize(
    "before,after",
    [
        ("付款需要审批。\n", "付款需要审批。\r\n"),
        ("需要**人工审批**。\n", "需要人工审批。\n"),
        ("Payment requires review.\n", "Payment   requires review.\n"),
        ("条款\n\n![A](https://a/x.png)\n", "条款\n\n![B](https://b/y.svg)\n"),
        (
            "条款\n<!-- mdocr:ocr-begin -->\n旧OCR\n<!-- mdocr:ocr-end -->\n",
            "条款\n<!-- mdocr:ocr-begin -->\n新OCR\n<!-- mdocr:ocr-end -->\n",
        ),
    ],
)
def test_proven_equivalence_and_advisory_image_ocr_changes_skip(
    tmp_path, before, after
):
    result, report, diff = pair(tmp_path, before, after)
    assert result.returncode == 0, result.stderr
    assert report["results"][0]["semantic_status"] == "EQUIVALENT"
    assert report["results"][0]["action"] == "SKIP"
    assert "旧OCR" not in json.dumps(diff, ensure_ascii=False)
    assert "新OCR" not in json.dumps(diff, ensure_ascii=False)


@pytest.mark.parametrize(
    "before,after",
    [
        ("第一条规则。\n\n删除规则。\n", "第一条规则。\n"),
        ("删除全部规则。\n", ""),
        ("金额超过100元审批。\n", "金额超过200元审批。\n"),
        ("超过限额需要审批。\n", "未超过限额需要审批。\n"),
        ("运营负责核对。\n", "财务负责核对。\n"),
        ("# A\n付款\n# B\n退款\n", "# A\n退款\n# B\n付款\n"),
        ("|币种|额度|\n|---|---|\n|USD|100|\n", "|币种|额度|\n|---|---|\n|USD|200|\n"),
        ("```py\nx = 1\n```\n", "```py\nx = 2\n```\n"),
        ("规则A。\n\n规则B。\n", "规则B。\n\n规则A。\n"),
        ("规则A。\n\n规则A。\n", "规则A。\n"),
    ],
)
def test_meaningful_edits_deletions_order_and_context_compile(tmp_path, before, after):
    result, report, _ = pair(tmp_path, before, after)
    assert result.returncode == 0, result.stderr
    assert report["results"][0]["semantic_status"] == "CHANGED"
    assert report["results"][0]["action"] == "COMPILE"


def test_alignment_budget_exhaustion_compiles_with_full_evidence(tmp_path):
    before, after = "旧规则。" * 6000, "新规则。" * 6000
    result, report, diff = pair(tmp_path, before, after, ("--max-diff-work", "0"))
    assert result.returncode == 0, result.stderr
    assert report["results"][0]["semantic_status"] == "UNCERTAIN"
    assert report["results"][0]["action"] == "COMPILE"
    assert diff["documents"][0]["old_units"][0]["text"] == before
    assert diff["documents"][0]["new_units"][0]["text"] == after


def test_unclosed_scope_is_per_document_defer_and_new_document_still_compiles(tmp_path):
    old, new, added = tmp_path / "old.md", tmp_path / "new.md", tmp_path / "added.md"
    old.write_text("规则\n> **图片转写：**\n> 未结束OCR\n")
    new.write_text("新规则\n")
    added.write_text("另一篇正文\n")
    result, report, diff = run(tmp_path, [doc(old)], [doc(new), doc(added, "2")])
    assert result.returncode == 2
    assert [r["action"] for r in report["results"]] == ["DEFER", "COMPILE"]
    assert report["results"][0]["reason_codes"] == ["BODY_SCOPE_BOUNDARY_UNCLOSED"]
    assert len(diff["documents"]) == 2


def recovery(tmp, source, insertions):
    path = tmp / "recovery.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "mdcompare-body-recovery-1",
                "documents": [
                    {
                        "knowledge_base": "book",
                        "document_id": "1",
                        "side": "baseline",
                        "source_copy": str(source),
                        "insertions": insertions,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_explicit_marker_repair_replays_and_retains_original_coordinates(tmp_path):
    original = "规则\n\n> **图片转写：**\n> OCR噪声\n业务正文\n"
    old, new, witness = (
        tmp_path / "old.md",
        tmp_path / "new.md",
        tmp_path / "witness.md",
    )
    old.write_text(original)
    witness.write_text(original)
    new.write_text("规则\n\n业务正文\n")
    offset = original.encode().index("业务正文".encode())
    path = recovery(
        tmp_path, witness, [{"byte_offset": offset, "text": "> 转写说明：\n"}]
    )
    result, report, diff = run(
        tmp_path, [doc(old)], [doc(new)], ("--recovery", str(path))
    )
    assert result.returncode == 0, result.stderr
    assert report["results"][0]["action"] == "SKIP"
    units = diff["documents"][0]["old_units"]
    last = next(u for u in units if "业务正文" in u["text"])
    assert last["start_line"] == 5
    assert last["projected_start_line"] == 6
    assert diff["documents"][0]["recovery"][0]["source_line"] == 5
    assert "OCR噪声" not in json.dumps(units, ensure_ascii=False)
    assert old.read_text() == original


@pytest.mark.parametrize(
    "kind", ["drift", "arbitrary", "negative", "split_utf8", "beyond", "empty"]
)
def test_invalid_recovery_defers_only_the_affected_document(tmp_path, kind):
    original = "规则\n"
    old, new, witness = (
        tmp_path / "old.md",
        tmp_path / "new.md",
        tmp_path / "witness.md",
    )
    old.write_text(original)
    new.write_text(original)
    witness.write_text("不同副本\n" if kind == "drift" else original)
    offset = {"negative": -1, "split_utf8": 1, "beyond": 999}.get(
        kind, len(original.encode())
    )
    text = (
        "任意替换正文\n"
        if kind == "arbitrary"
        else ""
        if kind == "empty"
        else "> 转写说明：\n"
    )
    path = recovery(tmp_path, witness, [{"byte_offset": offset, "text": text}])
    result, report, _ = run(tmp_path, [doc(old)], [doc(new)], ("--recovery", str(path)))
    assert result.returncode == 2, result.stderr
    assert report["results"][0]["action"] == "DEFER"


def test_invalid_recovery_schema_is_error_without_traceback(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("规则\n")
    path = tmp_path / "recovery.json"
    path.write_text('{"schema_version":"wrong", "documents":[]}')
    result, report, _ = run(tmp_path, [], [doc(body)], ("--recovery", str(path)))
    assert result.returncode == 1
    assert "RECOVERY_MANIFEST_INVALID" in result.stderr
    assert "Traceback" not in result.stderr
    assert report is None


def test_output_cannot_overwrite_source_or_repair_manifest(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("规则\n")
    result, _, _ = run(tmp_path, [], [doc(body)], ("--diff-output", str(body)))
    assert result.returncode == 1
    assert "REPORT_INPUT_COLLISION" in result.stderr
    assert body.read_text() == "规则\n"


def test_semantic_help_contains_installed_contract_and_no_authentication(tmp_path):
    for full in ([], ["--full"]):
        result = subprocess.run(
            [sys.executable, "-m", "mdcompare", "semantic", "--help", *full],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        for field in (
            "semantic-1.0",
            "COMPILE",
            "SKIP",
            "DEFER",
            "mdcompare-body-recovery-1",
        ):
            assert field in result.stdout
        if full:
            assert "byte_offset" in result.stdout
            assert "original" in result.stdout


def test_index_references_complete_private_evidence_without_duplicate_operation_text(
    tmp_path,
):
    result, _, detail = pair(
        tmp_path, "甲规则。\n\n乙规则。\n", "乙规则。\n\n甲规则。\n"
    )
    assert result.returncode == 0
    index = json.loads((tmp_path / "diff.json").read_text())
    reference = tmp_path / index["documents"][0]["evidence"]
    assert reference.parent.name == index["evidence_directory"]
    assert reference.stat().st_mode & 0o777 == 0o600
    assert reference.parent.stat().st_mode & 0o777 == 0o700
    changes = detail["documents"][0]["changes"]
    assert all("old_ref" in change and "new_ref" in change for change in changes)
    assert any(change["operation"] == "move" for change in changes)
    assert not any("old" in change or "new" in change for change in changes)


def test_duplicate_occurrence_removal_has_a_delete_operation(tmp_path):
    result, _, diff = pair(tmp_path, "重复规则。\n\n重复规则。\n", "重复规则。\n")
    assert result.returncode == 0
    assert len(diff["documents"][0]["old_units"]) == 2
    assert (
        sum(
            change["operation"] == "delete"
            for change in diff["documents"][0]["changes"]
        )
        == 1
    )
