"""US equity sessions for reports produced after the close (06:00 Taipei).

Calendar closure is independent of quote freshness. No network or state I/O;
refreshing uses only the supplied callback, once per affected symbol.
"""

import datetime as dt
import math
from collections.abc import Callable
from typing import Any, TypeGuard


# Verified by HTTP 200 on 2026-10-09:
# https://www.nyse.com/trade/hours-calendars
# Early closes are sessions. In particular, 2027-12-31 is OPEN.
_HOLIDAYS = {
    2026: frozenset("01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25".split()),
    2027: frozenset("01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24".split()),
    2028: frozenset("01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25".split()),
}
_WEEKDAYS = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")
_TPE = dt.timezone(dt.timedelta(hours=8))


def _date(value: object) -> dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _holiday(day: dt.date) -> bool:
    return day.strftime("%m-%d") in _HOLIDAYS.get(day.year, ())


def _calendar(today: dt.date) -> tuple[dt.date, dt.date | None, bool]:
    expected = today - dt.timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= dt.timedelta(days=1)
    detected = _holiday(expected)
    session = expected
    if today.year not in _HOLIDAYS:
        return expected, None, False
    while session.year in _HOLIDAYS:
        if session.weekday() < 5 and not _holiday(session):
            return expected, session, detected
        session -= dt.timedelta(days=1)
    return expected, None, detected


def detect(quotes: dict[str, Any], today_tpe_date: dt.date) -> dict[str, Any]:
    """Classify the prior US weekday; quote dates never establish a holiday.

    ``gap_days`` retains the legacy calendar-day difference, not session count.
    Unknown calendar coverage is unavailable, never evidence of a closure.
    """
    expected, session, detected = _calendar(today_tpe_date)
    quote = quotes.get("QQQ")
    quote = quote if isinstance(quote, dict) else {}
    raw = quote.get("date")
    actual = _date(raw)
    if session is None:
        reason = "unknown_calendar"
        print("::warning::US session calendar unavailable for "
              f"{today_tpe_date.isoformat()}; treating US data as stale")
    elif raw in (None, ""):
        reason = "missing_date"
    elif actual is None:
        reason = "invalid_date"
    elif actual > session:
        reason = "future_date"
    elif quote.get("error") or quote.get("stale"):
        reason = "data_lag" if actual < session else "unavailable"
    elif detected:
        reason = "holiday"
    elif actual < expected:
        reason = "data_lag"
    else:
        reason = "fresh"
    return {
        "detected": detected,
        "stale": detected or reason != "fresh",
        "reason": reason,
        "actual_date": raw if isinstance(raw, str) else "",
        "actual_weekday": _WEEKDAYS[actual.weekday()] if actual else "",
        "expected_date": expected.isoformat(),
        "expected_session_date": session.isoformat() if session else "",
        "gap_days": (expected - actual).days if actual else None,
    }


def is_stale(info: object) -> bool:
    """Honor explicit flags; absent legacy status does not disable US signals."""
    if not isinstance(info, dict):
        return False
    return bool(info.get("detected", False) or info.get("stale", False))


def status_text(info: object) -> str:
    """Reader-facing status, without presenting a data outage as a holiday."""
    if not isinstance(info, dict):
        return "美股資料不可用，無法確認最新收盤。"
    actual = info.get("actual_date") or "未知"
    expected = info.get("expected_session_date") or info.get("expected_date") or "未知"
    if info.get("detected"):
        return (f"美股上一預期平日 {info.get('expected_date') or '未知'} 為官方休市日；"
                f"最新資料日期 {actual}，應有交易日 {expected}，非新一日美股訊號。")
    if info.get("reason") == "data_lag":
        return f"美股行情資料落後：最新資料 {actual}，應有交易日 {expected}；不是休市。"
    if is_stale(info):
        if info.get("reason") == "unknown_calendar":
            return "美股交易日曆未涵蓋此日期，資料新鮮度無法確認；不可判定休市。"
        return f"美股資料不可用：資料日期 {actual}，應有交易日 {expected}；不可判定休市。"
    return f"美股最新收盤資料日期 {actual}，符合預期交易日 {expected}。"


def _positive_finite(value: Any) -> bool:
    if isinstance(value, (bool, str, bytes)):
        return False
    try:
        return bool(math.isfinite(value) and value > 0)
    except (TypeError, ValueError, OverflowError):
        return False


def _complete(quote: object, session: str) -> TypeGuard[dict[str, Any]]:
    return (isinstance(quote, dict) and bool(session)
            and quote.get("date") == session and not quote.get("error")
            and _positive_finite(quote.get("close"))
            and _positive_finite(quote.get("prev_close")))


def usable_value(quote: object, field: str) -> float | None:
    """Do not promote retained display-only observations into forecasts/history."""
    if not isinstance(quote, dict) or quote.get("stale") or quote.get("error"):
        return None
    value = quote.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def stale_quote_label(quote: dict[str, Any]) -> str:
    observed = _date(quote.get("date"))
    return f"行情未更新（觀測日 {observed.isoformat() if observed else '未知'}）"


def subject_change(quote: dict[str, Any]) -> str:
    if quote.get("stale"):
        return stale_quote_label(quote)
    change = usable_value(quote, "change_pct")
    return f"{change}%" if change is not None else "資料缺失"


def refresh_quotes(quotes: dict[str, Any], now_tpe: dt.datetime,
                   fetch: Callable[[str], dict[str, Any]], *,
                   recover: Callable[[str, str, str], dict[str, Any]] | None = None) -> None:
    """Refresh unusable base quotes once before predictions; tag every symbol.

    A valid last-session quote on a holiday remains usable for the existing
    models (stale=False), while detect() still suppresses new overnight signals.
    Naive datetimes are interpreted as Taipei; aware ones are converted there.
    """
    today = (now_tpe.astimezone(_TPE) if now_tpe.tzinfo else now_tpe).date()
    _, session, _ = _calendar(today)
    expected = session.isoformat() if session else ""
    for symbol in ("QQQ", "TSM", "SPY"):
        prior = quotes.get(symbol)
        if _complete(prior, expected):
            prior["stale"] = False
            continue
        replacement = None
        if expected:
            try:
                replacement = fetch(symbol)
            except Exception as exc:
                # Do not leak provider response bodies into workflow logs.
                print(f"::warning::{symbol} quote refresh failed ({type(exc).__name__})")
        if not _complete(replacement, expected) and recover is not None and session:
            _, previous, _ = _calendar(session)
            if previous:
                try:
                    replacement = recover(symbol, expected, previous.isoformat())
                except Exception as exc:
                    print(f"::warning::{symbol} chart recovery failed ({type(exc).__name__})")
        if _complete(replacement, expected):
            replacement = dict(replacement)
            replacement["stale"] = False
            quotes[symbol] = replacement
            if replacement.get("quote_source") == "yahoo_chart_recovery":
                print(f"[quote] {symbol} recovered expected session {expected} via Yahoo chart")
        else:
            if not isinstance(prior, dict):
                prior = {"error": "quote unavailable"}
                quotes[symbol] = prior
            prior["stale"] = True
            print(f"::warning::{symbol} quote unavailable for expected session "
                  f"{expected or 'unknown (calendar unavailable)'}; retained prior data as stale")
