"""Time values for sync use the fixed UTC+8 business timezone."""

from datetime import datetime, timedelta, timezone
from typing import Any

UTC8 = timezone(timedelta(hours=8))


def parse_timestamp(value: str) -> datetime:
    """Parse an upstream timestamp; timezone-free input defaults to UTC+8."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC8)
    return parsed.astimezone(UTC8)


def timestamp_record(value: str, field: str = "content_updated_at") -> dict[str, Any]:
    if "T" not in value and " " not in value.strip():
        raise ValueError("exact timestamp requires a time component")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return {
        "field": field,
        "value": value,
        # Existing capture/plan schemas call this field utc. Keep the key for
        # compatibility while serializing the same instant with UTC+8 offset.
        "utc": parse_timestamp(value).isoformat(),
        "default_timezone": parsed.tzinfo is None,
    }


def now_iso() -> str:
    return datetime.now(UTC8).isoformat()
