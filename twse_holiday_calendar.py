"""Verified TWSE full-market closures for 2026; no runtime network dependency."""

import datetime as dt

# Taiwan Stock Exchange's 115 年市場開休市日期:
# https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=html
# Weekends are handled by the caller; these are weekday closures only.
_CLOSED_2026 = frozenset(dt.date.fromisoformat(day) for day in (
    "2026-01-01", "2026-02-12", "2026-02-13", "2026-02-16",
    "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-27", "2026-04-03", "2026-04-06", "2026-05-01",
    "2026-06-19", "2026-09-25", "2026-09-28", "2026-10-09",
    "2026-10-26", "2026-12-25",
))


def is_known_closed(day: dt.date) -> bool:
    """Only assert closures documented in the 2026 official schedule."""
    return day in _CLOSED_2026
