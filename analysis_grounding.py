# -*- coding: utf-8 -*-
"""**根據**的檢查(與 `analysis_schema` 的**形狀**檢查刻意分開)。

## 為什麼分成兩個模組

第十二輪 P1-3 的教訓正是這兩件事被混為一談:strict structured output
保證了形狀 —— 欄位齊全、型別正確、enum 合法 —— 於是很容易以為輸出「驗過了」。
但形狀完美的報告可以完全沒有根據。

實測反例(外審給的,逐字):`materiality=high` 的 `fact`、`evidence_ids=[]`、
`claim_audit=[]`。這份輸出**零問題通過驗證**,而 renderer 會把它排進
「昨夜三大重點」與「我的明確立場」寄出去 —— 讀信的人看到的是一句
語氣肯定的市場判斷,背後什麼都沒有。

缺陷的形狀是本 repo 記過的那一條:**空集合讓迴圈沒跑**。高重要性的檢查
寫在 `for c in claim_audit` 裡,`claim_audit` 空的時候整段直接跳過;
而 `key_drivers` 只驗「ID 存不存在」,沒驗「有沒有」。兩個漏洞都不會有
錯誤訊息,只會安靜地放行。

## 判準

**會進到信裡的段落,都要帶得出根據。**

不是「我挑幾個欄位來檢查」—— 欄位清單會漂移,而「這段會不會被寄出去」
不會。空物件不算有內容:「這天沒有國際盤可談」與「有話要說卻說不出根據」
是兩回事,只有後者要擋。
"""
from __future__ import annotations

#: 會被 renderer 排進信裡的段落。
RENDERED = ("executive_summary", "key_drivers", "taiwan_market",
            "global_market", "top_news_analysis", "scenario_tree",
            "contradictions", "portfolio_implications")

#: schema 裡帶 `evidence_ids` 而且會被寄出去的物件段落
#: (`key_drivers` 與 `claim_audit` 是清單,另外處理)。
EVIDENCE_BEARING = ("market_regime", "taiwan_market", "global_market")


def is_rendered(obj: dict) -> bool:
    """這份輸出有沒有東西真的會被寄出去。"""
    return any((obj or {}).get(k) for k in RENDERED)


def problems(obj: dict) -> list:
    """回傳「有話說卻說不出根據」的清單(空 = 通過)。"""
    out: list = []
    if not isinstance(obj, dict):
        return out
    for i, d in enumerate(obj.get("key_drivers") or []):
        if isinstance(d, dict) and not (d.get("evidence_ids") or []):
            out.append(f"key_drivers[{i}] 會被排進信裡卻沒有任何證據")
    for sec in EVIDENCE_BEARING:
        node = obj.get(sec)
        if isinstance(node, dict) and node and not (node.get("evidence_ids") or []):
            out.append(f"{sec} 有內容卻沒有任何證據")
    for i, n in enumerate(obj.get("top_news_analysis") or []):
        if isinstance(n, dict) and not str(n.get("source_item_id") or "").strip():
            out.append(f"top_news_analysis[{i}] 沒有指明是哪一則新聞")
    # `claim_audit` 是稽核軌跡本身。它空著時,上面每一條逐項檢查都會因為
    # 「沒有東西可迭代」而通過 —— 那是最安靜的一種假通過,所以它自己要被檢查。
    if is_rendered(obj) and not (obj.get("claim_audit") or []):
        out.append("有內容要寄出,claim_audit 卻是空的(無從稽核)")
    return out
