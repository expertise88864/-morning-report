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


def test_bases_use_the_us_close_that_drove_the_last_trade():
    qqq = _series({"2026-11-06": 600.0, "2026-11-09": 610.0, "2026-11-10": 590.0, "2026-11-16": 560.0})
    fx = _series({"2026-11-09": 32.0, "2026-11-10": 32.1, "2026-11-16": 32.5})
    # 11/10 台股收盤反映的是美東 11/09 收盤 → 基準 610;匯率取 11/10
    assert th.bases(qqq, fx, D(2026, 11, 10), 570.0, 32.4) == (610.0, 32.1)
    assert th.bases(pd.Series(dtype=float), None, D(2026, 11, 10), 570.0, 32.4) == (570.0, 32.4)


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
