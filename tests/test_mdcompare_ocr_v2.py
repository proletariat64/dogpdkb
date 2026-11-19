"""Public mdocr-ocr-2 evidence fixtures."""

from pathlib import Path

from mdcompare.knowledge_units import _declared_generated_spans
from mdcompare.model import Document
from mdcompare.ocr import _records


def test_v2_records_require_unique_resource_ids():
    doc = Document(
        "book",
        "1",
        "raw",
        Path("/tmp/v2.md"),
        {},
        [],
        Path("/tmp"),
        {
            "contract": "mdocr-ocr-2",
            "configuration": {},
            "images": [
                {"resource_id": "x", "status": "success"},
                {"resource_id": "x", "status": "success"},
            ],
        },
    )
    assert _records(doc) is None


def test_v2_knowledge_block_boundary_is_recognized():
    text = "before\n<!-- mdocr:ocr-begin v=2 id=x -->\ntext\n<!-- mdocr:ocr-end -->\nafter\n"
    spans = _declared_generated_spans(
        text,
        {
            "version": "generated-regions-v1",
            "regions": [{"kind": "mdocr_ocr", "start_line": 2, "end_line": 4}],
        },
    )
    assert spans == [(7, 68)]
