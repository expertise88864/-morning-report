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

缺陷的形狀是本 repo 記過的那一條:**空集合讓迴圈沒跑**。

## 判準

**會進到信裡的段落,都要帶得出根據。** 空物件不算有內容:
「這天沒有國際盤可談」與「有話要說卻說不出根據」是兩回事,只擋後者。
"""
from __future__ import annotations

#: **接受契約的版本**(第十三輪 P1-3)。這個模組決定 Luna 的輸出被不被
#: 採用、要不要修補、要不要落回 legacy —— 也就是決定 primary 成功率、
#: 成本、延遲與**信裡的內容**。它顯然是實驗系統契約的一部分,而先前
#: 它既不在同群鍵裡、也沒有行為快照:改掉 grounding 規則而不升任何版本,
#: 兩種完全不同的接受行為會被當成同一群樣本相加。
#: v2(schema v2):加 `cross_market_synthesis` —— 漏掉它的話,一段完全
#: 沒有根據的橫向綜合會照樣寄出,而它正是最容易被寫成漂亮空話的一段。
#: v3(第十五輪):接受政策多了「合法但淺 → 加深一次」(修補時機變了
#: 就是接受行為變了)。v4(P2-4):`priced_in` 也要帶證據。
#: v5(第十七輪):接受政策加「張力要有逐筆 resolution」與「鏈要走到
#: 財務層」的深度提示 —— 修補時機再次改變。
GROUNDING_VERSION = 17

#: 會被 renderer 排進信裡的段落。
RENDERED = ("executive_summary", "key_drivers", "taiwan_market",
            "global_market", "top_news_analysis", "scenario_tree",
            "contradictions", "portfolio_implications",
            "cross_market_synthesis", "priced_in")

#: schema 裡帶 `evidence_ids` 而且會被寄出去的物件段落
#: (`key_drivers` 與 `claim_audit` 是清單,另外處理)。
#: 第十六輪 P2-4:`priced_in` 已經進 RENDERED 卻不在這裡 —— 於是
#: 「市場已完全反映降息」這種**高推論性**的句子可以完全沒有證據就寄出。
#: 它比一般新聞摘要**更**需要根據,因為它宣稱的是市場的預期狀態。
EVIDENCE_BEARING = ("market_regime", "taiwan_market", "global_market",
                    "cross_market_synthesis", "priced_in")


def has_content(node: dict) -> bool:
    """這個段落**真的有話要說**嗎(`evidence_ids` 不算)。

    r1(Codex,P2):**不能用 dict 的 truthiness 判斷。** strict schema 規定
    所有欄位必填,所以資料不足那天的合法空段落是「欄位都在、值都空」
    —— 它是 truthy 的,於是「有內容卻沒有證據」會誤報,Luna 白白修補一次
    再落回 legacy,而那一段根本沒有任何文字會進信。
    **誤判的代價不是漏擋,是讓 Luna 在資料稀薄的日子看起來比較不可靠**,
    而那正是這個實驗要量的東西。
    (先前的測試用 `{}` 當空段落 —— 那不是 strict 輸出真正的形狀。)
    """
    if not isinstance(node, dict):
        return False
    for k, v in node.items():
        if k == "evidence_ids":
            continue
        if isinstance(v, str) and v.strip():
            return True
        if not isinstance(v, str) and v:
            return True
    return False


def is_rendered(obj: dict) -> bool:
    """這份輸出有沒有東西真的會被寄出去。"""
    for k in RENDERED:
        v = (obj or {}).get(k)
        if isinstance(v, dict):
            if has_content(v):
                return True
        elif isinstance(v, str):
            if v.strip():
                return True
        elif v:
            return True
    return False


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
        if has_content(node) and not (node.get("evidence_ids") or []):
            out.append(f"{sec} 有內容卻沒有任何證據")
    for i, n in enumerate(obj.get("top_news_analysis") or []):
        if isinstance(n, dict) and not str(n.get("source_item_id") or "").strip():
            out.append(f"top_news_analysis[{i}] 沒有指明是哪一則新聞")
    # `claim_audit` 是稽核軌跡本身。它空著時,上面每一條逐項檢查都會因為
    # 「沒有東西可迭代」而通過 —— 那是最安靜的一種假通過,所以它自己要被檢查。
    if is_rendered(obj) and not (obj.get("claim_audit") or []):
        out.append("有內容要寄出,claim_audit 卻是空的(無從稽核)")
    return out
