# -*- coding: utf-8 -*-
"""**張力算出來之後,誰能引用它的什麼**(第十八輪拆出)。

`signal_tensions` 負責**偵測**(讀行情、算門檻、產出觀測);這個模組
負責**查詢**(可引用的 ID、兩側各對應哪些證據、哪幾筆非處理不可)。

拆開的理由是第十八輪 P1-4 那個缺陷的形狀:偵測端隨手掛了一個
`market:MACRO.10Y.change_bps`,而查詢端把它原封不動交給
`evidence_ids()` 當成合法引用 —— packet 裡根本沒有那個欄位。
**產出者與查詢者是兩種責任**,而中間需要一個可以被單獨盯住的介面
(`market_refs_claimed()` 就是為了讓 packet 端核對得了而存在的)。
"""
from __future__ import annotations

from typing import Optional


def evidence_refs(detected: Optional[dict]) -> set:
    """張力自己提供的可引用 ID(`tension:*`、`market:*`、`derived:*`)。

    衍生值的**來源欄位**也一併回傳 —— 模型要能引用
    `market:MACRO.10Y.close` 來說明那 12 bps 是怎麼來的。
    """
    out: set = set()
    for it in ((detected or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        out.add(f"tension:{it.get('tension_id')}")
        out.update(str(r) for r in (it.get("evidence_refs") or []))
        for side in (it.get("left"), it.get("right")):
            if isinstance(side, dict):
                out.update(str(r) for r in (side.get("derived_from") or []))
    return out


def market_refs_claimed(detected: Optional[dict]) -> set:
    """張力宣稱的 **`market:` 路徑**(供 packet 端核對是否真的存在)。

    這是第十八輪 P1-4 的**通用防線**:張力模組給什麼 ref、
    `evidence_ids()` 就收什麼,於是一個打錯或臆造的 market 路徑
    會靜靜變成合法引用。核對的責任在 packet 端(它才知道樹長什麼樣)。
    """
    out: set = set()
    for it in ((detected or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        cand = list(it.get("evidence_refs") or [])
        for side in (it.get("left"), it.get("right")):
            if isinstance(side, dict):
                cand += list(side.get("derived_from") or [])
        out.update(str(r) for r in cand if str(r).startswith("market:"))
    return out


def sides_evidence(detected: Optional[dict]) -> dict:
    """`{"tension:<id>": (左側可引用的 refs, 右側可引用的 refs)}`。

    第十八輪 P1-5:調和一筆張力卻只引用一則不相干的新聞,形式合法而
    語意空白 —— 驗證器要能問「你引用的東西**跟這筆張力有關嗎**」,
    就需要知道兩側各自對應哪些 ref。
    """
    out = {}
    for it in ((detected or {}).get("items") or []):
        if not isinstance(it, dict):
            continue

        def _refs(side):
            side = side if isinstance(side, dict) else {}
            got = {str(side.get("evidence_ref") or "")}
            got |= {str(r) for r in (side.get("derived_from") or [])}
            return {r for r in got if r}
        out[f"tension:{it.get('tension_id')}"] = (_refs(it.get("left")),
                                                  _refs(it.get("right")))
    return out


def required_tension_ids(detected: Optional[dict]) -> set:
    """**必須被橫向綜合正面處理**的張力(stale 的不強制)。"""
    return {f"tension:{it['tension_id']}"
            for it in ((detected or {}).get("items") or [])
            if isinstance(it, dict) and it.get("kind") == "tension"
            and it.get("usable_for_inference")}


def required_gap_ids(detected: Optional[dict]) -> dict:
    """`{gap_id: 為什麼今天這一項沒有答案}` —— **逐項,不是一個總數**。

    第十八輪 P1-8:先前驗證器只問「data_gaps 是不是空的」。於是今天
    利率×科技、開盤預測×廣度、產業分歧三項全部跑不成,而模型寫一句
    「缺某公司的資本支出金額」就通過了 —— 收件人會以為那三項查過了。
    """
    d = detected if isinstance(detected, dict) else {}
    out = {name: "今天缺少這項檢查需要的行情欄位,沒有跑成"
           for name in (d.get("unavailable") or [])}
    for it in (d.get("items") or []):
        if isinstance(it, dict) and not it.get("usable_for_inference"):
            out[str(it.get("tension_id") or "")] = (
                str(it.get("caveat") or "") or "資料不同步,不能拿來推論")
    return {f"gap:{k}": v for k, v in out.items() if k}
