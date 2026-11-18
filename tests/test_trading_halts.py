"""停牌日不估 00662 公允價;復牌日累計停牌期間的 QQQ 與匯率(2026-11-17 使用者同意)。"""
import datetime as dt

import pandas as pd

import morning_report as mr
import trading_halts as th

D = dt.date


def test_halt_registry_days():
    assert not th.halted("00662", D(2026, 11, 10))
    assert th.halted("00662", D(2026, 11, 11)) and th.halted("00662.TW", "2026-11-16")
    assert not th.halted("00662", D(2026, 11, 17)) and not th.halted("0050", D(2026, 11, 12))
    assert th.resumed_since("00662", D(2026, 11, 17)) == D(2026, 11, 10)
    assert th.resumed_since("00662", D(2026, 11, 18)) is None
    assert "停牌中" in th.halt_note("00662", D(2026, 11, 13))


def _series(values: dict):
    return pd.Series(list(values.values()), index=pd.to_datetime(list(values)))


def test_rebase_uses_the_us_close_that_drove_the_last_trade():
    qqq = _series({"2026-11-06": 600.0, "2026-11-09": 610.0, "2026-11-10": 590.0, "2026-11-16": 560.0})
    fx = _series({"2026-11-09": 32.0, "2026-11-10": 32.1, "2026-11-16": 32.5})
    # 11/10 台股收盤反映的是美東 11/09 收盤 → 基準 610;匯率取 11/10
    prev, pct, fx_prev, fx_pct = th.rebase(qqq, fx, D(2026, 11, 10), 560.0, 590.0, -0.05, 32.5, 32.4, 0.003)
    assert (prev, round(pct, 6), fx_prev, round(fx_pct, 6)) == (610.0, round(560 / 610 - 1, 6), 32.1, round(32.5 / 32.1 - 1, 6))


def test_rebase_leaves_ordinary_days_and_missing_data_alone():
    qqq = _series({"2026-11-13": 600.0, "2026-11-16": 610.0, "2026-11-17": 605.0})
    plain = (610.0, -0.008, 32.4, 0.001)
    # 週三的晨報:最後交易日 11/17(二)之後只有一個美股收盤 → 跟原本一樣
    assert th.rebase(qqq, None, D(2026, 11, 17), 605.0, 610.0, -0.008, 32.5, 32.4, 0.001) == plain
    assert th.rebase(None, None, D(2026, 11, 10), 605.0, 610.0, -0.008, 32.5, 32.4, 0.001) == plain   # 歷史抓不到
    assert th.rebase(qqq, None, None, 605.0, 610.0, -0.008) == (610.0, -0.008, None, 0.0)


def test_previous_session_skips_weekends_and_listed_holidays(monkeypatch):
    monkeypatch.setattr(th.tw_holidays, "CLOSED", frozenset({"2027-02-08", "2027-02-09", "2027-02-10", "2027-02-11", "2027-02-12"}))
    assert th.tw_holidays.previous_session(D(2027, 2, 15)) == D(2027, 2, 5)   # 春節連假後第一天
    assert th.last_trade("2330", D(2027, 2, 15)) == D(2027, 2, 5)
    assert th.last_trade("00662", D(2026, 11, 17)) == D(2026, 11, 10)        # 停牌登記優先
    monkeypatch.setattr(th.tw_holidays, "CLOSED", frozenset())
    assert th.tw_holidays.previous_session(D(2026, 11, 16)) == D(2026, 11, 13)   # 週一 → 上週五


class _Hist:
    def __init__(self, df):
        self.df = df

    def history(self, **_kw):
        return self.df


def _fake_yf(monkeypatch):
    days = pd.bdate_range("2026-08-03", "2026-11-16")
    qqq = [600.0 + i for i in range(len(days))]
    frames = {"QQQ": pd.DataFrame({"Close": qqq}, index=days),
              "00662.TW": pd.DataFrame({"Close": [23.0] * len(days)}, index=days),
              "TWD=X": pd.DataFrame({"Close": [32.0] * len(days)}, index=days)}
    monkeypatch.setattr(mr.yf, "Ticker", lambda sym: _Hist(frames[sym]))
    return dict(zip(days.date, qqq))


def test_resume_day_accumulates_the_whole_halt(monkeypatch):
    q = _fake_yf(monkeypatch)
    close, prev = q[D(2026, 11, 16)], q[D(2026, 11, 13)]
    one_day = mr.calc_00662_fair_value(close, prev, 32.0, 23.56, usdtwd_prev=32.0)
    resumed = mr.calc_00662_fair_value(close, prev, 32.0, 23.56, usdtwd_prev=32.0, since_trade=D(2026, 11, 10))
    assert one_day["qqq_pct"] == round((close / prev - 1) * 100, 2)
    assert resumed["qqq_pct"] == round((close / q[D(2026, 11, 9)] - 1) * 100, 2)
    assert resumed["fair_price"] > one_day["fair_price"]


def test_halted_day_has_no_fair_value_and_is_not_a_data_error(monkeypatch):
    q = {"QQQ": {"close": 600.0, "prev_close": 599.0}, "TSM": None}
    monkeypatch.setattr(mr, "require_quote", lambda quotes, key: quotes.get(key))
    inputs = {"usdtwd_today": 32.0, "usdtwd_prev": 32.0, "last_00662": 117.8, "target_day": D(2026, 11, 12)}
    fair, _ = mr._us_price_predictions(q, inputs, None, {})
    assert fair.get("halted") and fair.get("fair_price") is None and "停牌中" in fair["error"]


def test_2330_after_a_holiday_uses_the_whole_adr_move(monkeypatch):
    days = pd.bdate_range("2026-08-03", "2026-11-16")
    tsm = pd.DataFrame({"Close": [300.0 + i for i in range(len(days))]}, index=days)
    fx = pd.DataFrame({"Close": [32.0] * len(days)}, index=days)
    tw_days = [d for d in days if d.date() not in (D(2026, 11, 12), D(2026, 11, 13))]   # 台股 11/12–13 休市
    hist = pd.DataFrame({"Open": [2000.0] * len(tw_days), "Close": [2000.0] * len(tw_days)}, index=pd.DatetimeIndex(tw_days))
    monkeypatch.setattr(mr.yf, "Ticker", lambda sym: _Hist({"TSM": tsm, "TWD=X": fx}[sym]))
    monkeypatch.setattr(mr, "fetch_twse_close", lambda code: None)
    by_day = dict(zip(days.date, tsm["Close"]))
    close, prev = by_day[D(2026, 11, 16)], by_day[D(2026, 11, 13)]
    plain = mr.calc_2330_predictions(close, prev, 32.0, hist)
    after = mr.calc_2330_predictions(close, prev, 32.0, hist, since_trade=D(2026, 11, 11))
    assert plain["tsm_pct"] == round((close / prev - 1) * 100, 2)
    assert after["tsm_pct"] == round((close / by_day[D(2026, 11, 10)] - 1) * 100, 2)
    assert after["model1_1to1"] > plain["model1_1to1"]


def test_resume_day_misses_do_not_feed_the_00662_bias(fake_yf, monkeypatch):
    """11/18 實信:11/17 復牌日以舊公式(只算一天)算的大誤差進了 bias,00662 被翻轉防護夾成 +0.00%。"""
    days = pd.date_range("2026-04-01", periods=20, freq="B")
    fake_yf({"^TWII": pd.DataFrame({"Open": [20000.0] * 20}, index=days),
             "2330.TW": pd.DataFrame({"Open": [1000.0] * 20}, index=days),
             "00662.TW": pd.DataFrame({"Open": [103.0] * 20}, index=days)})   # 實際比預測高 3%
    dates = [d.date() for d in days[:12]]
    history = [{"date": d.isoformat(), "fair_00662": 100.0} for d in dates]
    fair = {"fair_price": 99.0, "last_00662_price": 100.0}
    learned, _, _ = mr.calibrate_predictions(dict(fair), {"error": "x"}, {"error": "x"}, history)
    assert learned["calibration"]["applied"]
    monkeypatch.setattr(th, "HALTS", {"00662": tuple(((d - dt.timedelta(days=1)).isoformat(), d.isoformat()) for d in dates)})
    skipped, _, _ = mr.calibrate_predictions(dict(fair), {"error": "x"}, {"error": "x"}, history)
    assert not skipped["calibration"]["applied"] and skipped["fair_price"] == 99.0


def test_fx_accumulates_on_its_own_when_us_markets_were_also_closed():
    """外審 PR #59:台美同天休市(美股只有一個新收盤),匯率仍有多筆觀測 → 匯率照樣累計。"""
    qqq = _series({"2026-12-30": 600.0, "2026-12-31": 605.0})
    fx = _series({"2026-12-30": 32.0, "2027-01-01": 32.4, "2027-01-02": 32.5})
    prev, pct, fx_prev, fx_pct = th.rebase(qqq, fx, D(2026, 12, 31), 605.0, 600.0, 605 / 600 - 1, 32.5, 32.4, 32.5 / 32.4 - 1)
    assert (prev, round(pct, 6)) == (600.0, round(605 / 600 - 1, 6))   # 美股照舊
    assert fx_prev == 32.0 and round(fx_pct, 6) == round(32.5 / 32.0 - 1, 6)


def test_2330_keeps_the_cumulative_adr_move_when_fx_history_fails(monkeypatch):
    days = pd.bdate_range("2026-08-03", "2026-11-16")
    tsm = pd.DataFrame({"Close": [300.0 + i for i in range(len(days))]}, index=days)
    hist = pd.DataFrame({"Open": [2000.0] * len(days), "Close": [2000.0] * len(days)}, index=days)

    def ticker(sym):
        if sym == "TWD=X":
            raise RuntimeError("fx down")
        return _Hist(tsm)
    monkeypatch.setattr(mr.yf, "Ticker", ticker)
    monkeypatch.setattr(mr, "fetch_twse_close", lambda code: None)
    by_day = dict(zip(days.date, tsm["Close"]))
    close, prev = by_day[D(2026, 11, 16)], by_day[D(2026, 11, 13)]
    after = mr.calc_2330_predictions(close, prev, 32.0, hist, since_trade=D(2026, 11, 11))
    assert after["tsm_pct"] == round((close / by_day[D(2026, 11, 10)] - 1) * 100, 2)
    assert after["model1_1to1"] == round(2000.0 * close / by_day[D(2026, 11, 10)], 2)
