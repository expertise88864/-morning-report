"""Bounded Yahoo chart recovery after yfinance returns stale daily bars.

The query1 endpoint was verified with HTTP 200 on 2026-10-10. This is an
alternate transport to the same provider, not independent corroboration.
"""
import datetime as dt
import math
from zoneinfo import ZoneInfo


def _number(value, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (positive and value <= 0)):
        raise ValueError("invalid daily quote value")
    return value


def fetch(symbol: str, session: str, previous: str, http_get) -> dict:
    """Require both exact exchange dates; never synthesize a close from metadata."""
    if symbol not in {"QQQ", "TSM", "SPY"}:
        raise ValueError("unsupported recovery symbol")
    day, prev = dt.date.fromisoformat(session), dt.date.fromisoformat(previous)
    if prev >= day:
        raise ValueError("invalid expected sessions")
    ny = ZoneInfo("America/New_York")
    start = dt.datetime.combine(prev, dt.time(), ny)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), ny)
    response = http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                        params={"period1": int(start.timestamp()), "period2": int(end.timestamp()),
                                "interval": "1d", "includePrePost": "false"}, timeout=12, retries=1,
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    response.raise_for_status()
    chart = response.json()["chart"]
    if chart.get("error") or len(chart.get("result") or []) != 1:
        raise ValueError("daily chart unavailable")
    result = chart["result"][0]
    meta = result["meta"]
    if (meta.get("symbol") != symbol or meta.get("exchangeTimezoneName") != "America/New_York"
            or meta.get("currency") != "USD" or meta.get("dataGranularity") != "1d"):
        raise ValueError("daily chart provenance mismatch")
    times, values = result["timestamp"], result["indicators"]["quote"][0]
    closes = values["close"]
    if not times or len(times) != len(closes):
        raise ValueError("daily chart length mismatch")
    positions = {}
    for i, timestamp in enumerate(times):
        stamp = dt.datetime.fromtimestamp(_number(timestamp, positive=True), ny)
        date = stamp.date().isoformat()
        if date in positions or date > session:
            raise ValueError("duplicate or future daily bar")
        positions[date] = i
    latest, prior = positions[session], positions[previous]
    close, prev_close = _number(closes[latest], positive=True), _number(closes[prior], positive=True)
    out = {"ticker": symbol, "date": session, "close": round(close, 4),
           "prev_close": round(prev_close, 4), "change_pct": round((close / prev_close - 1) * 100, 2),
           "quote_source": "yahoo_chart_recovery", "previous_date": previous}
    for field in ("high", "low", "volume"):
        series = values.get(field)
        value = series[latest] if isinstance(series, list) and len(series) == len(times) else None
        out[field] = (_number(value) if value is not None else None)
    return out
