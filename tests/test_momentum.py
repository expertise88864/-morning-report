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


def test_ewma_vol_insufficient_returns_none():
    assert mr._ewma_vol_pct([0.01] * 5) is None      # < 10 筆
    assert mr._ewma_vol_pct([]) is None


def test_ewma_vol_reacts_to_recent_spike():
    """EWMA 應對『近期波動放大』敏感:結尾有大跳動的序列 EWMA 明顯高於全程平靜序列。"""
    calm = [0.001] * 60
    spike = [0.001] * 59 + [0.06]            # 最後一天 +6% 大跳動
    v_calm = mr._ewma_vol_pct(calm)
    v_spike = mr._ewma_vol_pct(spike)
    assert v_calm is not None and v_spike is not None
    assert v_spike > v_calm * 3              # 近期 spike 把條件波動度顯著拉高
    assert v_spike > 0


def test_ewma_vol_constant_returns_converge():
    """常數報酬 r 的 EWMA 波動度應收斂到 |r|(此處 1% → ~1.0)。"""
    v = mr._ewma_vol_pct([0.01] * 300)
    assert v is not None and abs(v - 1.0) < 0.05


def test_momentum_metrics_includes_ewma_vol():
    np.random.seed(7)
    values = 100 * np.cumprod(1 + np.random.normal(0, 0.012, 80))
    m = mr.calc_momentum_metrics(_close_series(values.tolist()))
    assert m.get("ewma_vol_pct") is not None and m["ewma_vol_pct"] > 0


def test_midterm_forecast_prefers_ewma_then_falls_back():
    # 有 ewma_vol_pct → 用 EWMA;band 依該 σ 計算
    fc = mr.calc_midterm_forecast(
        {"last": 100.0, "daily_vol_pct": 1.0, "ewma_vol_pct": 2.0, "pct_20d": 0.0},
        horizons=(5,))
    assert fc["5d"]["vol_basis"] == "EWMA"
    assert fc["5d"]["band_1s_pct"] == round(2.0 * (5 ** 0.5), 2)      # 用 2.0 不是 1.0
    # 無 ewma → 退回 20d-std
    fc2 = mr.calc_midterm_forecast(
        {"last": 100.0, "daily_vol_pct": 1.0, "pct_20d": 0.0}, horizons=(5,))
    assert fc2["5d"]["vol_basis"] == "20d-std"
    assert fc2["5d"]["band_1s_pct"] == round(1.0 * (5 ** 0.5), 2)


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
