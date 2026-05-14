"""calc_taiex_prediction 測試：三訊號加權 / 缺訊號 reweight / 全缺 error。"""
import numpy as np


def _hist(mkdf):
    return mkdf(np.linspace(22000, 23000, 30))


def test_three_signals(fake_yf, mkdf):
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.2, tsm_pct=0.8, night_pct=0.5)
    assert res["signal_count"] == 3
    assert len(res["signals"]) == 3
    assert res["pred_open"] > 0
    assert res["ci_lower"] <= res["pred_open"] <= res["ci_upper"]


def test_reweight_when_night_missing(fake_yf, mkdf):
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.0, tsm_pct=1.0, night_pct=None)
    # 夜盤缺 → 只剩兩個訊號，權重自動重新分配
    assert res["signal_count"] == 2
    names = {s["name"] for s in res["signals"]}
    assert "Night_TXF" not in names
    assert res["pred_open"] > 0


def test_all_signals_missing_returns_error(fake_yf, mkdf):
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=None, tsm_pct=None, night_pct=None)
    assert res.get("error")


def test_missing_history_returns_error():
    import morning_report as mr
    assert mr.calc_taiex_prediction(None, 1.0, 1.0, 1.0).get("error")


def test_consensus_all_bullish(fake_yf, mkdf):
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.5, tsm_pct=1.0, night_pct=0.8)
    assert "偏多" in res["consensus"]
    assert res["signal_std"] is not None
