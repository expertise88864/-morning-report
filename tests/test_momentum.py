"""calc_momentum_metrics + calc_midterm_forecast + 過熱/超賣 alert 測試。"""
import numpy as np
import pandas as pd

import morning_report as mr


def _close_series(values):
    idx = pd.date_range("2026-01-05", periods=len(values), freq="B")
    return pd.Series(values, index=idx)


def test_momentum_metrics_insufficient_history():
    """< 6 天無法算 5d 動能 → 回空 dict。"""
    s = _close_series([100.0, 101.0, 102.0])
    assert mr.calc_momentum_metrics(s) == {}


def test_momentum_metrics_with_25_days():
    # 25 天線性上升,5 日累積應 > 0,MA20 應低於最新價
    values = list(np.linspace(100.0, 110.0, 25))
    s = _close_series(values)
    m = mr.calc_momentum_metrics(s)
    assert m["last"] > 0
    assert m["pct_5d"] is not None and m["pct_5d"] > 0
    assert m["pct_20d"] is not None and m["pct_20d"] > 0
    assert m["ma20"] is not None
    assert m["ma20_dist_pct"] is not None and m["ma20_dist_pct"] > 0    # 最新價 > MA20
    assert m["daily_vol_pct"] is not None and m["daily_vol_pct"] >= 0


def test_midterm_forecast_band_scales_with_sqrt_horizon():
    """1 月區間應該大於 1 週區間(σ ∝ √h)。"""
    # 構造 21 天波動 ~1% daily
    np.random.seed(42)
    values = 100 * np.cumprod(1 + np.random.normal(0, 0.01, 25))
    s = _close_series(values.tolist())
    m = mr.calc_momentum_metrics(s)
    fc = mr.calc_midterm_forecast(m, horizons=(5, 20))
    # band 應該擴大
    assert fc["20d"]["band_pct"] > fc["5d"]["band_pct"]
    # 區間應 lower < upper
    assert fc["5d"]["lower"] < fc["5d"]["upper"]
    assert fc["20d"]["lower"] < fc["20d"]["upper"]


def test_midterm_forecast_error_when_insufficient():
    assert mr.calc_midterm_forecast({}).get("error")
    assert mr.calc_midterm_forecast({"last": 100}).get("error")    # 缺 vol


def test_trend_label():
    assert mr._trend_label({"ma20_dist_pct": 6.0}).startswith("強勢")
    assert mr._trend_label({"ma20_dist_pct": 3.0}) == "上行"
    assert mr._trend_label({"ma20_dist_pct": 0}) == "盤整"
    assert mr._trend_label({"ma20_dist_pct": -3.0}) == "下行"
    assert mr._trend_label({"ma20_dist_pct": -6.0}).startswith("弱勢")
    assert mr._trend_label({}) == "—"


def test_overheat_alert_triggers():
    """5 日累積 > +5% 或 < -5% 應觸發 orange 警示。"""
    quotes = {
        "MACRO": {},
        "MIDTERM": {
            "2330": {"metrics": {"pct_5d": 7.0, "ma20_dist_pct": 4.0}, "trend": "上行"},
            "00662": {"metrics": {"pct_5d": -6.5, "ma20_dist_pct": -4.5}, "trend": "下行"},
            "0050": {"metrics": {"pct_5d": 2.0, "ma20_dist_pct": 1.0}, "trend": "上行"},
        },
    }
    alerts = mr.detect_market_alerts(quotes, {}, {}, {})
    titles = [a["title"] for a in alerts]
    assert any("2330 短期過熱" in t for t in titles)
    assert any("00662 短期超賣" in t for t in titles)
    # 0050 在 ±5% 內,不應觸發
    assert not any("0050" in t for t in titles)
