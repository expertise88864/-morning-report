"""calc_2330_predictions 三模型測試：model1 / model2 比值回歸 / model3 ADR 衰減 / 缺歷史。"""
import numpy as np
import pandas as pd

import morning_report as mr


def _aligned_data(mkdf, n=60):
    """產生對齊的 TSM / TWD=X 歷史與 2330 歷史，讓 model2 / model3 路徑被走到。"""
    idx = pd.date_range("2026-02-02", periods=n, freq="B")
    rng = np.random.default_rng(7)
    tsm_rets = rng.normal(0, 0.018, n)
    # 確保每天 |漲跌| > 1.1%，model3 的 |tsm_pct| > 1% 篩選才有足夠樣本
    tsm_rets = np.where(np.abs(tsm_rets) < 0.011,
                        0.012 * np.sign(tsm_rets + 1e-9), tsm_rets)
    tsm = 200.0 * np.cumprod(1 + tsm_rets)
    fx = 31.0 + rng.normal(0, 0.03, n)
    # 2330 理論價 ≈ TSM × FX ÷ 5，加一點雜訊
    t2330 = tsm * fx / 5.0 * (1 + rng.normal(0, 0.01, n))
    return idx, {
        "TSM": mkdf(tsm, idx),
        "TWD=X": mkdf(fx, idx),
    }, mkdf(t2330, idx)


def test_missing_hist_returns_error(fake_yf):
    fake_yf({})
    res = mr.calc_2330_predictions(220.0, 218.0, 31.0, None)
    assert res.get("error")


def test_model1_one_to_one(fake_yf, mkdf):
    # TSM / TWD=X 抓不到 → model2 為 None，但 model1 一定要算出來
    fake_yf({})
    idx = pd.date_range("2026-02-02", periods=30, freq="B")
    hist_2330 = mkdf(np.linspace(1000, 1080, 30), idx)
    last_2330 = float(hist_2330.iloc[-1]["Close"])
    tsm_close, tsm_prev = 220.0, 215.0
    res = mr.calc_2330_predictions(tsm_close, tsm_prev, 31.0, hist_2330)
    tsm_pct = (tsm_close - tsm_prev) / tsm_prev
    assert res["model1_1to1"] == round(last_2330 * (1 + tsm_pct), 2)
    assert res["model2_regression"] is None


def test_model2_regression(fake_yf, mkdf):
    idx, yf_data, hist_2330 = _aligned_data(mkdf, n=60)
    fake_yf(yf_data)
    res = mr.calc_2330_predictions(220.0, 215.0, 31.0, hist_2330)
    assert res["model2_regression"] is not None
    assert res["model2_regression"] > 0


def test_model3_adr_decay(fake_yf, mkdf):
    idx, yf_data, hist_2330 = _aligned_data(mkdf, n=60)
    fake_yf(yf_data)
    res = mr.calc_2330_predictions(220.0, 215.0, 31.0, hist_2330)
    assert res["model3_adr_decay"] is not None
    assert 0.3 <= res["decay_factor"] <= 1.2
    # 三模型齊備時應有中位數與區間
    assert "mid" in res and "range" in res


def test_model4_momentum_added(fake_yf, mkdf):
    """model4 momentum 在 hist_2330 有 ≥6 天資料時應計算出來。"""
    idx, yf_data, hist_2330 = _aligned_data(mkdf, n=60)
    fake_yf(yf_data)
    res = mr.calc_2330_predictions(220.0, 215.0, 31.0, hist_2330)
    assert res["model4_momentum"] is not None
    # 5 日動能 % 也記在 res 內
    assert res["momentum_5d_pct"] is not None
    # range 與 mid 來自四個模型
    assert "mid" in res and "range" in res
