"""美股日線備用來源(使用者 2026-11-12:「一個掛掉就看不到資料了」)。"""
import pytest

import us_market_session as ums
import us_quote_backup as backup
import us_quote_recovery as recovery


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def raise_for_status(self):
        if self.status >= 400:
            raise OSError(f"HTTP {self.status}")

    def json(self):
        return self.body


def _nasdaq(symbol="QQQ", rows=None):
    rows = rows if rows is not None else [
        {"date": "11/11/2026", "close": "$690.38", "volume": "44,871,770", "high": "695.57", "low": "683.5617"},
        {"date": "11/10/2026", "close": "674.76", "volume": "38,845,160", "high": "680.95", "low": "667.05"}]
    return {"data": {"symbol": symbol, "tradesTable": {"rows": rows}}}


def _yahoo_null_close():
    """11-12 那班的 Yahoo:11/11 那一格在,收盤是空的。"""
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "QQQ", "exchangeTimezoneName": "America/New_York", "currency": "USD", "dataGranularity": "1d"},
        "timestamp": [1794321000, 1794407400],
        "indicators": {"quote": [{"close": [674.76, None], "high": [1, 1], "low": [1, 1], "volume": [1, 1]}]}}]}}


def test_backup_parses_both_sessions_and_keeps_the_shape():
    quote = backup.fetch("QQQ", "2026-11-11", "2026-11-10", lambda *a, **k: _Resp(_nasdaq()))
    assert quote == {"ticker": "QQQ", "date": "2026-11-11", "close": 690.38, "prev_close": 674.76,
                     "change_pct": 2.31, "quote_source": "nasdaq_backup", "previous_date": "2026-11-10",
                     "high": 695.57, "low": 683.5617, "volume": 44871770}
    assert ums._complete(dict(quote), "2026-11-11")


@pytest.mark.parametrize("body, reason", [
    (_nasdaq("SPY"), "provenance_mismatch"),
    (_nasdaq(rows=[{"date": "11/10/2026", "close": "674.76"}]), "session_bar_missing"),
    (_nasdaq(rows=[{"date": "11/11/2026", "close": "690.38"}]), "previous_bar_missing"),
    (_nasdaq(rows=[{"date": "11/11/2026", "close": "N/A"}, {"date": "11/10/2026", "close": "674.76"}]), "session_close_invalid"),
    (_nasdaq(rows=[{"date": "11/11/2026", "close": "0"}, {"date": "11/10/2026", "close": "674.76"}]), "session_close_invalid"),
    (_nasdaq(rows=[{"date": "11/12/2026", "close": "1"}, {"date": "11/11/2026", "close": "1"},
                   {"date": "11/10/2026", "close": "1"}]), "duplicate_or_future_bar"),
    (_nasdaq(rows=[{"date": "壞日期", "close": "1"}]), "date_invalid"),
    ({"data": None}, "provenance_mismatch"),
])
def test_backup_rejects_anything_that_is_not_exactly_the_two_sessions(body, reason):
    with pytest.raises(recovery.Rejected) as caught:
        backup.fetch("QQQ", "2026-11-11", "2026-11-10", lambda *a, **k: _Resp(body))
    assert caught.value.reason == reason


def test_unsupported_symbol_and_http_errors_do_not_pass():
    with pytest.raises(recovery.Rejected):
        backup.fetch("NVDA", "2026-11-11", "2026-11-10", lambda *a, **k: _Resp(_nasdaq("NVDA")))
    with pytest.raises(OSError):
        backup.fetch("QQQ", "2026-11-11", "2026-11-10", lambda *a, **k: _Resp({}, 403))


def test_fetch_any_uses_the_backup_only_when_yahoo_fails(capsys):
    def get(url, **_kw):
        return _Resp(_nasdaq()) if "nasdaq.com" in url else _Resp(_yahoo_null_close())
    quote = recovery.fetch_any("QQQ", "2026-11-11", "2026-11-10", get)
    assert quote["quote_source"] == "nasdaq_backup" and quote["close"] == 690.38
    assert "改用備用來源 Nasdaq" in capsys.readouterr().out

    def both_down(url, **_kw):
        return _Resp({}, 403) if "nasdaq.com" in url else _Resp(_yahoo_null_close())
    with pytest.raises(recovery.Rejected) as caught:
        recovery.fetch_any("QQQ", "2026-11-11", "2026-11-10", both_down)
    assert caught.value.reason == "session_close_missing"   # 回報 Yahoo 那次的原因
    assert "backup quote failed" in capsys.readouterr().out


def test_refresh_quotes_fills_the_stale_quote_from_the_backup():
    import datetime as dt

    def get(url, **_kw):
        return _Resp(_nasdaq()) if "nasdaq.com" in url else _Resp(_yahoo_null_close())
    quotes = {"QQQ": {"ticker": "QQQ", "date": "2026-11-10", "close": 674.76, "prev_close": 679.35, "change_pct": -0.68}}
    now = dt.datetime(2026, 11, 12, 10, 12, tzinfo=ums._TPE)
    ums.refresh_quotes(quotes, now, lambda s: dict(quotes.get(s) or {"error": "x"}),
                       recover=lambda s, day, prev: recovery.fetch_any(s, day, prev, get))
    assert quotes["QQQ"]["date"] == "2026-11-11" and quotes["QQQ"]["stale"] is False
    assert quotes["QQQ"]["quote_source"] == "nasdaq_backup"
