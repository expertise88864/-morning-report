"""Conservative adapter for date-only earnings entries from yfinance."""

import datetime as dt


def _source_date(value) -> dt.date | None:
    """Preserve the provider's calendar date; its time zone is not established."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def earnings_row(ticker: str, calendar, today: dt.date, end: dt.date) -> dict | None:
    """Do not infer an Eastern after-hours release from ``Earnings Date``."""
    if not isinstance(calendar, dict):
        return None
    values = calendar.get("Earnings Date")
    if values is None:
        return None
    if isinstance(values, (dt.date, str)):
        first = values
    else:
        try:
            first = next(iter(values))
        except (TypeError, StopIteration):
            return None
    day = _source_date(first)
    if day is None or not today <= day <= end:
        return None
    return {
        "date": day,
        "time": "時間待確認",
        "title": f"{ticker} 財報",
        "note": "yfinance 來源日期；時區與公布時刻未核實",
        "impact": "high",
        "date_zone_uncertain": True,
    }
