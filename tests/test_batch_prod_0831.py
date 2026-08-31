# -*- coding: utf-8 -*-
"""2026-08-31 實信 + 架構外審:SLA 可稽核性與品質判準的完成競態。

08/31 那班 `date=2026-08-31 08:30`、`total_seconds=2088` —— 信其實
**09:05 才寄出**,而 state 看起來像「08:30 成功」。使用者前一天才定案
「09:00 前必到」,但系統當時**沒有任何欄位能稽核那句話**。
"""
import datetime as dt
import io
import json
from pathlib import Path

import morning_report as mr
import run_quality as rq

_ROOT = Path(mr.__file__).resolve().parent


def test_the_manifest_records_when_the_letter_actually_went_out(
        tmp_path, monkeypatch):
    """`date` 是**開跑時刻**(同日冪等需要它,跨午夜不能記成隔天),
    SLA 需要的是**寄出的那一刻** —— 兩件事,兩個欄位。"""
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"date": "2026-08-31 08:30", "delivery": {}}),
                 encoding="utf-8")
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", m)
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(mr, "_RUN_STAMP", "")
    mr._set_run_stamp(dt.datetime(2026, 8, 31, 8, 30, tzinfo=mr.TPE))
    mr._mark_delivery_in_manifest(attempted=True, success=True)
    d = json.loads(m.read_text(encoding="utf-8"))["delivery"]
    assert d.get("delivered_at"), d
    # 開跑戳不會被寄出時刻蓋掉(它還要給同日冪等用)
    assert json.loads(m.read_text(encoding="utf-8"))["date"] == "2026-08-31 08:30"
    # 寄出時刻不繼承上一班的
    assert dt.datetime.fromisoformat(d["delivered_at"]).date() == \
        dt.datetime.now(mr.TPE).date()


def test_a_letter_after_nine_is_a_defect_not_a_silent_success():
    """使用者的 SLA:信可以晚到,但台股 09:00 開盤前必須到。
    宣告了期限卻沒有判準能稽核,那條 SLA 就只是一句話。"""
    def _codes(at):
        return {f["code"] for f in rq.assess({
            "date": "2026-08-31 08:30",
            "delivery": {"success": True, "delivered_at": at},
            "llm": {"analysis_origin": "luna_specialized"}})}
    # 08/31 實際:09:05:34
    late = _codes("2026-08-31T09:05:34+08:00")
    assert "delivery_sla_missed" in late, late
    assert [f for f in rq.assess({
        "date": "2026-08-31 08:30",
        "delivery": {"success": True,
                     "delivered_at": "2026-08-31T09:05:34+08:00"},
        "llm": {"analysis_origin": "luna_specialized"}})
        if f["code"] == "delivery_sla_missed"][0]["severity"] == "defect"
    # 新排程的正常日子(05:41)不得誤報
    assert "delivery_sla_missed" not in _codes("2026-08-31T05:41:00+08:00")
    # 邊界:09:00 整點就算違規(台股已開盤)
    assert "delivery_sla_missed" in _codes("2026-08-31T09:00:00+08:00")
    assert "delivery_sla_missed" not in _codes("2026-08-31T08:59:59+08:00")
    # 舊 manifest 沒有這個欄位 → **不得**產生假警報
    assert "delivery_sla_missed" not in _codes("")
    # 壞值要說得出來,不是靜靜跳過
    assert "delivered_at_unparsable" in _codes("garbage")


def test_the_sla_deadline_is_a_pinned_constant():
    """改期限要連測試一起改,不能默默放寬。"""
    assert (rq.SLA_HOUR, rq.SLA_MINUTE) == (9, 0)


def test_a_quality_defect_actually_reaches_a_human(monkeypatch):
    """**完成競態**(外審 P1):品質判準先前只由看門狗事後跑,而看門狗
    可能在主班還在跑時啟動,看不到「這班最後 Luna 會失敗」。

    第一版我只加了自評步驟 + `continue-on-error`,註解卻宣稱「結果寫進
    output 由獨立 job 判讀」—— **那個 output 與那個 job 都不存在**
    (r1 外審)。判準會跑、會印 annotation,然後沒有任何人收到通知。
    這條驗的是**整條鏈**:步驟寫 output → job 暴露 output → 有消費端。
    """
    import yaml
    wf = yaml.safe_load(io.open(
        _ROOT / ".github" / "workflows" / "morning-report-a.yml",
        encoding="utf-8").read())
    send = wf["jobs"]["send-report"]
    steps = send["steps"]
    names = [s.get("name") or "" for s in steps]

    # ① 步驟存在、有 id、不讓 job 變紅、位置在寄信之後發佈之前
    q = [s for s in steps if (s.get("name") or "").startswith("本班品質")]
    assert q, names
    q = q[0]
    assert q.get("id") == "quality", q
    assert q.get("continue-on-error") is True, "品質瑕疵不得讓 job 變紅"
    assert "assert_run_quality" in q["run"]
    i = names.index(q["name"])
    assert names.index("Run morning report") < i < names.index(
        "發佈 state(契約通過後才 push)")

    # ② 步驟真的寫 output(不是只印 annotation)
    assert "GITHUB_OUTPUT" in q["run"] and "defect=" in q["run"], q["run"]

    # ③ job 把它暴露出去
    outs = send.get("outputs") or {}
    assert "steps.quality.outputs.defect" in str(outs.get("quality_defect"))
    assert "steps.quality.outputs.detail" in str(outs.get("quality_detail"))

    # ④ **有消費端**,而且它讀的就是那個 output
    alert = wf["jobs"].get("alert-on-quality")
    assert alert, "沒有消費端 = 判準跑完沒有人知道"
    assert "quality_defect" in alert["if"] and "'true'" in alert["if"], alert["if"]
    assert alert.get("needs") == "send-report"
    body = str(alert["steps"])
    assert "quality_alert.py" in body
    assert "QUALITY_DETAIL" in body, "告警信沒帶上判準說了什麼"
    # ⑤ 失敗告警的觸發條件不含品質(兩者語意不同,不可混為一談)
    fail_if = wf["jobs"]["alert-on-failure"]["if"]
    assert "quality" not in fail_if, fail_if


def test_the_quality_alert_says_what_went_wrong():
    """信件內容抽成獨立腳本才測得到(而且 workflow 裡不再有巢狀 heredoc
    —— 第一版就是那個 heredoc 把 YAML 弄壞的)。"""
    import datetime as _dt
    sys_path = str(_ROOT / "tools")
    import sys as _sys
    if sys_path not in _sys.path:
        _sys.path.insert(0, sys_path)
    import quality_alert as qa
    msg = qa.build_message(
        "[watchdog] 品質異常:delivery_sla_missed —— 晨報 09:05 才寄出",
        "https://example/run/1",
        _dt.datetime(2026, 8, 31, 9, 6, tzinfo=qa.TPE))
    assert "晨報品質" in msg["Subject"] and "2026-08-31" in msg["Subject"]
    text = msg.get_content()
    assert "delivery_sla_missed" in text, "沒帶上判準說了什麼"
    assert "https://example/run/1" in text
    assert "看門狗" in text, "要說清楚這封是產出者自己發的"
