"""Absorption Ratio 系統性風險早警 + regime/alert 整合測試。"""
import numpy as np

import morning_report as mr


def _mh_from_returns(rets: np.ndarray, start_price: float = 100.0) -> list[dict]:
    """由日報酬矩陣 (days, M) 建出 model_history 快照(每檔有 close)。"""
    prices = start_price * np.cumprod(1.0 + rets, axis=0)
    days, m = prices.shape
    codes = [f"{1000 + i}" for i in range(m)]
    mh = []
    for d in range(days):
        # 月/日滾動,字典序與時間序一致(每月最多 28 天)
        date = f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}"
        mh.append({"session_date": date,
                   "stocks": {codes[i]: {"close": float(prices[d, i])} for i in range(m)}})
    return mh


def test_absorption_insufficient_history_returns_empty():
    assert mr.calc_absorption_ratio([]) == {}
    short = _mh_from_returns(np.random.RandomState(1).normal(0, 0.02, (30, 25)))
    assert mr.calc_absorption_ratio(short) == {}            # < window+short_win+5


def test_absorption_high_when_returns_share_common_factor():
    """單一共同因子主導 → 前幾主成分吃下幾乎全部變異 → AR 接近 1。"""
    rng = np.random.RandomState(0)
    days, m = 60, 25
    factor = rng.normal(0, 0.02, (days, 1))
    rets = factor + rng.normal(0, 0.001, (days, m))         # 共同因子 >> 個股雜訊
    ar = mr.calc_absorption_ratio(_mh_from_returns(rets), window=20, short_win=10)
    assert ar and ar["ar"] > 0.7
    assert ar["n_assets"] == 25 and ar["n_factors"] >= 1


def test_absorption_lower_when_returns_independent():
    """獨立報酬 → 變異分散在多主成分 → AR 明顯低於共同因子情境。"""
    rng = np.random.RandomState(2)
    days, m = 60, 25
    corr = mr.calc_absorption_ratio(
        _mh_from_returns(rng.normal(0, 0.02, (days, 1)) + rng.normal(0, 0.001, (days, m))),
        window=20, short_win=10)
    indep = mr.calc_absorption_ratio(
        _mh_from_returns(rng.normal(0, 0.02, (days, m))), window=20, short_win=10)
    assert indep and corr
    assert indep["ar"] < corr["ar"]


def test_absorption_shift_flags_fragile_on_regime_compression():
    """前段獨立、後段忽然高度相關 → 近期 AR 急升 → ΔAR_z>0 且觸發 fragile。"""
    rng = np.random.RandomState(3)
    days, m = 70, 25
    rets = rng.normal(0, 0.02, (days, m))                   # 前段獨立
    factor = rng.normal(0, 0.03, (days, 1))
    rets[40:] = factor[40:] + rng.normal(0, 0.001, (days, m))[40:]   # 後段壓縮
    ar = mr.calc_absorption_ratio(_mh_from_returns(rets), window=20, short_win=10)
    assert ar and ar["ar_shift_z"] > 0
    assert ar["fragile"] is True


def test_market_regime_risk_off_on_severe_absorption():
    base = {"MACRO": {"VIX": {"close": 15}, "SOX": {"change_pct": 0.5}},
            "BREADTH": {"advance_ratio": 55}}
    assert mr._market_regime({**base, "ABSORPTION": {"severe": True}}) == "risk_off"
    assert mr._market_regime({**base, "ABSORPTION": {"severe": False}}) == "neutral"
    assert mr._market_regime(base) == "neutral"             # 無 ABSORPTION 不影響


def test_alert_fires_only_when_absorption_fragile():
    fragile = {"ABSORPTION": {"ar_shift_z": 1.6, "fragile": True, "severe": False,
                              "ar": 0.91, "n_assets": 50, "n_factors": 10}}
    titles = [a["title"] for a in mr.detect_market_alerts(fragile, {}, {}, {})]
    assert any("系統性風險" in t for t in titles)
    calm = {"ABSORPTION": {"ar_shift_z": 0.3, "fragile": False}}
    assert not any("系統性風險" in a["title"]
                   for a in mr.detect_market_alerts(calm, {}, {}, {}))
