# -*- coding: utf-8 -*-
"""2026-09-01 r7 外審:**監控自己的契約**。

主程式的 schema 思維已經成熟,但最外圍那層「監控監控的東西」還留著
早期的 legacy-style 判斷:missing-is-old-format 與 truthiness。
而看門狗存在的理由正是「有跑過 ≠ 有成功寄到」——
證據不見了或壞掉,恰恰是最該吵的時候。
"""
import io
import json
import sys
from pathlib import Path

import morning_report as mr
import run_quality as rq

_ROOT = Path(mr.__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "tools"))
import report_watchdog as w  # noqa: E402


def _manifest(tmp_path, payload):
    p = tmp_path / "run_manifest.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_missing_delivery_evidence_is_not_all_the_same_thing(tmp_path):
    """三種狀態先前壓成一個 `{}` → 全部解讀成「舊格式,正常」:
    真舊檔沒有這個欄位、現行世代的 writer 沒寫出來、欄位型別壞掉。
    後兩者是**證據不見了**,那是最該吵的時候。"""
    cases = [
        ({"date": "2026-09-02 05:20"}, w.EVIDENCE_LEGACY_MISSING),
        ({"manifest_schema": 2, "date": "2026-09-02 05:20"},
         w.EVIDENCE_CURRENT_MISSING),
        ({"manifest_schema": 1, "date": "2026-09-02 05:20"},
         w.EVIDENCE_CURRENT_MISSING),
        ({"manifest_schema": 2, "date": "x", "delivery": []},
         w.EVIDENCE_INVALID),
        ({"manifest_schema": 2, "date": "x", "delivery": "foo"},
         w.EVIDENCE_INVALID),
        ({"manifest_schema": 2, "date": "x", "delivery": {"success": True}},
         w.EVIDENCE_VALID),
    ]
    for payload, expect in cases:
        got, _dv = w.delivery_state(_manifest(tmp_path, payload))
        assert got == expect, (payload, got, expect)


def test_a_truthy_string_is_not_a_successful_delivery():
    """`success` 是**控制流事實**(要不要告警、要不要補寄、要不要判 SLA、
    origin/main 有沒有結論)。`"false"` 是壞掉的 state 最可能長的樣子,
    而它在 Python 是 truthy。"""
    assert rq.delivery_success({"success": True}) == rq.DELIVERY_SUCCEEDED
    assert rq.delivery_success({"success": False}) == rq.DELIVERY_NOT_SUCCEEDED
    assert rq.delivery_success({}) == rq.DELIVERY_NOT_SUCCEEDED
    for junk in ("false", "no", 1, 0, [1], {"a": 1}, "true"):
        assert rq.delivery_success({"success": junk}) == (
            rq.DELIVERY_SUCCESS_INVALID), junk


def test_the_assessor_reports_a_broken_success_field():
    codes = {f["code"]: f["severity"] for f in rq.assess({
        "date": "2026-09-02 05:20", "manifest_schema": 2,
        "llm": {"analysis_origin": "luna_specialized"},
        "delivery": {"success": "false",
                     "delivered_at": "2026-09-02T05:30:00+08:00",
                     "first_delivered_at": "2026-09-02T05:30:00+08:00"}})}
    assert codes.get("delivery_success_invalid") == "defect", codes
    # 壞掉的 success **不可以**同時被當成「寄出了」而去判 SLA
    assert "delivery_sla_missed" not in codes


def test_broken_success_does_not_block_the_rescue(tmp_path):
    """漏判的方向要選對:`"false"` 被當成「今天已經寄出」會**擋掉補寄**,
    那是「漏寄一整天」的方向 —— 而重複寄信才是那道守衛原本要防的事。"""
    import datetime as dt
    now = dt.datetime(2026, 9, 2, 5, 20, tzinfo=mr.TPE)
    for junk in ("false", 1, "no", [1]):
        d = {"date": "2026-09-02 05:07",
             "delivery": {"success": junk, "run_kind": "schedule"}}
        assert mr._manifest_delivery_verdict(d, now) == "", junk
    good = {"date": "2026-09-02 05:07",
            "delivery": {"success": True, "run_kind": "schedule"}}
    assert mr._manifest_delivery_verdict(good, now)


def test_both_monitors_share_one_predicate():
    """兩套監控對同一件事要說同一句話 —— 判準本體只有一份。"""
    src = io.open(_ROOT / "tools" / "report_watchdog.py",
                  encoding="utf-8").read()
    assert "_rq.delivery_success(dv)" in src, (
        "看門狗自己又寫了一套 success 判準")
    assert 'd.get("success"):' not in src, (
        "還有 truthiness 判斷沒有改掉")
    mr_src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    assert "_rq.delivery_success(delivery)" in mr_src


def test_the_incident_model_does_not_drift_back(tmp_path):
    """r7 外審(P3):看門狗的檔頭又宣稱了隔壁檔剛撤回的因果 ——
    「改 cron 會讓排程註冊卡死」。incident 文件是事故當下真的會看的東西,
    錯誤的根因會導致錯誤的處置(「沒改 cron 就不會再發生」)。

    這道守衛掃**所有** workflow 檔,不只掃當時出問題的那一個 ——
    只掃單一檔的守衛,下一次換個檔案寫錯就靜默失效。
    """
    bad = []
    for path in sorted((_ROOT / ".github" / "workflows").glob("*.yml")):
        text = io.open(path, encoding="utf-8").read()
        for i, line in enumerate(text.splitlines(), 1):
            if "cron" not in line or "卡死" not in line:
                continue
            # 明確否定那個因果的句子不算(它們正是要留下來的)
            if any(k in line for k in ("不是", "✗", "未證實")):
                continue
            bad.append(f"{path.name}:{i} {line.strip()}")
    assert not bad, ("workflow 註解又把「改 cron」寫成已證實的根因:", bad)
    # 檔名與檔頭要一致 —— 上一次就是改了檔名沒改註解
    for path in sorted((_ROOT / ".github" / "workflows").glob("*-b.yml")):
        head = io.open(path, encoding="utf-8").read()[:400]
        assert "`-a`" not in head or "`-b`" in head, (
            f"{path.name} 的檔頭還在說自己是 -a")


def test_a_broken_schema_value_is_not_an_old_file(tmp_path):
    """r7 外審第二輪:**key 在不在要問 key** —— 第三次踩到同一個形狀。

    `run_quality` 已經確立過同一條(欄位缺席才是舊檔,存在但無效是壞掉),
    我卻在看門狗用 `.get()` 的回傳值判,於是
    `manifest_schema: null / "2" / false` 全被當成「真舊檔」——
    版本資訊壞掉的檔反而拿到最寬鬆的待遇,而它連自己是第幾版都說不清。
    """
    for bad in (None, "2", False, True, 0, -1, 1.5, [2]):
        got, _ = w.delivery_state(_manifest(
            tmp_path, {"manifest_schema": bad, "date": "2026-09-02 05:20"}))
        assert got == w.EVIDENCE_INVALID, (bad, got)
    # 真的沒有這個 key 才是舊檔
    got, _ = w.delivery_state(_manifest(tmp_path, {"date": "2026-09-02 05:20"}))
    assert got == w.EVIDENCE_LEGACY_MISSING
    # 合法世代 + 缺 delivery = writer 沒寫出來
    for good in (1, 2):
        got, _ = w.delivery_state(_manifest(
            tmp_path, {"manifest_schema": good, "date": "x"}))
        assert got == w.EVIDENCE_CURRENT_MISSING, good


def test_broken_evidence_is_checked_before_what_it_says(tmp_path):
    """r7 外審第二輪:`skipped_reason` 原本排在 `success` 驗證之前 ——
    於是「`success` 型別壞掉」的檔只要**順便**有這個欄位,就會走
    「刻意不寄 → 正常」而完全不檢查。**壞掉的證據不可以因為它剛好
    也說了一句「我今天不寄」就被信任。**
    """
    import datetime as dt
    now = dt.datetime(2026, 9, 2, 5, 20, tzinfo=mr.TPE)
    broken = {"date": "2026-09-02 05:07",
              "delivery": {"success": "false",
                           "skipped_reason": "weekend_no_new_content"}}
    assert mr._manifest_delivery_verdict(broken, now) == "", (
        "壞掉的證據擋掉了補寄")
    # 正常的「刻意不寄」仍然要擋(批#69 r2 修過的假警報不可以回來)
    ok = {"date": "2026-09-02 05:07",
          "delivery": {"success": False,
                       "skipped_reason": "weekend_no_new_content"}}
    assert mr._manifest_delivery_verdict(ok, now)

    src = io.open(_ROOT / "tools" / "report_watchdog.py",
                  encoding="utf-8").read()
    body = src[src.index("def main("):]
    assert body.index("_rq_delivery_success(delivery)") < body.index(
        'if delivery.get("skipped_reason")'), (
        "看門狗又把 skipped_reason 排到 success 驗證前面")


def test_a_broken_receipt_cannot_claim_todays_conclusion(tmp_path,
                                                         monkeypatch):
    """`fresh_conclusion()` 同理:壞掉的 success 不可以宣稱今天已有結論
    ——它會讓看門狗放棄補寄。"""
    remote = tmp_path / "receipt.json"
    remote.write_text(json.dumps({
        "date": "2026-09-02", "delivery": {
            "success": "false", "skipped_reason": "weekend_no_new_content"}}),
        encoding="utf-8")
    monkeypatch.setenv("WATCHDOG_FRESH_RECEIPT", str(remote))
    monkeypatch.delenv("WATCHDOG_FRESH_MANIFEST", raising=False)
    import datetime as dt
    assert w.fresh_conclusion(dt.datetime(2026, 9, 2, 7, 50)) == "", (
        "壞掉的收據宣稱了今天已有結論")
    remote.write_text(json.dumps({
        "date": "2026-09-02", "delivery": {"success": True,
                                           "run_kind": "schedule"}}),
        encoding="utf-8")
    assert w.fresh_conclusion(dt.datetime(2026, 9, 2, 7, 50))


def test_a_broken_schema_does_not_erase_real_delivery_evidence(tmp_path):
    """r7 外審第三輪:**我的修法把漏報換成了誤報。**

    `manifest_schema` 壞掉不會讓一個明確的 `success: true` 失效。
    我把 schema 檢查排在寄送證據之前,於是「版本壞掉但確實寄出了」
    被判成「沒寄到」(rc=1)—— 而 rc=1 會觸發自動補寄:
    ★把漏報換成了重複寄信,那是收不回來的那一邊。★
    版本壞掉是品質缺陷(由 `run_quality` 報,rc=2),
    不是由看門狗宣稱沒寄到。
    """
    cases = [
        # 版本壞掉,但寄送證據明確 → 仍然是 valid
        ({"manifest_schema": None, "date": "x",
          "delivery": {"success": True}}, w.EVIDENCE_VALID),
        ({"manifest_schema": "2", "date": "x",
          "delivery": {"success": False,
                       "skipped_reason": "weekend_no_new_content"}},
         w.EVIDENCE_VALID),
        # 版本壞掉**又**沒有寄送證據 → 才是 invalid
        ({"manifest_schema": None, "date": "x"}, w.EVIDENCE_INVALID),
        ({"manifest_schema": None, "date": "x", "delivery": []},
         w.EVIDENCE_INVALID),
    ]
    for payload, expect in cases:
        got, _ = w.delivery_state(_manifest(tmp_path, payload))
        assert got == expect, (payload, got, expect)

    # 版本壞掉本身仍然要被**品質判準**抓到(換一個機制報,不是不報)
    codes = {f["code"]: f["severity"] for f in rq.assess({
        "manifest_schema": None, "date": "2026-09-02 05:20",
        "llm": {"analysis_origin": "luna_specialized"},
        "delivery": {"success": True,
                     "delivered_at": "2026-09-02T05:30:00+08:00",
                     "first_delivered_at": "2026-09-02T05:30:00+08:00"}})}
    assert codes.get("manifest_schema_invalid") == "defect", codes
