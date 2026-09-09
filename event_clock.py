"""Shared calendar time filtering, independent of the event source."""
import datetime as dt
import re
from zoneinfo import ZoneInfo


def future_rows(rows: list[dict], now: dt.datetime) -> list[dict]:
    """Filter known Taipei clock times; retain all-day/unknown-time events."""
    result = []
    for row in rows:
        match = re.fullmatch(r"(\d{2}):(\d{2})", str(row.get("time") or ""))
        if match and row.get("date"):
            try:
                stamp = dt.datetime.combine(row["date"], dt.time(int(match[1]), int(match[2])),
                                            ZoneInfo("Asia/Taipei"))
                if stamp <= now.astimezone(ZoneInfo("Asia/Taipei")):
                    continue
            except (ValueError, TypeError):
                pass  # Unknown timestamp is not proof an event has passed.
        result.append(row)
    return result
