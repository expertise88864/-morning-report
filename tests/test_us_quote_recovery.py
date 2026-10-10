"""Recover stale daily quotes without accepting the wrong session or symbol."""
import datetime as dt
from unittest.mock import Mock

import pytest

import us_market_session as us
import us_quote_recovery as recovery


def payload():
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "QQQ", "exchangeTimezoneName": "America/New_York",
                 "currency": "USD", "dataGranularity": "1d"},
        "timestamp": [1791466200, 1791552600],
        "indicators": {"quote": [{"close": [100., 102.], "high": [101., 103.],
                                    "low": [99., 101.], "volume": [1000, 1200]}]},
    }]}}


def fetch(data):
    response = Mock()
    response.json.return_value = data
    http = Mock(return_value=response)
    result = recovery.fetch("QQQ", "2026-10-09", "2026-10-08", http)
    assert http.call_args.kwargs["timeout"] == 12
    assert http.call_args.kwargs["retries"] == 1
    assert http.call_args.kwargs["params"]["interval"] == "1d"
    return result


def test_exact_daily_dates_restore_the_expected_close_and_change():
    result = fetch(payload())
    assert result["date"] == "2026-10-09" and result["previous_date"] == "2026-10-08"
    assert result["close"] == 102 and result["change_pct"] == 2
    assert result["quote_source"] == "yahoo_chart_recovery"


@pytest.mark.parametrize("mutation", [
    lambda r: r["meta"].update(symbol="SPY"),
    lambda r: r["meta"].update(currency="TWD"),
    lambda r: r["meta"].update(dataGranularity="1m"),
    lambda r: r["meta"].update(exchangeTimezoneName="UTC"),
    lambda r: r.update(timestamp=[1791379800, 1791466200]),
    lambda r: r.update(timestamp=[1791466200, 1791466200]),
    lambda r: r.update(timestamp=[1791466200, 1791639000]),
    lambda r: r["indicators"]["quote"][0].update(close=[100., float("nan")]),
    lambda r: r["indicators"]["quote"][0].update(close=[None, 102.]),
    lambda r: r["indicators"]["quote"][0].update(close=[100.]),
])
def test_malformed_or_stale_chart_cannot_restore_predictions(mutation):
    data = payload()
    mutation(data["chart"]["result"][0])
    with pytest.raises((KeyError, ValueError)):
        fetch(data)


def test_chart_only_runs_after_primary_retry_remains_stale():
    old = {"date": "2026-10-08", "close": 100., "prev_close": 99.}
    fresh = fetch(payload())
    quotes = {s: dict(fresh) for s in ("QQQ", "TSM", "SPY")}
    quotes["QQQ"] = old
    recover = Mock(return_value=fresh)
    us.refresh_quotes(quotes, dt.datetime(2026, 10, 10, 6), Mock(return_value=old), recover=recover)
    recover.assert_called_once_with("QQQ", "2026-10-09", "2026-10-08")
    assert quotes["QQQ"]["close"] == 102 and not quotes["QQQ"]["stale"]
    recover.reset_mock()
    us.refresh_quotes(quotes, dt.datetime(2026, 10, 10, 6), Mock(), recover=recover)
    recover.assert_not_called()


def test_failed_recovery_preserves_stale_guard_and_does_not_log_response(capsys):
    quotes = {s: {"date": "2026-10-08", "close": 100., "prev_close": 99.}
              for s in ("QQQ", "TSM", "SPY")}
    us.refresh_quotes(quotes, dt.datetime(2026, 10, 10, 6), Mock(return_value={}),
                      recover=Mock(side_effect=ValueError("private response")))
    assert all(q["stale"] for q in quotes.values())
    assert "private response" not in capsys.readouterr().out
