"""Public resource-catalog CLI with real local Markdown."""

import json
import subprocess
import sys


def catalog(tmp_path, body, *, resources=None, provenance=None, historical=False):
    raw = tmp_path / "raw.md"
    raw.write_text(body)
    row = {
        "knowledge_base": "book",
        "document_id": "doc",
        "path": "raw.md",
        "content_kind": "historical_enriched" if historical else "raw",
        "metadata": {"name": "Title", "logical_path": "x", "source_ref": "book:doc"},
        "resources": resources or [],
    }
    if provenance is not None:
        row["provenance"] = provenance
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({"schema_version": "1.0", "documents": [row]}))
    output = tmp_path / "moved/output.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "catalog",
            "--input",
            str(manifest),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result, json.loads(output.read_text())["documents"][
        0
    ] if output.exists() else None


def test_catalog_rejects_declared_and_generated_id_collision(tmp_path):
    result, row = catalog(
        tmp_path,
        "![a](a.png)\n![b](b.png)\n",
        resources=[
            {"resource_id": "b.png", "kind": "image", "url": "a.png"},
        ],
    )
    assert result.returncode == 1
    assert row is None


def test_catalog_does_not_use_local_bytes_for_protocol_relative_remote(tmp_path):
    evidence = tmp_path / "image.png"
    evidence.write_bytes(b"Unrelated local bytes")
    result, row = catalog(tmp_path, f"![remote](//cdn.example{evidence})\n")
    assert result.returncode == 0, result.stderr
    assert "path" not in row["resources"][0]


def test_catalog_does_not_open_legacy_raw_snapshot(tmp_path):
    result, row = catalog(
        tmp_path,
        "Before\nAfter\n",
        historical=True,
        provenance={
            "contract": "mdocr-raw-snapshot-v1",
            "raw_path": "absent.md",
            "blocks": [],
        },
        resources=[
            {
                "resource_id": "original",
                "kind": "image",
                "url": "a.png",
                "path": "a.png",
            }
        ],
    )
    assert result.returncode == 0, result.stderr
    assert row["resources"][0]["path"] == str(tmp_path / "a.png")
    assert row["resources"][0]["resource_id"] == "original"


def test_catalog_builds_manifest_without_fetching_remote_resources(tmp_path):
    raw = tmp_path / "raw.md"
    raw.write_text(
        "![local](image.png)\n![remote](https://invalid.example/a.png)\n[manual](manual.pdf)\n"
    )
    (tmp_path / "image.png").write_bytes(b"original image")
    manifest = tmp_path / "input.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "documents": [
                    {
                        "knowledge_base": "any",
                        "document_id": "doc",
                        "path": "raw.md",
                        "content_kind": "raw",
                        "metadata": {
                            "name": "title",
                            "logical_path": "x",
                            "source_ref": "any:doc",
                        },
                    }
                ],
            }
        )
    )
    output = tmp_path / "output.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "catalog",
            "--input",
            str(manifest),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    row = json.loads(output.read_text())["documents"][0]
    assert row["path"] == str(raw)
    entries = {r["resource_id"]: r for r in row["resources"]}
    assert entries["image.png"]["path"] == str(tmp_path / "image.png")
    assert entries["manual.pdf"]["kind"] == "attachment"
    assert "path" not in entries["https://invalid.example/a.png"]
    assert row["resource_warnings"] == []
    assert raw.read_text().startswith("![local]")


def test_catalog_preserves_declared_evidence_when_output_moves(tmp_path):
    (tmp_path / "raw.md").write_text("![image](https://invalid.example/a.png)\n")
    (tmp_path / "original.png").write_bytes(b"original")
    manifest = tmp_path / "input.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "documents": [
                    {
                        "knowledge_base": "any",
                        "document_id": "doc",
                        "path": "raw.md",
                        "content_kind": "raw",
                        "metadata": {
                            "name": "Title",
                            "logical_path": "x",
                            "source_ref": "any:doc",
                        },
                        "resources": [
                            {
                                "resource_id": "stable-id",
                                "kind": "image",
                                "url": "https://invalid.example/a.png",
                                "path": "original.png",
                            }
                        ],
                        "ocr": {
                            "contract": "mdocr-ocr-2",
                            "images": [
                                {
                                    "resource_id": "stable-id",
                                    "source_path": "original.png",
                                    "artifact": "ocr.md",
                                }
                            ],
                        },
                    }
                ],
            }
        )
    )
    output = tmp_path / "another" / "output.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdcompare",
            "catalog",
            "--input",
            str(manifest),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    row = json.loads(output.read_text())["documents"][0]
    assert row["resources"][0]["resource_id"] == "stable-id"
    assert row["resources"][0]["path"] == str(tmp_path / "original.png")
    image = row["ocr"]["images"][0]
    for key, filename in [
        ("source_path", "original.png"),
        ("artifact", "ocr.md"),
    ]:
        assert image[key] == str(tmp_path / filename)
