"""Public resource-catalog CLI with real local Markdown."""

import json
import subprocess
import sys


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
                            "contract": "prepared-jpeg-ocr-v1",
                            "images": [
                                {
                                    "resource_id": "stable-id",
                                    "source_path": "original.png",
                                    "prepared_image": "prepared.jpg",
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
        ("prepared_image", "prepared.jpg"),
        ("artifact", "ocr.md"),
    ]:
        assert image[key] == str(tmp_path / filename)
