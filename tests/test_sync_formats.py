import json

import pytest

from scripts import sync_formats


def test_sheet_uses_skylark_markdown_body(monkeypatch):
    calls = []

    def fake_call(tool, args):
        calls.append((tool, args))
        return {
            "id": 1,
            "title": "Sheet",
            "body": "## Sheet1\n\n| A |\n| --- |\n| value |",
        }

    monkeypatch.setattr(sync_formats, "call_tool", fake_call)

    body, detail = sync_formats.export_doc(1, "lakesheet")

    assert "## Sheet1" in body
    assert "| value |" in body
    assert detail["title"] == "Sheet"
    assert calls == [("skylark_doc_detail", {"doc_id": 1, "format": "md"})]


def test_table_exports_pages_options_multiline_and_images(monkeypatch):
    record_offsets = []

    def fake_call(tool, args):
        assert tool == "skylark_table_call"
        action = args["action"]
        if action == "table.list":
            return {"tables": [{"sheet_id": "sheet-1", "title": "UAT cases"}]}
        if action == "table.get":
            return {
                "table": {
                    "sheet": [
                        {
                            "columns": [
                                {"id": "title", "name": "Title", "type": "text"},
                                {
                                    "id": "status",
                                    "name": "Status",
                                    "type": "select",
                                    "options": [{"id": "pass", "value": "Pass"}],
                                },
                                {
                                    "id": "attachments",
                                    "name": "Evidence",
                                    "type": "image",
                                },
                            ]
                        }
                    ]
                }
            }
        if action == "record.list":
            offset = args["payload"]["offset"]
            record_offsets.append(offset)
            values = [
                {
                    "uuid": "row-1",
                    "data": json.dumps(
                        {
                            "title": {"value": "first | case\nnext line"},
                            "status": {"value": "pass"},
                        }
                    ),
                },
                {
                    "uuid": "row-2",
                    "data": json.dumps(
                        {
                            "title": {"value": "second"},
                            "attachments": {
                                "value": [
                                    {
                                        "name": "proof.png",
                                        "src": "https://intranetproxy.alipay.com/skylark/lark/image.png",
                                    }
                                ]
                            },
                        }
                    ),
                },
            ]
            record = values[offset // 200]
            return {
                "records": [record],
                "has_more": offset == 0,
                "offset": offset,
                "limit": 200,
            }
        raise AssertionError(action)

    monkeypatch.setattr(sync_formats, "call_tool", fake_call)

    body, detail = sync_formats.export_doc(123, "laketable")

    assert record_offsets == [0, 200]
    assert detail == {"id": 123, "format": "laketable"}
    assert body.startswith("## UAT cases\n\n| Title | Status | Evidence |")
    assert "first \\| case<br>next line" in body
    assert "| Pass |" in body
    assert "![proof.png](https://yuque.antfin.com/images/lark/image.png)" in body
    assert "second" in body


def test_table_rejects_stalled_record_pagination(monkeypatch):
    def fake_call(tool, args):
        if args["action"] == "table.list":
            return {"tables": [{"sheet_id": "sheet-1", "title": "Table"}]}
        if args["action"] == "table.get":
            return {"table": {"sheet": [{"columns": [{"id": "a", "name": "A"}]}]}}
        return {"records": [], "has_more": True}

    monkeypatch.setattr(sync_formats, "call_tool", fake_call)

    with pytest.raises(sync_formats.SkylarkError, match="stopped advancing"):
        sync_formats.export_doc(123, "laketable")
