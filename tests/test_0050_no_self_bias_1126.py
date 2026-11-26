"""2026-11-26 使用者同意:0050 不再做自身的殘差校正(120 日回測:重複修正讓 MAE 0.951% → 1.007%)。"""
import test_us_session_integration as us

import morning_report as mr


def test_the_0050_phase_does_not_apply_its_own_bias(monkeypatch):
    q = us.quotes()
    q["TSM"].update(stale=True, change_pct=10, date="2026-10-07")
    q["MACRO"] = {"SOX": {"change_pct": 0}}
    us._phase3_offline(monkeypatch, {})
    monkeypatch.setattr(mr, "calibrate_0050_bias", lambda p, h: (_ for _ in ()).throw(AssertionError("不該再呼叫")))
    monkeypatch.setattr(mr, "load_history_state", lambda **k: [{"date": "2026-10-01", "pred_0050": 90.0}] * 20)
    ctx = us._phase3_ctx(q)
    mr._phase_taifex_and_chips(ctx)
    assert ctx.tw0050_pred["pred_open"] == 100. and "calibration" not in ctx.tw0050_pred
    assert ctx.tw0050_pred["model_version"] == mr.TW0050_MODEL_VERSION == 2


def test_history_rows_carry_the_0050_rule_version():
    """外審 PR #67:新舊規則寫在同一個 `pred_0050` 欄位,要有版本才分得開(缺值 = v1)。"""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(mr._phase_render))
    keys = {k.value for n in ast.walk(tree) if isinstance(n, ast.Dict) for k in n.keys if isinstance(k, ast.Constant)}
    assert {"pred_0050", "model_0050_version"} <= keys
    assert mr.calc_0050_prediction(100.0, {}, {"weighted_pct": 1.0})["model_version"] == 2
    assert "model_version" not in mr.calc_0050_prediction(None, {}, {})
