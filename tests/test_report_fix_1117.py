"""11-17 實信:00662 1 拆 5 沒還原;今日一圖不夠極端也畫;美股財報日期比公告晚一天。"""
import datetime as dt

import pandas as pd

import corporate_splits as cs
import daily_chart as dc
import morning_report as mr
import us_earnings as ue

TPE = dt.timezone(dt.timedelta(hours=8))


def _yahoo_00662():
    """11-17 實測的 Yahoo 00662.TW:11/09、11/10 已改成新刻度,更早的仍是舊刻度;停牌那幾天量 0。"""
    days = ["2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-16", "2026-11-17"]
    close = [118.75, 113.35, 22.88, 23.56, 23.56, 23.56, 23.07]
    idx = pd.DatetimeIndex(pd.to_datetime(days)).tz_localize("Asia/Taipei")
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": [1, 1, 5, 5, 0, 0, 5]}, index=idx)


def test_split_restores_only_the_old_scale_rows():
    out = cs.frame("00662.TW", _yahoo_00662())
    assert [round(v, 2) for v in out["Close"]] == [23.75, 22.67, 22.88, 23.56, 23.56, 23.56, 23.07]
    assert list(out["Volume"]) == [1, 1, 5, 5, 0, 0, 5]                       # 成交量不動
    now = "2026-11-17"
    assert cs.value("00662", "2026-11-16", 116.18, now) == 116.18 / 5          # 停牌期間的舊刻度預測
    assert cs.value("00662", "2026-11-17", 22.25, now) == 22.25                # 新刻度不動
    assert cs.value("00662", "2026-11-09", 22.88, now) == 22.88                # Yahoo 已改過的不再除
    assert cs.value("00662", "2026-07-14", 120.0, "2026-07-16") == 120.0       # 兩邊都在分割前:同刻度,不動
    assert cs.value("0050", "2026-01-01", 190.0, now) == 190.0 and cs.value("00662", "2026-11-16", None, now) is None
    early = _yahoo_00662().iloc[:2]                                           # 序列沒跨過分割日:不動
    assert list(cs.frame("00662.TW", early)["Close"]) == [118.75, 113.35]
    assert cs.series("00662", {"2026-11-06": 113.35, "2026-11-17": 23.07}) == {"2026-11-06": 113.35 / 5, "2026-11-17": 23.07}
    assert cs.frame("0050.TW", _yahoo_00662()) is not None

    class _YF:
        class Ticker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, **_):
                return _yahoo_00662()
    assert round(cs.history(_YF, "00662.TW", period="3mo")["Close"].iloc[0], 2) == 23.75


def test_prediction_delta_compares_on_the_same_scale():
    hist = [{"date": "2026-11-16", "fair_00662": 116.18, "pred_0050": 102.66}]
    note = mr._prediction_delta_note(hist, "2026-11-17", {"00662": 22.25, "0050": 95.18})
    assert "00662 -4.24%" in note and "0050 -7.29%" in note and "-80" not in note


def _rows(values, key="vix"):
    start = dt.date(2026, 9, 1)
    return [{"date": (start + dt.timedelta(days=i)).isoformat(), key: v} for i, v in enumerate(values)]


def test_daily_chart_only_when_today_is_a_20_day_high_or_low():
    base = [20.0 + (i % 5) * 0.5 for i in range(40)]
    assert dc.pick(_rows(base + [23.0]))["key"] == "vix"          # 新高:畫
    assert dc.pick(_rows(base + [19.5]))["key"] == "vix"          # 新低:畫
    assert dc.pick(_rows(base + [21.7])) == {}                    # 區間中間:不畫(即使偏離不小)


def test_us_earnings_use_the_exact_time_and_convert_to_taipei():
    class _YF:
        class Ticker:
            def __init__(self, symbol):
                self.symbol = symbol

            @property
            def calendar(self):
                return {"Earnings Date": [dt.date(2026, 11, 18)]} if self.symbol in ("NVDA", "AMD") else {}

            def get_earnings_dates(self, limit=4):
                if self.symbol == "AMD":
                    raise RuntimeError("scrape blocked")
                idx = pd.DatetimeIndex([pd.Timestamp("2026-11-17 16:00", tz="America/New_York"),
                                        pd.Timestamp("2026-08-26 16:00", tz="America/New_York")])
                return pd.DataFrame({"EPS Estimate": [2.47, 2.09]}, index=idx)
    rows = ue.events(_YF, dt.date(2026, 11, 17), dt.date(2026, 11, 24), TPE)
    nvda = next(r for r in rows if r["title"] == "NVDA 財報")
    assert (nvda["date"], nvda["time"], nvda["note"]) == (dt.date(2026, 11, 18), "05:00", "美東 11/17（二）盤後公布")
    amd = next(r for r in rows if r["title"] == "AMD 財報")
    assert (amd["date"], amd["time"], amd["note"]) == (dt.date(2026, 11, 18), "盤後(美東)", "日期待確認")
    assert ue.events(_YF, dt.date(2026, 12, 1), dt.date(2026, 12, 8), TPE) == []
