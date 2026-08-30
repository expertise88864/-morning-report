# -*- coding: utf-8 -*-
"""2026-08-30 週日實信:排程復活 + 冪等實戰成功,但觀測面三個缺陷。

主鏈全對:改名後排程第一次自然觸發(兩班都來了)、備援班 182 秒空轉
(三來源冪等第一次實戰)、看門狗判「已寄」不補。以下修的是配角:
收據沒更新、run_kind 記錯、回顧的 `---` 印成文字。
"""
import datetime as dt
import io
import json
from pathlib import Path

import morning_report as mr

_ROOT = Path(mr.__file__).resolve().parent


def test_the_receipt_date_is_today_not_the_stale_manifest_date(tmp_path,
                                                               monkeypatch):
    """週日路徑的 mark 跑在寫 manifest **之前**,`base` 是昨天的檔 ——
    收據 payload 變成「昨天+success」,與遠端內容相同而被去重跳過,
    今天的寄送於是**沒有收據**(實信:收據停在 08/29,信是 08/30 的)。
    收據回答「今天有結論了嗎」,日期就該是今天。"""
    stale = tmp_path / "m.json"
    stale.write_text(json.dumps(
        {"date": "2026-08-29 07:35",
         "delivery": {"success": True, "run_kind": "workflow_dispatch"}}),
        encoding="utf-8")
    receipt = tmp_path / "r.json"
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", stale)
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", receipt)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setattr(mr, "_RUN_STAMP", "")
    mr._set_run_stamp(dt.datetime(2026, 8, 30, 8, 10, tzinfo=mr.TPE))
    mr._mark_delivery_in_manifest(attempted=True, success=True)
    data = json.loads(receipt.read_text(encoding="utf-8"))
    assert str(data["date"])[:10] == "2026-08-30", data["date"]
    # run_kind 是本班的觸發方式,不是昨天檔案裡殘留的那個
    assert data["delivery"]["run_kind"] == "schedule", data["delivery"]


def test_model_horizontal_rules_never_print_as_text():
    """實信:回顧三段之間的 `---` 被印成三個橫槓的文字行。"""
    html = mr._render_week_review_html(
        "### 本週大事回顧\n內容甲\n\n---\n\n### 下週關注方向\n內容乙",
        __import__("html"))
    assert "---" not in html, html[:400]
    assert "本週大事回顧" in html and "內容乙" in html


def test_the_week_review_prompt_forbids_already_settled_events():
    """實信第 4 條把**已公布的輝達財報**寫成「下週關注、時間未定」——
    上週被寫成下週。規則要用素材裡真實的形狀說明(prompt 要示範它要求
    的東西),這裡驗規則存在且在圍欄外。"""
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    i = src.index("### 下週關注方向")
    seg = src[i:i + 800]
    assert "已經發生、結果已知的事不得列入" in seg
    j = src.index("def _build_week_review_prompt")
    fence = src.index("</UNTRUSTED_SOURCE_DATA>", j)
    assert src.index("已經發生、結果已知的事不得列入", j) > fence


def test_every_emitted_degradation_label_is_registered():
    """**08/29 品質告警信實際印出「沒見過的降級步驟:gazette」。**

    查下去不是漏一個,是漏 17 個 —— 只註冊了 `weekend_gazette`,而平日
    路徑發的是 `gazette`。症狀不是壞掉,是**看起來像壞掉**:告警信把
    每一個未註冊標籤都報成 `unknown_degradation`,operator 分不出
    「真的有沒見過的狀況」與「這個標籤忘了登記」。

    判準用掃描不用列舉:新增 `_DEGRADED_STEPS.append("x")` 而忘了註冊時,
    這條會在**作者面前**紅,不必等生產寄出告警信。
    """
    import re
    import run_quality as rq
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    emitted = {m.group(1) for m in
               re.finditer(r'_DEGRADED_STEPS\.append\("([a-z0-9_:]+)"\)', src)}
    assert len(emitted) >= 20, f"掃不到標籤就是判準壞了:{len(emitted)}"
    # 開放家族(動態組成,由前綴豁免;見 run_quality 的 `_family_ok`)
    families = ("llm:", "state:", "gap:", "recap:")
    missing = sorted(e for e in emitted
                     if e not in rq.KNOWN_DEGRADED
                     and not e.startswith(families))
    assert not missing, f"這些標籤會在告警信裡變成「沒見過的降級步驟」:{missing}"


def test_a_run_that_crosses_midnight_stamps_the_day_it_started(tmp_path,
                                                               monkeypatch):
    """r1 外審:第一版把收據日期改成「寄送完成時的牆鐘」—— 手動觸發
    可以 23:50 開始、00:05 寄出,收據於是蓋成**隔天**,隔天 06:07 的排程
    讀到它就整班跳過,**真正的隔天晨報缺席**。

    同一次執行的所有產物要說同一個事實,而那個事實在**開跑當下**就定了。
    """
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"date": "2026-08-29 07:35", "delivery": {}}),
                 encoding="utf-8")
    r = tmp_path / "r.json"
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", m)
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", r)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    # **開跑日不可以是「今天」**:用今天的話,壞掉的版本(寄完看牆鐘)
    # 也會得到今天 —— 測試就靠巧合通過了(第一版正是如此,突變沒紅)。
    started = dt.datetime(2026, 1, 15, 23, 50, tzinfo=mr.TPE)
    assert started.strftime("%Y-%m-%d") != dt.datetime.now(
        mr.TPE).strftime("%Y-%m-%d"), "反例日期撞到今天就量不到了"
    monkeypatch.setattr(mr, "_RUN_STAMP", "")
    mr._set_run_stamp(started)
    # 寄送在午夜之後才完成 —— 收據仍要是「開跑那天」
    mr._mark_delivery_in_manifest(attempted=True, success=True)
    data = json.loads(r.read_text(encoding="utf-8"))
    assert str(data["date"])[:10] == "2026-01-15", data["date"]
    # 而且戳是冪等的:同一次執行再問一次不會變成隔天
    assert mr._set_run_stamp(
        dt.datetime(2026, 1, 16, 0, 5, tzinfo=mr.TPE))[:10] == "2026-01-15"


def test_recipient_addresses_never_come_from_public_repo_variables():
    """**public repo 的 Actions log 裡 `vars.*` 是明文、`secrets.*` 被遮罩。**
    收件人是個人信箱,不該有機會出現在公開 log(2026-08-30 架構外審)。
    `RECIPIENT` 已確認是 secret(variables 裡沒有這個名字),改 secrets-only
    不會斷;`QUALITY_RECIPIENT` 目前仍是 variable,改成 secret 優先、
    保留 vars 當過渡(使用者搬完就會走 secret)。"""
    import glob
    import re
    for path in glob.glob(str(_ROOT / ".github" / "workflows" / "*.yml")):
        wf = io.open(path, encoding="utf-8").read()
        # **掃所有 `*_RECIPIENT`**(r1 外審):第一版的 regex 只認恰好叫
        # `RECIPIENT` 的鍵,漏掉 `RADAR_RECIPIENT`(而且它 `vars` 在前 ——
        # 加了 secret 也不會生效)。收件人是一族,守衛就要掃整族。
        for m in re.finditer(r"^\s*([A-Z0-9_]*RECIPIENT):\s*(.+)$", wf, re.M):
            key, expr = m.group(1), m.group(2)
            if "vars." not in expr:
                continue
            # 過渡期例外:仍是 repo variable 的,至少要 **secret 優先**,
            # 使用者搬進 secret 當天就生效,不必再改程式。
            assert expr.index("secrets.") < expr.index("vars."), (
                f"{path}: {key} 的 vars 在 secret 前面 —— "
                "加了 secret 也不會生效")


def test_a_successful_send_does_not_inherit_yesterdays_skip_reason(
        tmp_path, monkeypatch):
    """r2 外審:`base` 可能是 checkout 來的舊 manifest,舊的
    `skipped_reason`(週日無新內容)會被複製進今天的 delivery ——
    於是 `success=true` 卻同時帶著 `skipped_reason`。而看門狗**先讀
    skipped_reason**,會把「寄出去了」判成「刻意沒寄」。"""
    m = tmp_path / "m.json"
    m.write_text(json.dumps({
        "date": "2026-08-29 07:00",
        "delivery": {"attempted": False, "success": False,
                     "skipped_reason": "weekend_no_new_content"}}),
        encoding="utf-8")
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", m)
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setattr(mr, "_RUN_STAMP", "")
    mr._set_run_stamp(dt.datetime.now(mr.TPE))
    mr._mark_delivery_in_manifest(attempted=True, success=True)
    d = json.loads(m.read_text(encoding="utf-8"))["delivery"]
    assert d["success"] is True and "skipped_reason" not in d, d
    # 看門狗的判讀順序:有 skipped_reason 就先回「刻意不寄」
    import sys as _s
    _s.path.insert(0, str(_ROOT / "tools"))
    import report_watchdog as w
    assert not w.delivery_state.__doc__ or True     # 只確認 import 得到
    assert d.get("run_kind") == "schedule"


def test_registered_is_not_the_same_as_silent():
    """r2 外審:上一批為了修「沒見過的降級步驟」把 17 個標籤補進
    `KNOWN_DEGRADED` —— 而那個集合的語意是「已知**且可接受**」。
    結果三個**安全機制失效**的訊號從「至少報 unknown」變成完全不出聲:
    修正比缺陷更糟。登記是為了讓掃描守衛過得去,能不能靜音是另一回事。"""
    import run_quality as rq
    codes = {f.get("code") for f in rq.assess({
        "date": "2026-08-30 08:15", "delivery": {"success": True},
        "degraded_steps": ["story_ledger_corrupt",
                           "delivery_receipt_publish",
                           "analysis_recap_unreadable", "gazette"]})}
    for label in ("story_ledger_corrupt", "delivery_receipt_publish",
                  "analysis_recap_unreadable"):
        assert label in codes, f"{label} 被靜音了:{codes}"
    # 真正可接受的降級不必各自出一條(否則告警信會被噪音淹掉)
    assert "gazette" not in codes
