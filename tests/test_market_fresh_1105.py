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
    nan = [{"Date": "20261103", "USD/NTD": "32.76"}, {"Date": "20261104", "USD/NTD": "NaN"}]      # 外審 PR #43
    nan_prev = [{"Date": "20261103", "USD/NTD": "NaN"}, {"Date": "20261104", "USD/NTD": "32.82"}]
    zero = [{"Date": "20261103", "USD/NTD": "0"}, {"Date": "20261104", "USD/NTD": "32.82"}]
    for get in (lambda *a, **k: _Resp(nan), lambda *a, **k: _Resp(nan_prev), lambda *a, **k: _Resp(zero),
                lambda *a, **k: _Resp(stale), lambda *a, **k: _Resp(absurd), lambda *a, **k: _Resp(_FX[:1]),
                lambda *a, **k: _Resp([]), lambda *a, **k: 1 / 0):
        assert mf.with_official_fx(_macro(), get, today)["USDTWD"]["close"] == 32.85


def test_error_row_is_left_alone():
    out = mf.with_official_fx({"USDTWD": {"error": "x"}}, lambda *a, **k: _Resp(_FX), dtm.date(2026, 11, 5))
    assert out == {"USDTWD": {"error": "x"}}


def test_margin_is_on_the_breadth_card():
    text = mf.margin_card_text({"margin_balance_100m_twd": 6234.7, "margin_change_100m_twd": -104.4})
    assert "6,234.7 億" in text and "-104.4 億" in text
    assert mf.margin_card_text({"margin_balance_100m_twd": 6234.7}).endswith("億</b>")
    same = {"margin_balance_100m_twd": 6234.7, "date": "2026/10/30"}
    assert "資料）" not in mf.margin_card_text(same, "2026-10-30")
    assert "（10/30 資料）" in mf.margin_card_text(same, "2026-11-03")    # 外審 PR #43:退回舊資料要標日期
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


def test_prompt_quotes_official_fx_but_forecast_input_is_untouched():
    """外審 PR #43:信上顯示官方匯率,分析卻還在引用 Yahoo。預測用的 quotes["USDTWD"] 不能動。"""
    from test_deepseek_legacy_golden import legacy_prompt_inputs
    args = list(legacy_prompt_inputs())
    quotes = {**args[0], "USDTWD": 32.85, "USDTWD_prev": 32.585,
              "MACRO": {**(args[0].get("MACRO") or {}),
                        "USDTWD": {"close": 32.821, "prev_close": 32.76, "source": "TAIFEX"}}}
    args[0] = quotes
    assert "USD/TWD：今 32.821 / 昨 32.76" in mr._build_prompt(*args)
    assert quotes["USDTWD"] == 32.85 and quotes["USDTWD_prev"] == 32.585
    assert mf.fx_for_prompt({"USDTWD": 32.85, "USDTWD_prev": 32.585, "MACRO": {"USDTWD": {"close": 32.9}}}) == (32.85, 32.585)
    assert mf.fx_for_prompt({"USDTWD": 32.85, "USDTWD_prev": None}) == (32.85, None)


def test_fx_row_and_prompt_say_which_day_the_official_rate_is():
    """11-06 實信:06:15 期交所還沒有 11/05,信裡的 32.82 是 11/04 的值,卻看不出來。"""
    macro = {"USDTWD": {"close": 32.821, "prev_close": 32.76, "source": "TAIFEX", "observed_on": "2026-11-04"}}
    assert mf.fx_label(macro) == "台幣匯率 USD/TWD（11/04 官方）"
    assert mf.fx_label({"USDTWD": {"close": 32.85}}) == "台幣匯率 USD/TWD" and mf.fx_label({}) == "台幣匯率 USD/TWD"
    assert "2026-11-04" in mf.fx_prompt_note({"MACRO": macro}) and mf.fx_prompt_note({}) == ""
