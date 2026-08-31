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


def test_a_quality_problem_actually_reaches_a_human(monkeypatch):
    """**整條鏈**:步驟寫 output → job 暴露 → 有消費端讀它。

    r1 抓到第一版只有自評步驟(output 與消費端都不存在);
    r2 又抓到**通知政策綁在退出碼上** —— 退出碼只有 `defect` 會非零,
    而 `analysis_not_specialized`(Luna 落回 legacy)這種最該通知的事
    是 `degraded`,於是這套機制當初要抓的那件事自己不會發告警。
    """
    import yaml
    wf = yaml.safe_load(io.open(
        _ROOT / ".github" / "workflows" / "morning-report-a.yml",
        encoding="utf-8").read())
    send = wf["jobs"]["send-report"]
    steps = send["steps"]
    names = [s.get("name") or "" for s in steps]

    q = [s for s in steps if (s.get("name") or "").startswith("本班品質")]
    assert q, names
    q = q[0]
    assert q.get("id") == "quality"
    assert q.get("continue-on-error") is True, "品質瑕疵不得讓 job 變紅"
    assert "assert_run_quality" in q["run"]
    # **只對真的產出本班 manifest 的 run 判**(r2 外審):no-op 備援班
    # 讀到的是 checkout 來的舊 manifest。
    assert "run_outcome == 'delivered'" in q["if"], q["if"]
    i = names.index(q["name"])
    assert names.index("Run morning report") < i < names.index(
        "發佈 state(契約通過後才 push)")

    # job 暴露的是 **alertable**(不是退出碼推導的 defect)
    outs = send.get("outputs") or {}
    assert "steps.quality.outputs.alertable" in str(outs.get("quality_alertable"))
    assert "steps.quality.outputs.summary" in str(outs.get("quality_detail"))

    # 有消費端,而且讀的就是那個 output
    alert = wf["jobs"].get("alert-on-quality")
    assert alert, "沒有消費端 = 判準跑完沒有人知道"
    assert "quality_alertable" in alert["if"] and "'true'" in alert["if"]
    assert alert.get("needs") == "send-report"
    body = str(alert["steps"])
    assert "quality_alert.py" in body and "QUALITY_DETAIL" in body
    # 告警 job 不需要 repo 寫入權(r2 外審 P2)
    assert (alert.get("permissions") or {}).get("contents") == "read"
    # 失敗告警的觸發條件不含品質(兩者語意不同)
    assert "quality" not in wf["jobs"]["alert-on-failure"]["if"]


def test_a_degraded_only_run_still_notifies(tmp_path, monkeypatch):
    """**08/31 那班的形狀**:Luna 落回 legacy → `analysis_not_specialized`
    是 `degraded` → 退出碼 0。綁退出碼的話不會通知,而看門狗那端「有任何
    finding 就告警」—— 兩套監控對同一件事說不同的話,比只有一套更糟。"""
    import sys as _sys
    _sys.path.insert(0, str(_ROOT / "tools"))
    import assert_run_quality as arq
    out = tmp_path / "gh_out"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    arq._emit_outputs([{"code": "analysis_not_specialized",
                        "severity": "degraded", "detail": "落回 legacy"}])
    text = out.read_text(encoding="utf-8")
    assert "alertable=true" in text, text
    assert "has_defect=false" in text, text        # 退出碼仍是 0(CI 不擋)
    assert "max_severity=degraded" in text
    assert "analysis_not_specialized" in text, "摘要沒帶上判準說了什麼"
    # 全過的日子不得通知
    out.write_text("", encoding="utf-8")
    arq._emit_outputs([])
    assert "alertable=false" in out.read_text(encoding="utf-8")


def test_the_run_says_what_it_actually_did(tmp_path, monkeypatch):
    """`run_outcome` 是品質自評與 state 契約的閘門 —— 沒有它,no-op 備援班
    會拿舊 manifest 判品質(false green,或對著昨天的缺陷再寄一封)。"""
    out = tmp_path / "gh_out"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"date": "2026-09-01 05:10", "delivery": {}}),
                 encoding="utf-8")
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", m)
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(mr, "_RUN_STAMP", "")
    mr._set_run_stamp(dt.datetime(2026, 9, 1, 5, 10, tzinfo=mr.TPE))
    mr._mark_delivery_in_manifest(attempted=True, success=True)
    assert "run_outcome=delivered" in out.read_text(encoding="utf-8")
    # 刻意不寄的日子不是 delivered
    out.write_text("", encoding="utf-8")
    mr._mark_delivery_in_manifest(attempted=False, success=False,
                                  skipped_reason="weekend_no_new_content")
    assert "run_outcome=intentionally_skipped" in out.read_text(encoding="utf-8")
    # 接線:no-op 早退那一條也要說
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    j = src.index("本班是備援觸發,不重複寄送")
    assert "already_delivered" in src[j:j + 200]


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


def test_the_sla_deadline_is_taipei_nine_not_local_nine():
    """r2 外審 P2:期限是「**台北**的九點」。目前 writer 寫 `+08:00`,
    直接讀 hour 剛好對 —— 但 writer 若改寫 UTC(`01:05:34+00:00` 就是
    台北 09:05),判準會看到 hour=1 而說「沒有超時」,**悄悄失效**。
    判準自己守住時區語意,不靠 writer 剛好寫對。"""
    def _codes(at):
        return {f["code"] for f in rq.assess({
            "date": "2026-09-01 05:10",
            "delivery": {"success": True, "delivered_at": at},
            "llm": {"analysis_origin": "luna_specialized"}})}
    # 同一個時刻的三種寫法,判準要說同一句話
    for at in ("2026-09-01T09:05:34+08:00",      # 台北
               "2026-09-01T01:05:34+00:00",      # UTC
               "2026-08-31T21:05:34-04:00"):     # 紐約
        assert "delivery_sla_missed" in _codes(at), at
    # 準時的也一樣(換算後 05:41 台北)
    for at in ("2026-09-01T05:41:00+08:00", "2026-08-31T21:41:00+00:00"):
        assert "delivery_sla_missed" not in _codes(at), at
    # 沒帶時區 → 視為台北(產出端一直是 TPE)
    assert "delivery_sla_missed" in _codes("2026-09-01T09:05:34")
    assert "delivery_sla_missed" not in _codes("2026-09-01T05:41:00")


def test_the_missing_timestamp_exemption_has_an_end_date():
    """r2 外審 P2:「舊 manifest 沒有這個欄位不算違規」是對的(否則部署
    當天必定一次假警報),但**沒有截止點的話那個豁免是永久的** ——
    將來某條新寄信路徑忘了寫,判準會說「沒問題」而不是「無法稽核」。"""
    def _codes(extra):
        m = {"date": "2026-09-01 05:10", "delivery": {"success": True},
             "llm": {"analysis_origin": "luna_specialized"}}
        m.update(extra)
        return {f["code"] for f in rq.assess(m)}
    # 這一版(含)以後:缺 delivered_at 是 defect
    assert "delivered_at_missing" in _codes(
        {"manifest_schema": rq.MANIFEST_SCHEMA_WITH_DELIVERED_AT})
    # 舊 manifest(沒有世代標記):豁免,不製造假警報
    assert "delivered_at_missing" not in _codes({})
    # **世代只有一個定義**(不然兩邊會漂移)
    import run_manifest as rm
    assert rq.MANIFEST_SCHEMA_WITH_DELIVERED_AT == rm.MANIFEST_SCHEMA


def test_the_generation_marker_survives_the_sunday_rebuild():
    """r1 外審:第一版把世代標記蓋在 `_mark_delivery_in_manifest` 的 `base`
    上,而**週日路徑之後會 `_write_run_manifest()` 從頭重建文件** ——
    標記就掉了,那份 manifest 於是永久保有「舊檔豁免」,
    `delivered_at` 缺席永遠不會被判成缺陷。

    我的原測試只 grep 原始碼有沒有那一行,**從來沒走過標記→重建的序列**
    —— 那正是缺陷所在的地方。這條走權威產生器本身。
    """
    import run_manifest as rm
    doc = rm.ManifestRecorder().build(
        date="2026-08-30 08:30", report_kind="weekend_digest",
        budget_seconds=2700.0, news_workers=4, degraded_steps=[], feeds={})
    assert doc.get("manifest_schema") == rm.MANIFEST_SCHEMA, doc.get(
        "manifest_schema")
    # 重建出來的文件,配上「寄成功但沒寫 delivered_at」→ 必須是缺陷
    doc["delivery"] = {"success": True}
    codes = {f["code"] for f in rq.assess(doc)}
    assert "delivered_at_missing" in codes, codes


def test_a_weekend_digest_is_not_told_the_market_opens():
    """r2 外審 P2:週日沒有台股開盤 —— SLA 仍然適用(使用者要的是
    「09:00 前」),但訊息不該說一個當天不存在的理由。"""
    def _detail(m):
        return "".join(f["detail"] for f in rq.assess(m)
                       if f["code"] == "delivery_sla_missed")
    base = {"date": "2026-08-30 08:30",
            "delivery": {"success": True,
                         "delivered_at": "2026-08-30T09:05:00+08:00"}}
    weekday = dict(base, llm={"analysis_origin": "luna_specialized"})
    assert "台股開盤" in _detail(weekday), _detail(weekday)
    sunday = dict(base, report_kind="weekend_digest")
    d = _detail(sunday)
    assert d, "週日的 SLA 照樣要判"
    assert "台股開盤" not in d, d
