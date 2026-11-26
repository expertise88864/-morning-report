"""2026-11-26 使用者同意:00662 公允價不再做 EMA 偏差修正(119 日回測:MAE 0.758% → 0.808%,t=2.47)。"""
import ast
import inspect

import pandas as pd

import morning_report as mr


def test_00662_fair_price_is_not_bias_corrected(fake_yf):
    days = pd.date_range("2026-04-01", periods=20, freq="B")
    fake_yf({"^TWII": pd.DataFrame({"Open": [20000.0] * 20}, index=days),
             "2330.TW": pd.DataFrame({"Open": [1000.0] * 20}, index=days),
             "00662.TW": pd.DataFrame({"Open": [103.0] * 20}, index=days)})   # 實際一路比預測高 3%
    history = [{"date": d.date().isoformat(), "fair_00662": 100.0, "pred_taiex": 20000.0} for d in days[:12]]
    fair = {"fair_price": 101.0, "last_00662_price": 100.0, "implied_change_pct": 1.0}
    f, _, t = mr.calibrate_predictions(fair, {"error": "x"}, {"pred_open": 20000.0, "last_close": 19900.0}, history)
    assert f["calibration"]["applied"] is False
    assert f["fair_price"] == 101.0 and f["implied_change_pct"] == 1.0 and "fair_price_raw" not in f
    assert "calibration" in t   # 加權指數的修正不受影響(回測不顯著,維持)


def test_00662_rule_version_is_recorded(fake_yf):
    fake_yf({})
    assert mr.FAIR_00662_MODEL_VERSION == 2 and mr.FAIR_00662_BIAS is False
    res = mr.calc_00662_fair_value(500.0, 495.0, 32.0, 118.0, usdtwd_prev=32.0)
    assert res["model_version"] == 2
    tree = ast.parse(inspect.getsource(mr._phase_render))
    keys = {k.value for n in ast.walk(tree) if isinstance(n, ast.Dict) for k in n.keys if isinstance(k, ast.Constant)}
    assert {"fair_00662", "model_00662_version"} <= keys
