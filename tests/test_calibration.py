"""calibrate_predictions 測試：bias 修正 + 三模型 MAE 反比加權。"""
import pandas as pd

import morning_report as mr


def _open_df(dates, value):
    """產生只有 Open 欄位的歷史 DataFrame（模擬 yfinance）。"""
    return pd.DataFrame({"Open": [value] * len(dates)}, index=pd.DatetimeIndex(dates))


def _hist_dates(n):
    return [d.strftime("%Y-%m-%d")
            for d in pd.date_range("2026-04-01", periods=n, freq="B")]


def test_calibration_insufficient_history():
    fair = {"fair_price": 120.0, "last_00662_price": 118.0}
    preds = {"model1_1to1": 1100.0, "model2_regression": 1090.0,
             "model3_adr_decay": 1095.0, "mid": 1095.0}
    taiex = {"pred_open": 23000.0}
    f, p, t = mr.calibrate_predictions(fair, preds, taiex, history=[{"date": "2026-05-01"}])
    assert f["calibration"]["applied"] is False
    assert p["calibration"]["applied"] is False
    assert t["calibration"]["applied"] is False
    # 原值不變
    assert f["fair_price"] == 120.0


def test_calibration_applies_positive_bias(fake_yf):
    # 歷史上預測都低估 1%（實際開盤一律高 1%）→ 今日預測應被上修約 1%
    dates = _hist_dates(12)
    all_dates = pd.date_range("2026-04-01", periods=20, freq="B")
    fake_yf({
        "^TWII": _open_df(all_dates, 20200.0),
        "2330.TW": _open_df(all_dates, 1010.0),
        "00662.TW": _open_df(all_dates, 101.0),
    })
    history = [{
        "date": d,
        "fair_00662": 100.0,
        "model1_2330": 1000.0, "model2_2330": 1000.0, "model3_2330": 1000.0,
        "weighted_final_2330": 1000.0,
        "pred_taiex": 20000.0,
    } for d in dates]

    fair = {"fair_price": 120.0, "last_00662_price": 118.0, "implied_change_pct": 1.7}
    preds = {"model1_1to1": 1100.0, "model2_regression": 1090.0,
             "model3_adr_decay": 1095.0, "mid": 1095.0, "range": (1090.0, 1100.0)}
    taiex = {"pred_open": 23000.0}

    f, p, t = mr.calibrate_predictions(fair, preds, taiex, history)

    assert f["calibration"]["applied"] is True
    assert f["calibration"]["bias_pct"] > 0
    assert f["fair_price"] > 120.0 and f["fair_price_raw"] == 120.0
    assert t["calibration"]["applied"] is True
    assert t["pred_open"] > 23000.0
    # 2330：三模型都有足夠樣本 → 應走 MAE 加權
    assert p["final_method"] == "近期 MAE 反比加權"
    assert p["weighted_final"] is not None
    assert p["mid"] == p["weighted_final"]   # mid 同步成校正後最終值


def test_model_weighting_favours_accurate_model(fake_yf):
    # model1 歷史很準、model3 很不準 → weighted_final 應明顯偏向 model1 的今日值
    dates = _hist_dates(12)
    all_dates = pd.date_range("2026-04-01", periods=20, freq="B")
    fake_yf({
        "^TWII": _open_df(all_dates, 20000.0),
        "2330.TW": _open_df(all_dates, 1000.0),
        "00662.TW": _open_df(all_dates, 100.0),
    })
    history = [{
        "date": d,
        "fair_00662": 100.0,
        "model1_2330": 999.0,    # 誤差 ~0.1%
        "model2_2330": 990.0,    # 誤差 ~1%
        "model3_2330": 970.0,    # 誤差 ~3%
        "weighted_final_2330": 1000.0,
        "pred_taiex": 20000.0,
    } for d in dates]

    fair = {"fair_price": 100.0, "last_00662_price": 100.0}
    preds = {"model1_1to1": 1100.0, "model2_regression": 1200.0,
             "model3_adr_decay": 1300.0, "mid": 1200.0, "range": (1100.0, 1300.0)}
    taiex = {"pred_open": 20000.0}

    f, p, t = mr.calibrate_predictions(fair, preds, taiex, history)

    mae = p["model_mae_pct"]
    assert mae["model1"] < mae["model2"] < mae["model3"]
    # 加權結果應比中位數(1200)更靠近準確的 model1(1100)
    assert abs(p["weighted_final"] - 1100.0) < abs(p["weighted_final"] - 1300.0)


def test_calibration_skips_error_dicts(fake_yf):
    dates = _hist_dates(8)
    all_dates = pd.date_range("2026-04-01", periods=15, freq="B")
    fake_yf({
        "^TWII": _open_df(all_dates, 20000.0),
        "2330.TW": _open_df(all_dates, 1000.0),
        "00662.TW": _open_df(all_dates, 100.0),
    })
    history = [{"date": d, "fair_00662": 100.0, "pred_taiex": 20000.0} for d in dates]
    fair = {"error": "QQQ 抓取失敗"}
    preds = {"error": "TSM 抓取失敗"}
    taiex = {"pred_open": 23000.0}
    f, p, t = mr.calibrate_predictions(fair, preds, taiex, history)
    # error dict 不應被加上 calibration 也不應崩潰
    assert f == {"error": "QQQ 抓取失敗"}
    assert p == {"error": "TSM 抓取失敗"}
    assert "calibration" in t
