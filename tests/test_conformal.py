"""Conformal-PID 區間校準(借鏡 Angelopoulos Conformal PID Control)測試。"""
import morning_report as mr


def test_update_conformal_q_widens_when_undercovered():
    """覆蓋率 < 80% → 區間太窄 → q 增(加寬)。"""
    q = mr._update_conformal_q(0.0, 70.0)            # 差 10pp
    assert q > 0
    assert abs(q - mr.CONFORMAL_LR * (80.0 - 70.0) / 100.0) < 1e-9


def test_update_conformal_q_narrows_when_overcovered():
    """覆蓋率 > 80% → 區間太寬 → q 減(收窄)。"""
    assert mr._update_conformal_q(0.0, 92.0) < 0


def test_update_conformal_q_none_coverage_unchanged():
    assert mr._update_conformal_q(1.23, None) == 1.23


def test_update_conformal_q_clamped():
    assert mr._update_conformal_q(99.0, 0.0) == mr.CONFORMAL_Q_HI     # 上限
    assert mr._update_conformal_q(-99.0, 100.0) == mr.CONFORMAL_Q_LO  # 下限


def test_compute_conformal_adjustments_per_key(monkeypatch):
    monkeypatch.setattr(mr, "_load_conformal_state", lambda: {"1d_open": 0.0, "5d": 1.0})
    wf = {
        "1d_open": {"interval_coverage_pct": 60.0},   # 嚴重不足 → 加寬
        "5d": {"interval_coverage_pct": 95.0},        # 過高 → 收窄
        "1d_close": {},                               # 無覆蓋率 → 不動(從 0)
    }
    adj = mr.compute_conformal_adjustments(wf, save=False)
    assert adj["1d_open"] > 0
    assert adj["5d"] < 1.0                            # 從 1.0 收窄
    assert adj["1d_close"] == 0.0
    assert set(adj) == set(mr.MODEL_TARGETS)


def _entry():
    return {"close": 1000.0, "daily_vol_pct": 2.0, "attention_score": 50,
            "news_catalyst_score": 0, "pct_5d": 0.0}


def test_forecast_band_widens_with_positive_conformal():
    base = mr.calc_stock_price_forecast(_entry())
    wide = mr.calc_stock_price_forecast(_entry(), conformal_adj={k: 3.0 for k in mr.MODEL_TARGETS})
    for k in ("1d_open", "5d"):
        assert wide[k]["interval_pct"] > base[k]["interval_pct"]
        assert wide[k]["conformal_adj_pct"] == 3.0
        assert base[k]["conformal_adj_pct"] == 0.0


def test_forecast_band_narrows_with_negative_conformal():
    base = mr.calc_stock_price_forecast(_entry())
    narrow = mr.calc_stock_price_forecast(_entry(), conformal_adj={k: -1.5 for k in mr.MODEL_TARGETS})
    # band 有 1.5 下限,挑波動够大的 5d 確認收窄生效
    assert narrow["5d"]["interval_pct"] < base["5d"]["interval_pct"]
