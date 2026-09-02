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
        # 日期要早於 `MANIFEST_SCHEMA_REQUIRED_FROM` 才算真舊檔
        # (r8 外審:那個豁免現在有截止日了)
        ({"date": "2026-08-15 05:20"}, w.EVIDENCE_LEGACY_MISSING),
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
    # r8 之後這條由**狀態機**報(`delivery_state_invalid`)—— 壞掉的
    # `success` 只是「這份紀錄說不出今天寄了沒」的其中一種形狀。
    assert codes.get("delivery_state_invalid") == "defect", codes
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
    assert "_rq.delivery_outcome(dv)" in src, (
        "看門狗自己又寫了一套判準")
    assert 'd.get("success"):' not in src, (
        "還有 truthiness 判斷沒有改掉")
    mr_src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    assert "_rq.delivery_outcome(delivery)" in mr_src


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
    # 真的沒有這個 key **而且日期早於截止日**才是舊檔
    # (r8 外審:這個豁免本身也需要一個歷史錨點,見
    #  `test_the_schema_exemption_itself_has_a_cutoff`)
    got, _ = w.delivery_state(_manifest(tmp_path, {"date": "2026-08-15 05:20"}))
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
    # r8 外審之後這件事由**狀態機**保證,而不是靠誰排在誰前面:
    # 矛盾與半套的組合在 `delivery_outcome()` 就是 invalid。
    body = src[src.index("def main("):]
    assert "_rq_delivery_outcome(delivery)" in body
    assert 'if delivery.get("skipped_reason")' not in body, (
        "又出現了自己排順序的 if")


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


def test_a_broken_delivery_shape_is_not_silent_in_the_assessor():
    """r7 外審附帶點名:`run_quality` 把非 dict 的 delivery 靜靜轉成 `{}`
    —— 那等於把「寄送結論損毀」偽裝成「還沒有結論」,而後者在判準眼中
    是安靜的。看門狗那端會 rc=1,判準這端也要出聲,否則品質告警鏈
    對同一件事說的話不一樣。

    **「缺 delivery」刻意不在判準報**:canary 是 `DRY_RUN=1` 不寄信,
    它的 manifest 正常就沒有這個欄位 —— 無條件報會讓 canary 每次都紅。
    「這一班該有結論了嗎」需要時序資訊(有沒有 run 還在跑),
    那是看門狗有而判準沒有的,所以那半留在看門狗。
    """
    base = {"manifest_schema": 2, "date": "2026-09-02 05:20",
            "llm": {"analysis_origin": "legacy"}}
    for bad in ([], "foo", 42, None):
        m = dict(base, delivery=bad)
        codes = {f["code"]: f["severity"] for f in rq.assess(m)}
        assert codes.get("delivery_structure_invalid") == "defect", (bad, codes)
    # canary(DRY_RUN)的常態:根本沒有這個欄位 —— 不可以誤報
    assert "delivery_structure_invalid" not in {
        f["code"] for f in rq.assess(dict(base))}
    ok = dict(base, delivery={"success": True,
                              "delivered_at": "2026-09-02T05:30:00+08:00",
                              "first_delivered_at": "2026-09-02T05:30:00+08:00"})
    assert "delivery_structure_invalid" not in {
        f["code"] for f in rq.assess(ok)}

    # 這個分工要說得出來:看門狗那半仍然守著「缺 delivery」
    assert w.EVIDENCE_CURRENT_MISSING == "current_missing"


def test_the_canary_is_not_broken_by_the_new_finding():
    """DRY_RUN 的 canary 走不到 `_mark_delivery_in_manifest()` ——
    這條測試把那個前提釘住:它一旦不成立,上面那條的推理就垮了。"""
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    i = src.index("_mark_delivery_in_manifest(attempted=True, success=True)")
    before = src[:i]
    j = before.rindex("def deliver_report(")
    assert "send_email(html, subject)" in src[j:i], (
        "寄送標記不再排在 send_email 之後 —— DRY_RUN 可能會寫出 delivery")


def test_the_schema_exemption_itself_has_a_cutoff(tmp_path):
    """r8 外審:前面幾條世代都靠 `manifest_schema` 判「這份檔有沒有義務
    寫某個欄位」,但 `manifest_schema` **自己**缺席時只能說「舊檔」——
    一個**永遠不會到期**的豁免,與先前修掉的 `delivered_at` /
    `first_delivered_at` 完全同型。而看門狗前面已經確認這份 manifest
    是**今天**的:一份今天剛產生、卻沒有世代標記的檔,不能叫舊檔。

    ★而我上一批的測試把這件事釘死了★:它拿 `2026-09-02` 的新鮮 manifest
    斷言 `EVIDENCE_LEGACY_MISSING`。
    """
    assert rq.MANIFEST_SCHEMA_REQUIRED_FROM.isoformat() == "2026-09-02", (
        "截止日刻意設 09-02 —— 部署當天早上真正的舊檔不該被追溯判錯")
    for day, expect in (("2026-08-15 05:20", w.EVIDENCE_LEGACY_MISSING),
                        ("2026-09-01 05:20", w.EVIDENCE_LEGACY_MISSING),
                        ("2026-09-02 05:20", w.EVIDENCE_INVALID),
                        ("2026-12-01 05:20", w.EVIDENCE_INVALID)):
        got, _ = w.delivery_state(_manifest(tmp_path, {"date": day}))
        assert got == expect, (day, got, expect)

    def _codes(day):
        return {f["code"] for f in rq.assess({
            "date": f"{day} 05:20", "llm": {"analysis_origin": "legacy"},
            "delivery": {"success": True, "attempted": True,
                         "delivered_at": f"{day}T05:30:00+08:00",
                         "first_delivered_at": f"{day}T05:30:00+08:00"}})}
    assert "manifest_schema_missing" in _codes("2026-09-02")
    assert "manifest_schema_missing" not in _codes("2026-09-01")


def test_an_intentional_skip_still_checks_the_control_plane(tmp_path,
                                                            monkeypatch):
    """r8 外審:`skipped_reason` 那條先前直接 `return 0` —— 於是 schema
    壞掉之類的問題在「今天不寄信」的日子**完全無聲**,而 workflow 的
    品質自評只在 `run_outcome == 'delivered'` 時跑,那條路也補不到。

    但**不能**直接跑完整判準:裡面有一大類「信的內容夠不夠好」的判準,
    而今天本來就沒有信 —— 硬跑會製造假警報。
    """
    broken = {"date": "2026-09-06 05:20", "manifest_schema": None,
              "delivery": {"success": False, "attempted": False,
                           "skipped_reason": "weekend_no_new_content"}}
    codes = {f["code"] for f in rq.assess(broken)}
    assert "manifest_schema_invalid" in codes, codes
    # 控制面的留下,信的內容那類濾掉
    kept = [f["code"] for f in rq.assess(broken)
            if f.get("domain") != rq.DOMAIN_CONTENT]
    assert "manifest_schema_invalid" in kept
    assert "analysis_not_specialized" in codes and (
        "analysis_not_specialized" not in kept), (
        "刻意不寄的日子拿「信的內容」報警 —— 那會製造假警報")

    src = io.open(_ROOT / "tools" / "report_watchdog.py",
                  encoding="utf-8").read()
    body = src[src.index("def main("):]
    seg = body[body.index('_outcome == "intentionally_skipped"'):]
    assert "_control_plane_exit(info)" in seg[:600], (
        "刻意不寄那條又變回直接 return 0")


def test_one_state_machine_not_several_orderings():
    """r8 外審:先前每個 consumer 各自把 `success` 與 `skipped_reason`
    排成自己的順序,於是**同一份 state 在兩處說不同的話** ——
    `{"success": true, "skipped_reason": "..."}` 在看門狗主流程是
    「刻意未寄信」,在 `fresh_conclusion()` 是「今天已寄出」。
    那不是誰的順序寫錯,是這一對欄位從來沒有被當成一個狀態看待。
    """
    cases = {
        rq.OUTCOME_DELIVERED: {"attempted": True, "success": True},
        rq.OUTCOME_SKIPPED: {"attempted": False, "success": False,
                             "skipped_reason": "weekend_no_new_content"},
        rq.OUTCOME_FAILED: {"attempted": True, "success": False},
        rq.OUTCOME_INCOMPLETE: {"attempted": True},
    }
    for expect, dv in cases.items():
        assert rq.delivery_outcome(dv) == expect, (dv, expect)
    # 矛盾與半套的組合一律 invalid
    for bad in ({"success": True, "skipped_reason": "w"},
                {"skipped_reason": "w"},
                {"success": "false", "skipped_reason": "w"},
                [], "foo", None):
        assert rq.delivery_outcome(bad) == rq.OUTCOME_INVALID, bad

    # 三個 consumer 都吃同一支
    wd = io.open(_ROOT / "tools" / "report_watchdog.py",
                 encoding="utf-8").read()
    assert wd.count("_rq_delivery_outcome(") >= 3, (
        "看門狗還有地方自己排 success / skipped_reason 的順序")
    mr_src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    assert "_rq.delivery_outcome(delivery)" in mr_src
    assert "_rq.delivery_outcome(dv)" in mr_src


def test_the_skip_path_alerts_on_control_plane_but_not_on_content(monkeypatch):
    """(58) 那條突變沒紅,是因為我只驗了「過濾之後剩什麼」——
    **沒有真的走過 `_control_plane_exit()`**。這條走它本身。

    刻意不寄的日子:控制面壞掉要 rc=2,而「信的內容」那類 finding
    一條都不可以觸發告警(今天本來就沒有信)。
    """
    # r9 外審後 finding 自己帶 `domain`(不再從名字猜)
    content_only = [{"code": "analysis_not_specialized", "detail": "落回 legacy",
                     "severity": "degraded", "domain": rq.DOMAIN_CONTENT},
                    {"code": "payload_over_budget", "detail": "packet 超標",
                     "severity": "defect", "domain": rq.DOMAIN_CONTENT}]
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: content_only)
    assert w._control_plane_exit("測試") == 0, (
        "刻意不寄的日子拿「信的內容」報警 —— 那會製造假警報")

    mixed = content_only + [{"code": "manifest_schema_invalid",
                             "severity": "defect", "detail": "版本壞掉",
                             "domain": rq.DOMAIN_CONTROL_PLANE}]
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: mixed)
    assert w._control_plane_exit("測試") == 2, "控制面壞掉卻沒有告警"

    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: [])
    assert w._control_plane_exit("測試") == 0


def test_the_contract_lives_in_one_module():
    """r8:`delivery` 的契約搬進 `delivery_contract.py`。

    這是「上限不得超過 1000」那道硬閘門擋下來的**第一次** —— 它逼出了
    這次搬移,而不是第六次調高數字。承諾寫成註解會被忽略,寫成會紅的
    測試不會。

    搬走的是**自足**的那一塊(只用 isinstance,沒有任何模組層 state);
    剛穩定下來的 SLA 判定照外審的順序留到 9/2 自然證據之後。
    """
    import delivery_contract as dc
    # re-export 不可以是「另外複製一份」
    assert rq.delivery_outcome is dc.delivery_outcome
    assert rq.delivery_success is dc.delivery_success
    assert rq.OUTCOME_INVALID == dc.OUTCOME_INVALID

    src = io.open(_ROOT / "delivery_contract.py", encoding="utf-8").read()
    assert "def delivery_outcome" in src and "def delivery_success" in src
    # 自足:不可以把模組層 state 或別的判準一起拖過來
    assert "MANIFEST_SCHEMA" not in src, "把不屬於這個契約的東西搬過來了"
    assert "import" not in src.split('"""')[2], (
        "這個模組應該是自足的 —— 出現 import 就要重新檢查它的邊界")

    rq_src = io.open(_ROOT / "run_quality.py", encoding="utf-8").read()
    assert "from delivery_contract import" in rq_src
    assert "def delivery_outcome" not in rq_src, "搬走了卻又留了一份"


def test_the_assessor_is_a_consumer_too():
    """r8 外審第二輪:我把看門狗與 `morning_report` 都換成了五態狀態機,
    **卻漏了判準自己** —— 於是矛盾的紀錄在看門狗被拒絕,在判準卻仍被
    當成「成功」而拿去判 SLA:同一份 state 兩種結論,
    正是收斂狀態機要消滅的那件事。

    ★而我的測試只驗了原始碼字串(誰呼叫了什麼),沒驗 `assess()` 的行為★
    —— 這條走行為。
    """
    def _codes(dv):
        return {f["code"]: f["severity"] for f in rq.assess({
            "date": "2026-09-02 05:20", "manifest_schema": 2,
            "llm": {"analysis_origin": "legacy"}, "delivery": dv})}

    at = {"delivered_at": "2026-09-02T09:30:00+08:00",
          "first_delivered_at": "2026-09-02T09:30:00+08:00"}
    for bad in (dict(at, success=True, skipped_reason="w"),
                dict(at, success="false"),
                {"skipped_reason": "w"},
                dict(at, success=1)):
        codes = _codes(bad)
        assert codes.get("delivery_state_invalid") == "defect", (bad, codes)
        assert "delivery_sla_missed" not in codes, (
            "拿自相矛盾的紀錄判了 SLA", bad, codes)

    # 正常的三種結局不可以誤報
    assert "delivery_state_invalid" not in _codes(
        {"attempted": False, "success": False,
         "skipped_reason": "weekend_no_new_content"})
    late = _codes(dict(at, success=True, attempted=True))
    assert "delivery_state_invalid" not in late
    assert late.get("delivery_sla_missed") == "defect", "遲到還是要抓"
    ok = _codes({"success": True, "attempted": True,
                 "delivered_at": "2026-09-02T05:30:00+08:00",
                 "first_delivered_at": "2026-09-02T05:30:00+08:00"})
    assert not any(c.startswith("delivery") for c in ok), ok

    # 三個 consumer 對**同一份** state 說同一句話
    contradictory = dict(at, success=True, skipped_reason="w")
    assert rq.delivery_outcome(contradictory) == rq.OUTCOME_INVALID
    assert "delivery_state_invalid" in _codes(contradictory)
    import datetime as dt
    assert mr._manifest_delivery_verdict(
        {"date": "2026-09-02 05:07", "delivery": contradictory},
        dt.datetime(2026, 9, 2, 5, 20, tzinfo=mr.TPE)) == ""


def test_every_finding_declares_its_domain():
    """r9 外審:分類**不可以從名字猜**。實測 47 個 finding code 裡只有 7 個
    符合舊的前綴表,而 `fetch_plan_no_clusters` / `payload_over_budget` /
    `phantom_refs` / `event_extractor_partial` / `watch_dropped_capacity`
    這些明顯是內容管線的 finding 全都會被當成控制面 —— 在「今天刻意不寄」
    的日子就是假警報。

    這道守衛掃 `run_quality.py` 裡**所有** `add("...")` 的字面 code,
    要求每一個都在宣告表裡(前綴家族除外)。空集合不算通過。
    """
    import re
    src = io.open(_ROOT / "run_quality.py", encoding="utf-8").read()
    codes = set(re.findall(r'add\("([a-z0-9_:]+)"', src))
    assert len(codes) >= 40, ("掃描器疑似失配,只找到", sorted(codes))
    # **動態產生的 code 也要算進來**(r10 外審):守衛先前只掃字面
    # `add("...")`,而 `_ALARMING` 那三個是 `add(_label, ...)` 產生的 ——
    # 它們從來沒被這道守衛檢查過,全部落到「沒登記 → 預設控制面」。
    # 「所有字面 add() 都登記」成立,不等於「所有可能產生的 finding
    # 都登記」;閉世界斷言要涵蓋工廠。
    codes |= set(re.findall(r'"([a-z0-9_]+)":\s*\(\s*"(?:defect|degraded)"',
                            src))
    # **守衛自己要掃得到那些工廠**:少了這一句,拿掉上面的動態掃描也不會
    # 讓任何測試變紅(那三個 code 剛好都登記了)—— 反例被前置條件擋住,
    # 而下一個忘記登記的動態 code 就會靜靜溜過去。
    for dyn in ("story_ledger_corrupt", "delivery_receipt_publish",
                "analysis_recap_unreadable"):
        assert dyn in codes, (
            f"守衛掃不到動態產生的 {dyn} —— 閉世界斷言漏了工廠")
    prefixes = tuple(p for p, _ in rq._DOMAIN_PREFIXES)
    missing = sorted(c for c in codes
                     if c not in rq._FINDING_DOMAINS
                     and not c.startswith(prefixes))
    assert not missing, (
        f"這些 finding 沒有宣告 domain:{missing} —— "
        "沒登記會落到控制面(多吵一次),但那不是可以長期依賴的預設")
    # 分類本身要有意義:兩類都不可以是空的
    doms = set(rq._FINDING_DOMAINS.values())
    assert doms == {rq.DOMAIN_CONTROL_PLANE, rq.DOMAIN_CONTENT}, doms
    # 外審點名的那幾個一定要在內容類(它們正是舊前綴表漏掉的)
    for c in ("fetch_plan_no_clusters", "payload_over_budget", "phantom_refs",
              "event_extractor_partial", "watch_dropped_capacity",
              "manifest_incomplete", "namespace_unrealizable"):
        assert rq.finding_domain(c) == rq.DOMAIN_CONTENT, c
    # 而 assess() 真的把 domain 帶在每一條 finding 上
    for f in rq.assess({"date": "2026-09-02 05:20", "manifest_schema": 2,
                        "llm": {"analysis_origin": "legacy"},
                        "delivery": {"success": True, "attempted": True}}):
        assert f.get("domain") in (rq.DOMAIN_CONTROL_PLANE,
                                   rq.DOMAIN_CONTENT), f


def test_the_record_defects_do_not_rewrite_the_outcome():
    """r9 外審:**「有沒有寄出」與「這份紀錄本身合不合法」是兩個維度。**

    `attempted` 是輔助 metadata。它壞掉不可以改寫結局 —— 明確的
    `success: true` 是很強的「已寄出」證據,因為旁邊的欄位不一致就判成
    INVALID → rc=1 → 自動補寄,那是把「metadata 壞了」變成**真的重複
    寄信**(收不回來的那一邊)。所以瑕疵只進 `defects`,由品質判準報。
    """
    import delivery_contract as dc
    # 結局不變,但留下瑕疵
    for dv, want_defect in (
            ({"attempted": False, "success": True},
             dc.DEFECT_ATTEMPTED_VS_DELIVERED),
            ({"attempted": "yes", "success": True},
             dc.DEFECT_ATTEMPTED_INVALID)):
        outcome, defects = dc.delivery_verdict(dv)
        assert outcome == dc.OUTCOME_DELIVERED, (dv, outcome)
        assert want_defect in defects, (dv, defects)
    skipped = dc.delivery_verdict(
        {"attempted": True, "success": False, "skipped_reason": "w"})
    assert skipped[0] == dc.OUTCOME_SKIPPED
    assert dc.DEFECT_ATTEMPTED_VS_SKIPPED in skipped[1]
    # 完全正常的兩種結局不留瑕疵
    assert dc.delivery_verdict({"attempted": True, "success": True}) == (
        dc.OUTCOME_DELIVERED, ())
    assert dc.delivery_verdict(
        {"attempted": False, "success": False, "skipped_reason": "w"}) == (
        dc.OUTCOME_SKIPPED, ())

    # 判準把瑕疵報成 degraded(不是 defect —— 信確實寄出去了)
    codes = {f["code"]: f["severity"] for f in rq.assess({
        "date": "2026-09-02 05:20", "manifest_schema": 2,
        "llm": {"analysis_origin": "legacy"},
        "delivery": {"attempted": False, "success": True,
                     "delivered_at": "2026-09-02T05:30:00+08:00",
                     "first_delivered_at": "2026-09-02T05:30:00+08:00"}})}
    assert codes.get(
        "delivery_record_attempted_false_but_delivered") == "degraded", codes
    assert "delivery_state_invalid" not in codes, (
        "metadata 不一致被升級成「說不出寄了沒」—— 那會觸發補寄", codes)


def test_a_skip_reason_must_be_a_string():
    """r9 外審:`bool(str(dv.get("skipped_reason") or "").strip())` ——
    `str()` 會把 `1` / `True` / `["..."]` / `{...}` 全部變成非空字串,
    於是壞掉的型別被合法化成「刻意不寄」,看門狗就不會補寄。
    這與上一輪修掉的 `success="false"` 是**完全同族**的問題:
    文件宣稱嚴格的狀態機,實作卻又做 coercion。

    這個欄位**決定結局**,所以型別壞掉是 INVALID(不像 `attempted`
    那種輔助 metadata 只留瑕疵)。
    """
    import delivery_contract as dc
    for junk in (1, True, ["weekend_no_new_content"], {"foo": "bar"}, 0.5):
        dv = {"attempted": False, "success": False, "skipped_reason": junk}
        assert dc.delivery_outcome(dv) == dc.OUTCOME_INVALID, (junk, dv)
    # 真正的字串照舊;空白字串等於沒有理由
    assert dc.delivery_outcome({
        "attempted": False, "success": False,
        "skipped_reason": "weekend_no_new_content"}) == dc.OUTCOME_SKIPPED
    assert dc.delivery_outcome({
        "attempted": True, "success": False,
        "skipped_reason": "   "}) == dc.OUTCOME_FAILED


def test_the_gate_pushed_out_two_more_boundaries():
    """r9:1000 行閘門**第二次**擋下「再加一點」。

    這批加了 domain 分類表(47 個 code)之後 `run_quality.py` 到 1098 行,
    閘門不接受第七次調高數字 —— 於是搬出兩個自足的邊界:
    `finding_domains.py`(分類登記表)與 `delivery_sla.py`(期限原語)。

    **判準本體仍在 `run_quality`**:`assess()` 裡產生
    `delivery_sla_missed` / `run_delivered_after_target` 的那段與 `add()`
    閉包綁著,要拆得再動一次結構 —— 那應該獨立成一批(這一批已經有
    四條行為修正)。誠實少搬。
    """
    import delivery_sla as ds
    import finding_domains as fd
    assert rq.finding_domain is fd.finding_domain
    assert rq._sla_business_day is ds._sla_business_day
    assert rq.MANIFEST_SCHEMA_REQUIRED_FROM == ds.MANIFEST_SCHEMA_REQUIRED_FROM

    rq_src = io.open(_ROOT / "run_quality.py", encoding="utf-8").read()
    assert len(rq_src.splitlines()) <= 1000, "又長回閘門之上了"
    # 搬走的不可以留第二份
    for gone in ("def finding_domain", "def _sla_business_day",
                 "def _to_sla_tz"):
        assert gone not in rq_src, f"{gone} 搬走了卻又留了一份"
    # 判準本體確實還在(這次刻意沒搬)
    assert 'add("delivery_sla_missed"' in rq_src
    # `KNOWN_DEGRADED` 不屬於期限原語 —— 第一次切片把它一起搬走了
    assert "KNOWN_DEGRADED = frozenset" in rq_src
    assert "KNOWN_DEGRADED" not in io.open(
        _ROOT / "delivery_sla.py", encoding="utf-8").read()


def test_a_degraded_only_day_is_not_an_incident(monkeypatch):
    """r10 外審(**9/2 自然重現**):`recap_not_previous_session` 是 degraded,
    而且是**前一天** Luna 落 legacy 的合理後果。它讓看門狗 09:27 又寄了
    一封與主班 07:42 **完全相同**的品質信,並把 Actions 上的看門狗染紅
    —— 而那天 07:37 準時寄達、SLA 過、特化路徑過、state 契約過。

    紅色的「Morning Report Watchdog」很容易被讀成「今天出事了」。
    """
    # **一定要注入 `get_json`**:r11 之後,只有降級時判準會去查主班的
    # `alert-on-quality` job 有沒有真的寄成 —— 不注入就會真的碰網路,
    # 而且查不到會退到 rc=4,測試看起來像壞掉。
    def _rc(findings):
        monkeypatch.setattr(w, "quality_findings", lambda *a, **k: findings)
        monkeypatch.setattr(w, "_manifest_run_id", lambda: "1")
        return w._quality_exit("2026-09-02 07:14", get_json=lambda url: {
            "jobs": [{"name": "alert-on-quality", "status": "completed",
                      "conclusion": "success"}]})

    assert _rc([]) == w.RC_OK
    degraded = [{"code": "recap_not_previous_session", "severity": "degraded",
                 "detail": "昨日觀點停在 08-31", "domain": rq.DOMAIN_CONTENT}]
    assert _rc(degraded) == w.RC_QUALITY_DEGRADED, "降級被當成缺陷"
    defect = degraded + [{"code": "luna_rejected", "severity": "defect",
                          "detail": "x", "domain": rq.DOMAIN_CONTENT}]
    assert _rc(defect) == w.RC_QUALITY_DEFECT, "有缺陷卻被降級處理"

    # workflow:只有 rc=1/2 才寄信與染紅
    import yaml
    wf = yaml.safe_load(io.open(
        _ROOT / ".github" / "workflows" / "report-watchdog-b.yml",
        encoding="utf-8").read())
    steps = {s.get("name") or "": s for j in wf["jobs"].values()
             for s in j.get("steps", [])}
    for name in ("Alert", "Fail the run so it is visible in the Actions list"):
        cond = " ".join(steps[name]["if"].split())
        assert "rc == '1'" in cond and "rc == '2'" in cond, (name, cond)
        assert "rc != '0'" not in cond, (
            f"{name} 又變回「只要不是 0 就當事故」", cond)
    assert "rc == '1'" in steps["Auto rescue"]["if"], "補寄的條件被動到了"


def test_the_domain_registry_covers_the_dynamic_factories():
    """r10 外審:守衛先前只掃字面 `add("...")`,而 `_ALARMING` 那三個是
    `add(_label, ...)` 產生的 —— **它們從來沒被這道守衛檢查過**,
    全部落到「沒登記 → 預設控制面」。

    「所有字面 `add()` 都登記」成立,不等於「所有可能產生的 finding
    都登記」。而 `analysis_recap_unreadable` 是**內容連續性**
    (明天的昨日觀點會缺),在刻意不寄的日子不該拿它報警。
    """
    for code, want in (("story_ledger_corrupt", rq.DOMAIN_CONTROL_PLANE),
                       ("delivery_receipt_publish", rq.DOMAIN_CONTROL_PLANE),
                       ("analysis_recap_unreadable", rq.DOMAIN_CONTENT)):
        assert code in rq._FINDING_DOMAINS, f"{code} 沒有明確登記"
        assert rq.finding_domain(code) == want, code


def test_rc3_only_applies_where_its_premise_holds(monkeypatch):
    """r10 外審第二輪(**我引入的**):rc=3 的意思是「主班收尾時已經寄過
    同一封了,不必說第二遍」—— 而主班的品質自評條件是
    `run_outcome == 'delivered'`,**刻意不寄的日子根本不跑**。

    在 `_control_plane_exit()` 回 3 就是把控制面的問題降成一行綠色的
    job log,沒有人會知道。前提不成立的地方不能套用同一個結論。
    """
    def _skip(findings):
        monkeypatch.setattr(w, "quality_findings", lambda *a, **k: findings)
        return w._control_plane_exit("2026-09-06 05:20")

    # 刻意不寄 + 控制面 finding(**降級也算**)→ 一律告警並染紅
    for sev in ("degraded", "defect"):
        rc = _skip([{"code": "manifest_schema_unsupported", "severity": sev,
                     "detail": "x", "domain": rq.DOMAIN_CONTROL_PLANE}])
        assert rc == w.RC_QUALITY_DEFECT, (sev, rc)
    # 內容類仍然濾掉(今天本來就沒有信)
    assert _skip([{"code": "analysis_not_specialized", "severity": "degraded",
                   "detail": "x", "domain": rq.DOMAIN_CONTENT}]) == w.RC_OK
    assert _skip([]) == w.RC_OK

    # 而寄成功那條路上,前提成立 —— rc=3 照舊
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: [
        {"code": "recap_not_previous_session", "severity": "degraded",
         "detail": "x", "domain": rq.DOMAIN_CONTENT}])
    monkeypatch.setattr(w, "_manifest_run_id", lambda: "1")
    assert w._quality_exit("2026-09-02 07:14", get_json=lambda url: {
        "jobs": [{"name": "alert-on-quality", "status": "completed",
                      "conclusion": "success"}]}
    ) == w.RC_QUALITY_DEGRADED

    # 前提本身要釘住:主班的品質自評確實只在 delivered 時跑
    import yaml
    wf = yaml.safe_load(io.open(
        _ROOT / ".github" / "workflows" / "morning-report-b.yml",
        encoding="utf-8").read())
    quality = [s for s in wf["jobs"]["send-report"]["steps"]
               if "品質自評" in (s.get("name") or "")][0]
    assert "run_outcome == 'delivered'" in quality["if"], (
        "主班品質自評的條件變了 —— rc=3 的前提要重新檢查", quality["if"])


def test_dedupe_requires_an_acknowledged_delivery(monkeypatch):
    """r11 外審:`rc=3` 的理由是「主班收尾時已經自評**並告警**過」。
    前半句有證據(判準跑過);後半句**沒有** —— `alert-on-quality` 是獨立
    job,SMTP 失敗或缺憑證時它會紅,而看門狗完全不知道,照樣認定
    「已經通知過」而放棄第二條通知路。

    **去重要基於「收到了」,不是基於「應該收到了」。**
    """
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: [
        {"code": "recap_not_previous_session", "severity": "degraded",
         "detail": "x", "domain": rq.DOMAIN_CONTENT}])
    monkeypatch.setattr(w, "_manifest_run_id", lambda: "33570065708")

    naps = []

    def _jobs(payload):
        return lambda url: payload

    def _exit(payload):
        """**明確注入 sleeper**:pending 的重試預設 2×20 秒,而目前不會慢
        只是因為 `conftest` 為了別的目的 patch 了全域 `time.sleep` ——
        依賴那個巧合的話,conftest 一改這條測試就會等 160 秒。"""
        return w._quality_exit("x", get_json=_jobs(payload),
                               sleep=naps.append)

    ok = w._quality_exit("x", get_json=_jobs(
        {"jobs": [{"name": "alert-on-quality", "status": "completed",
                   "conclusion": "success"}]}))
    assert ok == w.RC_QUALITY_DEGRADED, "確認寄成了卻還是重寄"

    for payload in ({"jobs": [{"name": "alert-on-quality",
                               "status": "completed",
                               "conclusion": "failure"}]},
                    {"jobs": [{"name": "alert-on-quality", "status": "completed",
                               "conclusion": "cancelled"}]},
                    {"jobs": [{"name": "send-report", "status": "completed",
                               "conclusion": "success"}]},   # 那個 job 沒跑
                    {"jobs": []}, {}):
        rc = _exit(payload)
        assert rc == w.RC_QUALITY_DEGRADED_UNSENT, (payload, rc)

    # API 查不到也要當成「沒送成」—— 不可以因為查詢失敗就推定已通知
    def _boom(url):
        raise OSError("no network")
    assert w._quality_exit("x", get_json=_boom,
                           sleep=naps.append) == w.RC_QUALITY_DEGRADED_UNSENT
    # manifest 沒有 run_id 同理
    monkeypatch.setattr(w, "_manifest_run_id", lambda: "")
    assert w._quality_exit("x", get_json=_jobs(
        {"jobs": [{"name": "alert-on-quality", "status": "completed",
                   "conclusion": "success"}]})
    ) == w.RC_QUALITY_DEGRADED_UNSENT

    # workflow:rc=4 要寄信,但不算事故
    import yaml
    wf = yaml.safe_load(io.open(
        _ROOT / ".github" / "workflows" / "report-watchdog-b.yml",
        encoding="utf-8").read())
    steps = {s.get("name") or s.get("id"): s for j in wf["jobs"].values()
             for s in j.get("steps", [])}
    alert = " ".join(steps["Alert"]["if"].split())
    assert "rc == '4'" in alert, alert
    fail = " ".join(
        steps["Fail the run so it is visible in the Actions list"]["if"].split())
    # **正面比對**:寫成「`rc == '4'` 不在裡面」量不到規則 ——
    # 退回 `rc != '0'` 的話那個否定式照樣成立(突變驗證抓到的白測)。
    assert fail == ("steps.check.outputs.rc == '1' || "
                    "steps.check.outputs.rc == '2'"), (
        "只有降級不該染紅,而條件要明確列舉", fail)
    # 查 API 要有 token —— 少了它會天天查不到而多寄一封
    assert "GITHUB_TOKEN" in (steps["Check last run"].get("env") or {}), (
        "判準查不到 job conclusion 就會退到 rc=4,每天多寄一封")


def _run_alert_script(rc, **env):
    """把 workflow 內嵌的告警腳本**真的執行一次**,回傳組出來的信。

    r11 外審第二輪:先前只驗 YAML 的 `if` 條件 —— 那量不到「rc=4 會走到
    哪一條路由」。新增 rc=4 之後它其實會寄給一般收件人、主旨宣告一場
    沒有發生的事故(「今天的晨報可能沒有跑起來」),而信明明已經寄達。
    條件對了不代表內容對。
    """
    import os
    import sys as _s
    import textwrap
    import types
    wf = io.open(_ROOT / ".github" / "workflows" / "report-watchdog-b.yml",
                 encoding="utf-8").read()
    seg = wf[wf.index("- name: Alert"):]
    seg = seg[:seg.index("- name: Fail the run")]
    body = seg[seg.index("python - <<'PY'") + len("python - <<'PY'"):]
    body = body[:body.index("\n          PY")]

    captured = []

    class _SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, m):
            captured.append(m)

    fake = types.ModuleType("smtplib")
    fake.SMTP = _SMTP
    old_mod = _s.modules.get("smtplib")
    _s.modules["smtplib"] = fake
    old_env = dict(os.environ)
    try:
        os.environ.update({"GMAIL_USER": "u", "GMAIL_APP_PASSWORD": "p",
                           "RECIPIENT": "reader@example.com",
                           "QUALITY_RECIPIENT": "ops@example.com",
                           "WATCHDOG_RC": str(rc),
                           "WATCHDOG_DETAIL": "細節", **env})
        try:
            exec(compile(textwrap.dedent(body), "<alert>", "exec"), {})
        except SystemExit:
            pass
    finally:
        os.environ.clear()
        os.environ.update(old_env)
        if old_mod is not None:
            _s.modules["smtplib"] = old_mod
        else:
            _s.modules.pop("smtplib", None)
    return captured[0] if captured else None


def test_the_degraded_resend_does_not_announce_a_fake_incident():
    """r11 外審第二輪:rc=4 走錯路由會寄給一般收件人,主旨說
    「今天的晨報可能沒有跑起來」—— 而信明明已經寄達,只是主班那封
    品質信沒送成。**誤導性告警比沒有告警更難查。**"""
    m = _run_alert_script(4)
    assert m is not None
    assert m["To"] == "ops@example.com", ("rc=4 寄給了一般收件人", m["To"])
    assert "晨報品質" in m["Subject"], m["Subject"]
    assert "沒有跑起來" not in m["Subject"], (
        "宣告了一場沒有發生的事故", m["Subject"])
    text = m.get_content()
    assert "有寄到" in text and "不是事故" in text, text[:200]

    # 另外兩條路不可以被這次改動帶壞
    q = _run_alert_script(2)
    assert q["To"] == "ops@example.com" and "但有段落沒跑成" in q["Subject"]
    # **rc=4 與 rc=2 說的是不同的事**:前者「信沒問題,是通知沒送成」,
    # 後者「信有段落沒跑成」。只斷言「都屬於品質類」的話,rc=4 掉到
    # rc=2 的分支也量不出來(突變驗證抓到的白測)。
    assert m["Subject"] != q["Subject"], (
        "rc=4 用了 rc=2 的主旨 —— 讀信的人會以為今天的信有缺", m["Subject"])
    assert "沒送成" in m["Subject"] and "沒送成" not in q["Subject"]
    assert m.get_content() != q.get_content()
    n = _run_alert_script(1)
    assert n["To"] == "reader@example.com" and "沒有跑起來" in n["Subject"]


def test_the_degraded_resend_cannot_turn_the_job_red():
    """rc=4 的契約是「只有降級,不是事故」,而**缺憑證正是它的觸發情境
    之一** —— 補寄再失敗一次就把 job 弄紅,等於自己違反自己的契約。
    rc=1/2 維持原本的語意(告警寄不出去就是要紅)。"""
    import yaml
    wf = yaml.safe_load(io.open(
        _ROOT / ".github" / "workflows" / "report-watchdog-b.yml",
        encoding="utf-8").read())
    alert = [s for j in wf["jobs"].values() for s in j.get("steps", [])
             if (s.get("name") or "") == "Alert"][0]
    coe = str(alert.get("continue-on-error") or "")
    assert "rc == '4'" in coe, ("rc=4 的寄送失敗仍會染紅", coe)
    assert "rc == '1'" not in coe and "rc == '2'" not in coe, (
        "把 rc=1/2 的失敗也吞掉了 —— 告警寄不出去必須是紅的", coe)


def test_a_pending_alert_is_not_a_failed_one():
    """r12 外審:「還沒跑完」不是「沒送成」。

    `alert-on-quality` 依賴 `send-report`,兩者之間天生有幾秒空窗 ——
    9/2 的實際時間是 `send-report` 07:42:29 完成、alert job 07:42:30 建立、
    07:42:37 寄完。看門狗若在那幾秒之間查到 `queued` / `in_progress`,
    先前會判「沒送成」而補一封,然後主班自己也寄成功
    —— **剛修掉的重複告警在 unlucky timing 下回來**。
    """
    J = lambda st, con=None, extra=(): {  # noqa: E731
        "jobs": [{"name": "alert-on-quality", "status": st,
                  "conclusion": con}, *extra]}
    # 三態本身
    assert w.producer_alert_state("1", lambda u: J("completed", "success")) == (
        w.ACK_SENT)
    for st in ("queued", "in_progress", "waiting"):
        assert w.producer_alert_state("1", lambda u, s=st: J(s)) == (
            w.ACK_PENDING), st
    assert w.producer_alert_state("1", lambda u: J("completed", "failure")) == (
        w.ACK_UNSENT)
    # API 出錯是**可重試**的狀態,不是「確定沒送成」
    def _boom(url):
        raise OSError("no network")
    assert w.producer_alert_state("1", _boom) == w.ACK_PENDING
    # 那個 job 還沒出現,但本次執行還有別的 job 在跑 → 它可能還沒建立
    assert w.producer_alert_state("1", lambda u: {
        "jobs": [{"name": "send-report", "status": "in_progress"}]}) == (
        w.ACK_PENDING)
    # **「目前的 job 都 completed」證明不了「整個 run 結束了」**
    # (r12:我原本的測試把這個錯誤推論釘住了)。r13 補上正確的問法 ——
    # 去查**整個 run** 的終局狀態:還在跑 → pending;已 completed →
    # alert job 確定不會再出現 → unsent。
    only_send = {"jobs": [{"name": "send-report", "status": "completed",
                           "conclusion": "success"}]}
    def _api(run_status):
        return lambda url: ({"status": run_status} if "/jobs" not in url
                            else only_send)
    assert w.producer_alert_state("1", _api("in_progress")) == w.ACK_PENDING
    assert w.producer_alert_state("1", _api("completed")) == w.ACK_UNSENT

    # 有界重試:pending → 等 → 成功(r13 之後回**四態字串**,不是布林)
    seq = iter([J("in_progress"), J("completed", "success")])
    naps = []
    assert w.producer_alert_delivered(
        "1", lambda u: next(seq), sleep=naps.append,
        tries=3, wait=0) == w.ACK_SENT
    assert len(naps) == 1, naps
    # 一直 pending → `unknown`(**不是** `unsent`):耐心用完是看門狗
    # 自己的預算,不是「producer 確定不會寄」這個事實。兩者都補寄,
    # 但訊息要說得出差別。
    naps.clear()
    assert w.producer_alert_delivered(
        "1", lambda u: J("in_progress"), sleep=naps.append,
        tries=3, wait=0) == w.ACK_UNKNOWN
    assert len(naps) == 2, naps


def test_running_out_of_patience_is_not_a_fact_about_the_producer():
    """r13 外審:三態把「等到逾時」折進 `unsent`,於是 rc=4 的訊息會宣稱
    「查不到成功紀錄」,而讀者無從分辨**確定沒寄**與**還在等**。

    補寄政策兩者相同(漏一封比重複一封糟),但**事實要分開記**。
    """
    J = lambda st, con=None: {  # noqa: E731
        "jobs": [{"name": "alert-on-quality", "status": st,
                  "conclusion": con}]}

    def _api(jobs, run_status):
        return lambda url: ({"status": run_status} if "/jobs" not in url
                            else jobs)

    # 耐心用完 → unknown(而 run 其實還在跑)
    assert w.producer_alert_delivered(
        "1", _api(J("in_progress"), "in_progress"),
        sleep=lambda s: None, tries=3, wait=0) == w.ACK_UNKNOWN
    # 整個 run 都結束了、告警確實失敗 → unsent
    assert w.producer_alert_delivered(
        "1", _api(J("completed", "failure"), "completed"),
        sleep=lambda s: None, tries=3, wait=0) == w.ACK_UNSENT
    # 兩者都補寄(rc=4),但看門狗說的話不一樣
    import io as _io
    src = _io.open(_ROOT / "tools" / "report_watchdog.py",
                   encoding="utf-8").read()
    assert "那一班已經結束、而品質告警沒有成功" in src
    assert "等到逾時仍查不出結果" in src
