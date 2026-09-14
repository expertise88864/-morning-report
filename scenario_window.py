"""Calendar-backed display windows; model-written dates never establish timing."""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")
NEAR = "七之三、未來 48 小時關鍵事件情境"
LATER = "七之三、後續排程事件情境（超過 48 小時）"
MIXED = "七之三、跨時段事件情境（含 48 小時內與後續排程）"
UNKNOWN = "七之三、事件情境（時間待確認）"
PAST = "七之三、已公布事件情境回顧"


def clock(value) -> dt.datetime | None:
    try:
        if not isinstance(value, dt.datetime):
            if not isinstance(value, str) or not re.search(r"[T ]\d{2}:\d{2}", value):
                return None
            value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.replace(tzinfo=TPE) if value.tzinfo is None else value.astimezone(TPE)
    except (ValueError, TypeError, OverflowError):
        return None


def event_clock(row) -> dt.datetime | None:
    if not isinstance(row, dict) or not re.fullmatch(r"\d{2}:\d{2}", str(row.get("time") or "")):
        return None
    day = row.get("date")
    if isinstance(day, dt.datetime):
        day = day.date()
    return clock(f"{day} {row['time']}")


def near_rows(rows, now):
    """Only exact known times enter the 48h prompt; full calendar remains elsewhere."""
    start = clock(now)
    if start is None:
        return []
    end = start + dt.timedelta(hours=48)
    return [row for row in (rows or []) if (stamp := event_clock(row)) is not None
            and start < stamp <= end]


def _resolve(event: str, packet: dict):
    from reader_fact_labels import scenario_time
    resolved = scenario_time(event, "", packet)
    stamps = [clock(m) for m in re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", resolved)]
    if len(stamps) == 1 and re.search(r"與|及|和|、|\band\b|&", event, re.I):
        return "", []  # A matched macro name cannot date an unmatched combined event.
    if not stamps:
        # Non-macro events require an exact calendar title, not an inferred date.
        rows = (packet.get("market") or {}).get("EVENT_CALENDAR") or []
        matches = [r for r in rows if isinstance(r, dict)
                   and str(r.get("title") or "").strip().casefold() == event.strip().casefold()]
        stamps = [event_clock(r) for r in matches]
        if len(set(stamps)) > 1:
            return "", []  # Repeated editions are not a unique event timestamp.
        resolved = "；".join(stamp.strftime("%Y-%m-%d %H:%M") for stamp in stamps if stamp) + "（台北）"
    return resolved, stamps


def display_time(event, fallback, packet):
    resolved, stamps = _resolve(event, packet)
    return resolved if stamps and all(stamps) else (fallback + "（時間未核實）" if fallback else "時間待確認")


def heading(event: str, packet: dict) -> str:
    """Preserve every scenario but do not advertise unknown/later times as 48h."""
    start = clock(packet.get("as_of"))
    _, stamps = _resolve(event, packet)
    if start is None:
        return UNKNOWN
    if not stamps or any(stamp is None for stamp in stamps):
        return UNKNOWN
    if all(stamp <= start for stamp in stamps):
        return PAST
    if all(start < stamp <= start + dt.timedelta(hours=48) for stamp in stamps):
        return NEAR
    if all(stamp > start for stamp in stamps):
        return LATER if all(stamp > start + dt.timedelta(hours=48) for stamp in stamps) else MIXED
    return UNKNOWN
