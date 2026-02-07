from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def now_iso(tz_name: str) -> str:
    """ISO timestamp with timezone offset, e.g. 2026-01-31T07:02:03-0500."""
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S%z")
