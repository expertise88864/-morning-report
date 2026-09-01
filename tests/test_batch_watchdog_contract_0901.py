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
    kept = [c for c in codes if not c.startswith(w._CONTENT_ONLY_PREFIXES)]
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
    content_only = [{"code": "analysis_not_specialized",
                     "severity": "degraded", "detail": "落回 legacy"},
                    {"code": "luna_rejected", "severity": "defect",
                     "detail": "特化輸出被擋"}]
    monkeypatch.setattr(w, "quality_findings", lambda *a, **k: content_only)
    assert w._control_plane_exit("測試") == 0, (
        "刻意不寄的日子拿「信的內容」報警 —— 那會製造假警報")

    mixed = content_only + [{"code": "manifest_schema_invalid",
                             "severity": "defect", "detail": "版本壞掉"}]
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
