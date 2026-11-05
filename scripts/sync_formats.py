"""Export supported Yuque document formats as Markdown."""

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from get_sync_updates import SkylarkError, call_tool

from mdocr.config import current_config

TABLE_FORMATS = {"laketable", "table"}
TABLE_PAGE_SIZE = 200


def rewrite_image_urls(body: str) -> str:
    for old, new in current_config().image_url_rewrites:
        body = body.replace(old, new)
    return body


def _cell_text(value: Any, field: dict[str, Any]) -> str:
    if isinstance(value, dict) and "value" in value:
        return _cell_text(value["value"], field)
    if value is None:
        return ""
    if isinstance(value, list):
        return "<br>".join(part for item in value if (part := _cell_text(item, field)))
    if isinstance(value, dict):
        if value.get("src"):
            name = str(value.get("name") or "attachment")
            return rewrite_image_urls(f"![{name}]({value['src']})")
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if field.get("type") in {"select", "multiSelect"}:
        options = {
            str(option.get("id")): str(option.get("value") or "")
            for option in field.get("options") or []
        }
        value = options.get(str(value), value)
    if isinstance(value, bool):
        value = "true" if value else "false"
    text = rewrite_image_urls(str(value))
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
        .replace("|", "\\|")
    )


def _record_values(record: dict[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SkylarkError(f"invalid table record data: {exc}") from exc
    if not isinstance(data, dict):
        raise SkylarkError("invalid table record payload")
    return data


def _table_records(doc_id: int, sheet_id: str) -> list[dict[str, Any]]:
    records = []
    seen = set()
    offset = 0
    while True:
        payload = call_tool(
            "skylark_table_call",
            {
                "action": "record.list",
                "doc_id": doc_id,
                "sheet_id": sheet_id,
                "payload": {"limit": TABLE_PAGE_SIZE, "offset": offset},
            },
        )
        page = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(page, list):
            raise SkylarkError(f"doc {doc_id}: invalid record.list payload")
        for record in page:
            record_id = record.get("uuid") or record.get("id")
            if record_id and record_id in seen:
                raise SkylarkError(f"doc {doc_id}: duplicate table record {record_id}")
            if record_id:
                seen.add(record_id)
            records.append(record)
        if not payload.get("has_more"):
            return records
        if not page:
            raise SkylarkError(
                f"doc {doc_id}: table record pagination stopped advancing"
            )
        offset += TABLE_PAGE_SIZE


def export_table(doc_id: int) -> tuple[str, dict[str, Any]]:
    listing = call_tool(
        "skylark_table_call", {"action": "table.list", "doc_id": doc_id}
    )
    tables = listing.get("tables") if isinstance(listing, dict) else listing
    if not isinstance(tables, list):
        raise SkylarkError(f"doc {doc_id}: invalid table.list payload")
    sections = []
    for index, table_info in enumerate(tables, 1):
        sheet_id = table_info.get("sheet_id") or table_info.get("id")
        if not sheet_id:
            raise SkylarkError(f"doc {doc_id}: table is missing sheet_id")
        detail = call_tool(
            "skylark_table_call",
            {"action": "table.get", "doc_id": doc_id, "sheet_id": sheet_id},
        )
        sheets = (
            (detail.get("table") or {}).get("sheet")
            if isinstance(detail, dict)
            else None
        )
        if not isinstance(sheets, list) or not sheets:
            raise SkylarkError(f"doc {doc_id}: invalid table.get payload")
        fields = sheets[0].get("columns") or []
        title = str(table_info.get("title") or f"Table {index}")
        lines = [f"## {title}", ""]
        if fields:
            lines.append(
                "| "
                + " | ".join(_cell_text(field.get("name"), {}) for field in fields)
                + " |"
            )
            lines.append("| " + " | ".join("---" for _ in fields) + " |")
            for record in _table_records(doc_id, sheet_id):
                values = _record_values(record)
                lines.append(
                    "| "
                    + " | ".join(
                        _cell_text(values.get(field.get("id")), field)
                        for field in fields
                    )
                    + " |"
                )
        else:
            lines.append("No columns.")
        sections.append("\n".join(lines))
    return "\n\n".join(sections), {"id": doc_id, "format": "laketable"}


def export_doc(
    doc_id: int, doc_format: str | None = None
) -> tuple[str, dict[str, Any]]:
    if str(doc_format or "").lower() in TABLE_FORMATS:
        return export_table(doc_id)
    data = call_tool("skylark_doc_detail", {"doc_id": doc_id, "format": "md"})
    if not isinstance(data, dict) or not isinstance(data.get("body"), str):
        raise SkylarkError(f"doc {doc_id}: invalid detail payload")
    return rewrite_image_urls(data["body"]), data
