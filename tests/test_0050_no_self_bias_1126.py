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
