# -*- coding: utf-8 -*-
"""**一份 `delivery` 到底是什麼結局** —— 這個問題只有一個答案。

2026-09-01 r8 外審的診斷:「控制事實」散落成 schema / success /
skipped_reason / receipt / watchdog rc 等多組半重疊狀態機,於是同一份
state 在不同 consumer 說不同的話。這個模組是那份 canonical contract。

消費端(全部吃這裡的判定,不自己排 `if` 的順序):
  * `run_quality.assess()` —— SLA 與品質判準
  * `tools/report_watchdog.py` —— 主流程與 `fresh_conclusion()`
  * `morning_report` —— 同日冪等、收據讀取

**獨立成模組的第二個理由**:`run_quality.py` 的行數上限一天內被調高五次,
上一批加了「上限不得超過 1000」的硬閘門逼自己動手。這一批就是它擋下來的
第一次 —— 搬走的是**這一批新增的、自足的**那一塊(只用 isinstance,
沒有任何模組層 state),不是剛穩定下來的 SLA 判定(那部分照外審的順序
排在 9/2 自然證據之後)。
"""


#: 一份 manifest/收據能不能證明「寄出去了」—— **三態,不是布林**。
DELIVERY_SUCCEEDED = "succeeded"      #: `success is True`
DELIVERY_NOT_SUCCEEDED = "not_yet"    #: `success is False`,或還沒有結論
DELIVERY_SUCCESS_INVALID = "invalid"  #: 有這個欄位,但型別不是 bool


def delivery_success(dv) -> str:
    """`delivery.success` 的**三態**判定(單一定義,三個模組共用)。

    2026-09-01 r7 外審:先前每個消費端各自寫 `if dv.get("success")` ——
    truthiness。於是 `"false"` / `1` / `"no"` / `[1]` 全都被當成「寄出去了」,
    而 `"false"` 是壞掉的 state 最可能長的樣子。

    這個欄位是**控制流事實**(要不要告警、要不要自動補寄、要不要判 SLA、
    origin/main 有沒有結論),所以它要跟 `manifest_schema` 一樣做精確型別
    契約:`is True` 才算成功,`is False` 才算沒成功,其餘一律「壞掉」——
    而壞掉**不可以**被當成「沒成功」靜靜吞掉,也不可以被當成成功。
    """
    if not isinstance(dv, dict) or "success" not in dv:
        return DELIVERY_NOT_SUCCEEDED
    raw = dv["success"]
    if raw is True:
        return DELIVERY_SUCCEEDED
    if raw is False:
        return DELIVERY_NOT_SUCCEEDED
    return DELIVERY_SUCCESS_INVALID


#: `delivery` 的**終局狀態**(單一定義,所有 consumer 共用)。
OUTCOME_DELIVERED = "delivered"
OUTCOME_SKIPPED = "intentionally_skipped"
OUTCOME_FAILED = "failed"
OUTCOME_INCOMPLETE = "incomplete"     #: 還沒有結論(attempted 中間狀態)
OUTCOME_INVALID = "invalid"           #: 型別壞掉,或**互相矛盾**


def delivery_outcome(dv) -> str:
    """一份 `delivery` 到底是什麼結局 —— **一個狀態機,不是幾個 if**。

    2026-09-01 r8 外審:先前每個 consumer 各自把 `success` 與
    `skipped_reason` 排成自己的順序,於是**同一份 state 在兩處說不同的話**:
    `{"success": true, "skipped_reason": "..."}` 在看門狗主流程是
    「刻意未寄信」,在 `fresh_conclusion()` 是「今天已寄出」。
    那不是誰的順序寫錯,是**這一對欄位從來沒有被當成一個狀態**看待 ——
    矛盾的組合根本沒有人拒絕它。

    不變量(外審給的表):

        DELIVERED   success is True,  沒有 skipped_reason
        SKIPPED     success is False, skipped_reason 非空
        FAILED      success is False, 沒有 skipped_reason,attempted is True
        INCOMPLETE  還沒有結論(只有 attempted,或什麼都還沒寫)
        INVALID     型別壞掉,或**同時**宣稱寄出與刻意不寄

    「只有 `skipped_reason` 而沒有 `success: false`」也是 INVALID:
    產出端一定成對寫(`attempted=False, success=False, skipped_reason=...`),
    少一半就是這份 state 不是它寫的。
    """
    if not isinstance(dv, dict):
        return OUTCOME_INVALID
    state = delivery_success(dv)
    if state == DELIVERY_SUCCESS_INVALID:
        return OUTCOME_INVALID
    skipped = bool(str(dv.get("skipped_reason") or "").strip())
    if state == DELIVERY_SUCCEEDED:
        # **不可以同時宣稱寄出與刻意不寄**
        return OUTCOME_INVALID if skipped else OUTCOME_DELIVERED
    if skipped:
        return (OUTCOME_SKIPPED if dv.get("success") is False
                else OUTCOME_INVALID)
    if dv.get("attempted") is True and dv.get("success") is False:
        return OUTCOME_FAILED
    return OUTCOME_INCOMPLETE
