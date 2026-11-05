"""2026-11-05 查證結論:亞股落後一天、台幣與官方不符、融資沒有固定顯示。數字都取自實際資料。"""
import datetime as dtm

import pandas as pd

import market_fresh as mf
import morning_report as mr

_SEOUL = "Asia/Seoul"


def _stamp(y, m, d, hh=15):
    return int(pd.Timestamp(dtm.datetime(y, m, d, hh), tz=_SEOUL).timestamp())


class _Ticker:
    def __init__(self, meta):
        self.history_metadata = meta


_BARS = pd.DataFrame({"Close": [5859.08, 5648.79]},
                     index=pd.DatetimeIndex(["2026-11-02", "2026-11-03"], tz=_SEOUL))


def test_newer_trade_replaces_the_stale_bar():
    """實信 11-05:KOSPI 寫 5,648.79(11/03),11/04 其實是 5,683.8。"""
    meta = {"regularMarketPrice": 5683.8, "regularMarketTime": _stamp(2026, 11, 4), "exchangeTimezoneName": _SEOUL}
    assert mf.latest_close("KOSPI", _Ticker(meta), _BARS, 5648.79, 5859.08) == (5683.8, 5648.79)


def test_same_day_trade_keeps_the_bars():
    meta = {"regularMarketPrice": 5650.0, "regularMarketTime": _stamp(2026, 11, 3), "exchangeTimezoneName": _SEOUL}
    assert mf.latest_close("KOSPI", _Ticker(meta), _BARS, 5648.79, 5859.08) == (5648.79, 5859.08)


def test_bad_or_missing_metadata_keeps_the_bars():
    for meta in ({}, None, {"regularMarketPrice": "x", "regularMarketTime": 1},
                 {"regularMarketPrice": 1.0, "regularMarketTime": _stamp(2026, 11, 4), "exchangeTimezoneName": _SEOUL}):
        assert mf.latest_close("KOSPI", _Ticker(meta), _BARS, 5648.79, 5859.08) == (5648.79, 5859.08), meta


def test_datetime_stamp_is_also_understood():
    stamp = dtm.datetime(2026, 11, 4, 6, 30, tzinfo=dtm.timezone.utc)
    meta = {"regularMarketPrice": 5683.8, "regularMarketTime": stamp, "exchangeTimezoneName": _SEOUL}
    assert mf.latest_close("KOSPI", _Ticker(meta), _BARS, 5648.79, 5859.08)[0] == 5683.8


class _Resp:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self.rows


_FX = [{"Date": "20261103", "USD/NTD": "32.76"}, {"Date": "20261104", "USD/NTD": "32.821"}]


def _macro():
    return {"USDTWD": {"close": 32.85, "prev_close": 32.585, "change_pct": 0.8, "pct_rank_252d": 100.0},
            "VIX": {"close": 16.61}}


def test_official_fx_replaces_yahoo_on_the_macro_row():
    """實信 11-05 寫 32.85、+0.80%;期交所是 32.821、+0.19%。"""
    out = mf.with_official_fx(_macro(), lambda *a, **k: _Resp(_FX), dtm.date(2026, 11, 5))
    row = out["USDTWD"]
    assert (row["close"], row["prev_close"], row["change_pct"]) == (32.821, 32.76, 0.19)
    assert row["source"] == "TAIFEX" and row["observed_on"] == "2026-11-04"
    assert row["pct_rank_252d"] == 100.0 and out["VIX"] == {"close": 16.61}


def test_stale_broken_or_absurd_fx_keeps_yahoo():
    """OpenAPI 2026-08 曾停更:太舊就不用。"""
    today = dtm.date(2026, 11, 5)
    stale = [{"Date": "20261020", "USD/NTD": "32.1"}, {"Date": "20261021", "USD/NTD": "32.2"}]
    absurd = [{"Date": "20261103", "USD/NTD": "32.76"}, {"Date": "20261104", "USD/NTD": "3.28"}]
    for get in (lambda *a, **k: _Resp(stale), lambda *a, **k: _Resp(absurd), lambda *a, **k: _Resp(_FX[:1]),
                lambda *a, **k: _Resp([]), lambda *a, **k: 1 / 0):
        assert mf.with_official_fx(_macro(), get, today)["USDTWD"]["close"] == 32.85


def test_error_row_is_left_alone():
    out = mf.with_official_fx({"USDTWD": {"error": "x"}}, lambda *a, **k: _Resp(_FX), dtm.date(2026, 11, 5))
    assert out == {"USDTWD": {"error": "x"}}


def test_margin_is_on_the_breadth_card():
    text = mf.margin_card_text({"margin_balance_100m_twd": 6234.7, "margin_change_100m_twd": -104.4})
    assert "6,234.7 億" in text and "-104.4 億" in text
    assert mf.margin_card_text({"margin_balance_100m_twd": 6234.7}).endswith("億</b>")
    assert mf.margin_card_text({}) == "" and mf.margin_card_text(None) == ""


def test_macro_fetch_uses_fresh_close_end_to_end(monkeypatch):
    """接線:日線少一天時,總經列的收盤與漲跌幅要跟著更新;美股指標不套用。"""
    idx = pd.date_range("2026-07-01", periods=70, freq="B", tz=_SEOUL)
    closes = [5000.0 + i for i in range(68)] + [5859.08, 5648.79]
    frame = pd.DataFrame({"Close": closes}, index=idx)
    meta = {"regularMarketPrice": 5683.8, "regularMarketTime": int((idx[-1] + pd.Timedelta(days=1)).timestamp()),
            "exchangeTimezoneName": _SEOUL}

    class Fake:
        def __init__(self, sym):
            self.history_metadata = meta

        def history(self, **_):
            return frame

    monkeypatch.setattr(mr.yf, "Ticker", Fake)
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: 1 / 0)
    macro = mr.fetch_macro_indicators()
    assert macro["KOSPI"]["close"] == 5683.8 and macro["KOSPI"]["change_pct"] == 0.62
    assert macro["SOX"]["close"] == 5648.79


def test_breadth_card_shows_margin_when_rendered():
    from test_brief_readability_1013 import _golden_quotes
    quotes = _golden_quotes()
    quotes["BREADTH"] = {"total": 100, "advance": 40, "decline": 50, "unchanged": 10, "advance_ratio": 40,
                         "total_value_yi": 1000}
    quotes["MARGIN"] = {"margin_balance_100m_twd": 6234.7, "margin_change_100m_twd": -104.4}
    out = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, "## 分析\n保留", "2026-11-05", "每日報")
    assert "融資餘額" in out and "6,234.7 億" in out and "-104.4 億" in out
