"""calc_00662_fair_value 測試：fallback / 歷史回歸 / 缺昨收。"""
import numpy as np
import pandas as pd

import morning_report as mr


def _regression_data(mkdf):
    """產生 QQQ / 00662.TW / TWD=X 三條對齊的歷史，讓歷史回歸路徑真的被走到。"""
    n = 80
    idx = pd.date_range("2026-01-06", periods=n, freq="B")
    rng = np.random.default_rng(42)
    rets = rng.normal(0, 0.015, n)
    # 確保每天 |漲跌| > 0.4%，才會被 |qqq_lag_pct| > 0.003 的篩選保留
    rets = np.where(np.abs(rets) < 0.004, 0.004 * np.sign(rets + 1e-9), rets)
    qqq = 500.0 * np.cumprod(1 + rets)
    # 00662 ≈ 0.9 倍 QQQ「前一日」漲跌幅
    tw = np.empty(n)
    tw[0] = tw[1] = 100.0
    for t in range(2, n):
        qqq_lag_pct = qqq[t - 1] / qqq[t - 2] - 1
        tw[t] = tw[t - 1] * (1 + 0.9 * qqq_lag_pct)
    fx = 31.0 + rng.normal(0, 0.02, n)
    return {
        "QQQ": mkdf(qqq, idx),
        "00662.TW": mkdf(tw, idx),
        "TWD=X": mkdf(fx, idx),
    }


def test_fair_value_missing_last_price(fake_yf):
    fake_yf({})
    res = mr.calc_00662_fair_value(520.0, 515.0, 31.0, None)
    assert res.get("error")


def test_fair_value_fallback_when_history_missing(fake_yf):
    # yf.Ticker 回傳空 DataFrame → samples 維持 0 → 走簡化版
    fake_yf({})
    res = mr.calc_00662_fair_value(520.0, 515.0, 31.0, 118.0, usdtwd_prev=31.1)
    assert res["samples"] == 0
    assert "簡化版" in res["method"]
    # 簡化版：fair = last * (1 + qqq_pct + fx_pct)
    qqq_pct = (520.0 - 515.0) / 515.0
    fx_pct = (31.0 - 31.1) / 31.1
    assert res["fair_price"] == round(118.0 * (1 + qqq_pct + fx_pct), 2)


def test_fair_value_regression_when_history_available(fake_yf, mkdf):
    fake_yf(_regression_data(mkdf))
    res = mr.calc_00662_fair_value(520.0, 515.0, 31.0, 118.0, usdtwd_prev=31.0)
    assert res["samples"] >= 15
    assert "歷史回歸" in res["method"]
    assert 0.5 <= res["beta"] <= 1.5
    assert isinstance(res["fair_price"], float)
    # 回歸版的關鍵欄位都要在
    for k in ("qqq_pct", "fx_pct", "avg_deviation_pct", "implied_change_pct"):
        assert k in res
