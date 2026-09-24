"""Carry a market bar's own date into prose without inventing a fresh quote.

The Yahoo daily index is in the market's timezone; converting it to Taipei
before taking the date can incorrectly call a US close the next day's close.
"""
from __future__ import annotations

from datetime import date, datetime


_SOURCE = "Yahoo Finance"


def observed_market_date(index_value: object) -> str | None:
    """Return only dates supplied by a datetime-like market index."""
    if isinstance(index_value, datetime):
        return index_value.date().isoformat()
    if isinstance(index_value, date):
        return index_value.isoformat()
    return None


def _valid_date(row: dict) -> str | None:
    value = row.get("observed_on")
    if not isinstance(value, str):
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def indicator_source_note(row: dict) -> str:
    """Old fixtures without provenance retain their old text shape."""
    if row.get("source") != _SOURCE:
        return ""
    observed = _valid_date(row)
    return f"；{_SOURCE}，{observed} 資料日" if observed else f"；{_SOURCE}，資料日未確認"


def yield_curve_source_note(macro: dict) -> str:
    """Do not imply one observation date when rate tenors differ."""
    legs = [(label, macro.get(key) or {}) for label, key in
            (("短期", "13W"), ("10 年", "10Y"), ("30 年", "30Y"))]
    legs = [(label, row) for label, row in legs if row.get("close") is not None]
    if not legs or not any("source" in row for _, row in legs):
        return ""
    if any(row.get("source") != _SOURCE for _, row in legs):
        return "；來源／資料日未確認"
    dates = [_valid_date(row) for _, row in legs]
    if dates[0] is not None and all(value == dates[0] for value in dates):
        return f"；{_SOURCE}，{dates[0]} 資料日"
    pairs = "、".join(f"{label} {value or '資料日未確認'}"
                     for (label, _), value in zip(legs, dates))
    return f"；{_SOURCE}，{pairs}"


def yield_curve_dates_comparable(macro: dict) -> bool:
    """Do not infer one curve from tenors observed on different or unknown dates."""
    legs = [macro.get(key) or {} for key in ("13W", "10Y", "30Y")]
    legs = [row for row in legs if row.get("close") is not None]
    if not any("source" in row for row in legs):
        return True  # Preserve older fixtures that have no provenance fields.
    dates = [_valid_date(row) if row.get("source") == _SOURCE else None
             for row in legs]
    return bool(dates and dates[0] and all(value == dates[0] for value in dates))
