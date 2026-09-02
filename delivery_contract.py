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


#: 紀錄本身的契約瑕疵 —— **與「有沒有寄出」是兩個維度**。
#: r9 外審:`attempted` 是輔助 metadata,它壞掉**不可以**改寫結局。
#: 明確的 `success: true` 是很強的「已寄出」證據;因為旁邊的欄位不一致
#: 就判成 INVALID → rc=1 → 自動補寄,那是把「metadata 壞了」變成
#: **真的重複寄信**(收不回來的那一邊)。所以瑕疵只進 `defects`,
#: 由品質判準報,不動控制流。
DEFECT_ATTEMPTED_INVALID = "attempted_not_boolean"
DEFECT_ATTEMPTED_VS_DELIVERED = "attempted_false_but_delivered"
DEFECT_ATTEMPTED_VS_SKIPPED = "attempted_true_but_skipped"


def _skip_reason(dv):
    """`skipped_reason` 的三態:`None`(沒有)/ 字串 / `False`(型別壞掉)。

    r9 外審:先前寫 `bool(str(dv.get("skipped_reason") or "").strip())`
    —— `str()` 會把 `1` / `True` / `["..."]` / `{...}` 全部變成非空字串,
    於是壞掉的型別被合法化成「刻意不寄」。這與上一輪修掉的
    `success="false"` 是**完全同族**的問題:文件宣稱嚴格的狀態機,
    實作卻又做 coercion。

    這個欄位**決定結局**(是不是刻意不寄),所以型別壞掉是 INVALID,
    不是瑕疵 —— 與 `attempted` 那種輔助 metadata 不同。
    """
    raw = dv.get("skipped_reason")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return False
    return raw.strip() or None


def delivery_verdict(dv):
    """→ `(結局, 契約瑕疵 tuple)` —— **兩個維度,不要壓成一個字串**。

    r9 外審:「有沒有寄出」與「這份紀錄本身是否完全合法」是兩件事。
    前者決定控制流(要不要補寄、要不要告警沒寄到),後者決定品質告警。
    """
    if not isinstance(dv, dict):
        return OUTCOME_INVALID, ()
    state = delivery_success(dv)
    if state == DELIVERY_SUCCESS_INVALID:
        return OUTCOME_INVALID, ()
    reason = _skip_reason(dv)
    if reason is False:                     # 型別壞掉 —— 說不出這是什麼結局
        return OUTCOME_INVALID, ()
    skipped = reason is not None

    defects = []
    attempted = dv.get("attempted")
    if "attempted" in dv and not isinstance(attempted, bool):
        defects.append(DEFECT_ATTEMPTED_INVALID)
        attempted = None

    if state == DELIVERY_SUCCEEDED:
        # **不可以同時宣稱寄出與刻意不寄** —— 兩個終局宣稱互斥
        if skipped:
            return OUTCOME_INVALID, tuple(defects)
        if attempted is False:
            defects.append(DEFECT_ATTEMPTED_VS_DELIVERED)
        return OUTCOME_DELIVERED, tuple(defects)
    if skipped:
        # 「刻意不寄」要有明確的 `success: false`,不能只有一半
        if dv.get("success") is not False:
            return OUTCOME_INVALID, tuple(defects)
        if attempted is True:
            defects.append(DEFECT_ATTEMPTED_VS_SKIPPED)
        return OUTCOME_SKIPPED, tuple(defects)
    if attempted is True and dv.get("success") is False:
        return OUTCOME_FAILED, tuple(defects)
    return OUTCOME_INCOMPLETE, tuple(defects)


def delivery_outcome(dv) -> str:
    """`delivery_verdict()` 的結局那一維(既有呼叫端沿用這支)。

    不變量(r8 外審給的表,r9 補上 `attempted` 與 `skipped_reason` 的型別):

        DELIVERED   success is True,  沒有 skipped_reason
        SKIPPED     success is False, skipped_reason 是**非空字串**
        FAILED      success is False, 沒有 skipped_reason,attempted is True
        INCOMPLETE  還沒有結論
        INVALID     型別壞掉,或**同時**宣稱寄出與刻意不寄

    `attempted` 的不一致**不在這裡**:它是輔助 metadata,壞掉只進
    `defects`(見 `delivery_verdict`)—— 否則會把「metadata 壞了」
    變成自動補寄,也就是真的重複寄信。
    """
    return delivery_verdict(dv)[0]
