import datetime as dt
from unittest.mock import Mock

import pytest

import us_market_session as us


def _quote(day="2026-10-08", **fields):
    return {"date": day, "close": 100.0, "prev_close": 99.0, **fields}


def _quotes(**overrides):
    return {"QQQ": _quote(), "TSM": _quote(), "SPY": _quote(), **overrides}


def test_october_9_lag_is_not_holiday():
    info = us.detect({"QQQ": _quote("2026-10-07")}, dt.date(2026, 10, 9))
    assert info == {
        "detected": False, "stale": True, "reason": "data_lag",
        "actual_date": "2026-10-07", "actual_weekday": "週三",
        "expected_date": "2026-10-08", "expected_session_date": "2026-10-08",
        "gap_days": 1,
    }
    assert "資料落後" in us.status_text(info)
    assert "不是休市" in us.status_text(info)


def test_october_9_fresh_quote():
    info = us.detect(_quotes(), dt.date(2026, 10, 9))
    assert not info["detected"] and not us.is_stale(info)
    assert info["reason"] == "fresh" and info["gap_days"] == 0
    assert "符合預期" in us.status_text(info)


@pytest.mark.parametrize("holiday,previous", [
    ("2026-01-19", "2026-01-16"), ("2026-02-16", "2026-02-13"),
    ("2026-04-03", "2026-04-02"), ("2026-05-25", "2026-05-22"),
    ("2026-06-19", "2026-06-18"), ("2026-07-03", "2026-07-02"),
    ("2026-09-07", "2026-09-04"), ("2026-11-26", "2026-11-25"),
    ("2026-12-25", "2026-12-24"), ("2027-01-01", "2026-12-31"),
    ("2027-01-18", "2027-01-15"), ("2027-02-15", "2027-02-12"),
    ("2027-03-26", "2027-03-25"), ("2027-05-31", "2027-05-28"),
    ("2027-06-18", "2027-06-17"), ("2027-07-05", "2027-07-02"),
    ("2027-09-06", "2027-09-03"), ("2027-11-25", "2027-11-24"),
    ("2027-12-24", "2027-12-23"), ("2028-01-17", "2028-01-14"),
    ("2028-02-21", "2028-02-18"), ("2028-04-14", "2028-04-13"),
    ("2028-05-29", "2028-05-26"), ("2028-06-19", "2028-06-16"),
    ("2028-07-04", "2028-07-03"), ("2028-09-04", "2028-09-01"),
    ("2028-11-23", "2028-11-22"), ("2028-12-25", "2028-12-22"),
])
def test_official_holidays(holiday, previous):
    today = dt.date.fromisoformat(holiday) + dt.timedelta(days=1)
    info = us.detect({"QQQ": _quote(previous)}, today)
    assert info["detected"] and info["stale"]
    assert info["reason"] == "holiday"
    assert info["expected_date"] == holiday
    assert info["expected_session_date"] == previous
    assert "官方休市日" in us.status_text(info)
    assert us.detect({}, today)["detected"]  # Closure never depends on a quote.


@pytest.mark.parametrize("today,expected", [
    ("2026-10-10", "2026-10-09"), ("2026-10-11", "2026-10-09"),
    ("2026-10-12", "2026-10-09"), ("2026-10-13", "2026-10-12"),
    ("2026-11-28", "2026-11-27"), ("2026-12-25", "2026-12-24"),
    ("2028-01-01", "2027-12-31"), ("2028-01-03", "2027-12-31"),
    ("2028-07-04", "2028-07-03"),
])
def test_weekends_bank_holidays_and_early_closes(today, expected):
    info = us.detect({"QQQ": _quote(expected)}, dt.date.fromisoformat(today))
    assert not info["detected"] and not info["stale"]
    assert info["expected_date"] == info["expected_session_date"] == expected


@pytest.mark.parametrize("quote,reason", [
    (None, "missing_date"), ({}, "missing_date"), ({"date": ""}, "missing_date"),
    ({"date": "2026-02-30"}, "invalid_date"), ({"date": 123}, "invalid_date"),
    ({"date": "20261008"}, "invalid_date"), ({"date": "2026-10-09"}, "future_date"),
    ({"date": "2030-01-01"}, "future_date"),
    (_quote(stale=True), "unavailable"), (_quote(error="bad quote"), "unavailable"),
])
def test_unavailable_quotes(quote, reason):
    info = us.detect({"QQQ": quote}, dt.date(2026, 10, 9))
    assert not info["detected"] and info["stale"]
    assert info["reason"] == reason
    assert "資料不可用" in us.status_text(info)


def test_holiday_bar_is_not_a_completed_session():
    info = us.detect({"QQQ": _quote("2026-07-03")}, dt.date(2026, 7, 4))
    assert info["detected"] and info["stale"]
    assert info["reason"] == "future_date"
    assert info["expected_session_date"] == "2026-07-02"


@pytest.mark.parametrize("today", [dt.date(2025, 10, 9), dt.date(2029, 10, 9)])
def test_unknown_calendar_is_visible_not_a_holiday(today, capsys):
    info = us.detect({"QQQ": _quote((today - dt.timedelta(days=1)).isoformat())}, today)
    assert info["stale"] and not info["detected"]
    assert us.is_stale(info)
    assert info["reason"] == "unknown_calendar" and info["expected_session_date"] == ""
    assert "::warning::" in capsys.readouterr().out
    assert "日曆未涵蓋" in us.status_text(info)


def test_known_new_year_holiday_does_not_guess_previous_year(capsys):
    info = us.detect({"QQQ": _quote("2025-12-31")}, dt.date(2026, 1, 2))
    assert info["detected"] and info["stale"]
    assert info["expected_session_date"] == "" and info["reason"] == "unknown_calendar"
    assert "::warning::" in capsys.readouterr().out


@pytest.mark.parametrize("info,expected", [
    ({"detected": True}, True), ({"detected": False}, False),
    ({"detected": False, "stale": True}, True),
    ({"detected": True, "stale": False}, True),
    ({"stale": True}, True), ({"stale": False}, False),
    ({}, False), (None, False), ({"actual_date": "2026-10-08"}, False),
    ({"detected": False, "stale": False}, False),
])
def test_is_stale_legacy_and_current(info, expected):
    assert us.is_stale(info) is expected


def test_refresh_recovers_once_and_keeps_other_quotes(capsys):
    quotes = _quotes(QQQ=_quote("2026-10-07"))
    tsm = quotes["TSM"]
    recovered = _quote(stale=True, change_pct=1.01)
    fetch = Mock(return_value=recovered)
    assert us.refresh_quotes(quotes, dt.datetime(2026, 10, 9, 6), fetch) is None
    fetch.assert_called_once_with("QQQ")
    assert quotes["QQQ"]["date"] == "2026-10-08"
    assert quotes["QQQ"]["change_pct"] == 1.01
    assert all(q["stale"] is False for q in quotes.values())
    assert quotes["TSM"] is tsm and recovered["stale"] is True
    assert not capsys.readouterr().out


@pytest.mark.parametrize("replacement", [
    None, {}, _quote("2026-10-07"), _quote("2026-10-09"),
    _quote(close=0), _quote(close=-1), _quote(close=float("nan")),
    _quote(close=float("inf")), _quote(prev_close=float("-inf")),
    _quote(prev_close=None), _quote(prev_close=True), _quote(close="100"),
    _quote(error="unavailable"),
])
def test_failed_refresh_preserves_prior_and_marks_stale(replacement, capsys):
    prior = _quote("2026-10-07")
    quotes = _quotes(QQQ=prior)
    fetch = Mock(return_value=replacement)
    us.refresh_quotes(quotes, dt.datetime(2026, 10, 9, 6), fetch)
    fetch.assert_called_once_with("QQQ")
    assert quotes["QQQ"] is prior
    assert prior == _quote("2026-10-07", stale=True)
    assert "::warning::QQQ" in capsys.readouterr().out


def test_refresh_exception_does_not_stop_other_symbols(capsys):
    quotes = {"QQQ": None, "TSM": _quote("2026-10-07"), "SPY": _quote("2026-10-09")}
    fetch = Mock(side_effect=[RuntimeError("private response body"), _quote(), _quote()])
    us.refresh_quotes(quotes, dt.datetime(2026, 10, 9, 6), fetch)
    assert [c.args for c in fetch.call_args_list] == [("QQQ",), ("TSM",), ("SPY",)]
    assert quotes["QQQ"]["stale"] is True
    assert not quotes["TSM"]["stale"] and not quotes["SPY"]["stale"]
    output = capsys.readouterr().out
    assert "::warning::" in output and "private response body" not in output


def test_holiday_prior_session_stays_usable_without_retry():
    quotes = {symbol: _quote("2026-07-02") for symbol in ("QQQ", "TSM", "SPY")}
    fetch = Mock()
    us.refresh_quotes(quotes, dt.datetime(2026, 7, 4, 6), fetch)
    fetch.assert_not_called()
    assert all(q["stale"] is False for q in quotes.values())
    assert us.is_stale(us.detect(quotes, dt.date(2026, 7, 4)))


def test_current_date_but_invalid_price_is_not_usable():
    quotes = _quotes(QQQ=_quote(close=float("nan")))
    fetch = Mock(return_value=_quote())
    us.refresh_quotes(quotes, dt.datetime(2026, 10, 9, 6), fetch)
    fetch.assert_called_once_with("QQQ")
    assert quotes["QQQ"]["close"] == 100 and not quotes["QQQ"]["stale"]


def test_unknown_calendar_tags_all_without_guessing_refresh(capsys):
    quotes = _quotes()
    fetch = Mock()
    us.refresh_quotes(quotes, dt.datetime(2029, 10, 9, 6), fetch)
    fetch.assert_not_called()
    assert all(q["stale"] for q in quotes.values())
    assert capsys.readouterr().out.count("::warning::") == 3


def test_refresh_converts_aware_clock_to_taipei():
    quotes = _quotes()
    fetch = Mock()
    us.refresh_quotes(quotes, dt.datetime(2026, 10, 8, 22, tzinfo=dt.timezone.utc), fetch)
    fetch.assert_not_called()
    assert all(q["stale"] is False for q in quotes.values())
