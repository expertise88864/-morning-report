# -*- coding: utf-8 -*-
"""**證據命名空間的單一真相來源**(第二十輪 P2-6)。

先前同一件事寫在三個地方,而三個地方說得不一樣:

  * `analysis_schema._EVIDENCE_IDS` 的說明:「三類都合法:新聞、market、tension」
    —— 實際已有十一個命名空間;
  * prompt 前段的 typed ID 清單:沒有 `fact:`,後段才另外補一段 fact 規則;
  * prompt 的「量化錨點」只列 market/derived/valuation/prediction,
    而 Python 的 advisory 已經接受 `fact:`。

於是模型同時收到「fact 是合法的新聞數字」與「量化錨點不能用 fact」。
**規則自相矛盾時,模型照哪一條做是隨機的** —— 而那種隨機性會被誤讀成
模型能力不穩。

這個檔讓 schema 說明與 prompt 從**同一份宣告**生成。改一次,兩邊一起動。
"""
from __future__ import annotations

#: `(前綴, 一句話說明, 是不是量化錨點)`。**順序就是 prompt 裡的順序。**
NAMESPACES = (
    ("n<編號>", "新聞（EVIDENCE 的 `news[].source_item_id`）", False),
    ("fact:", "新聞裡帶單位的數字（逐則列在 `numeric_facts`）", True),
    ("market:", "行情欄位（`market:QQQ.change_pct` 這種葉節點）", True),
    ("derived:", "本報算出來的衍生值（附來源欄位）", True),
    ("tension:", "訊號張力與同向訊號", False),
    ("valuation:", "00662 估值", True),
    ("prediction:", "2330 與加權的開盤預測", True),
    ("universe:", "台股個股當日漲跌", True),
    ("calibration:", "本報模型的校準狀況", False),
    ("portfolio:", "彙總曝險（只有百分比與檔數）", False),
    ("quality:", "本報今日的資料涵蓋度", False),
)

#: 可以當**量化錨點**的前綴。與上表同一份宣告 —— 兩份清單各自漂移的話,
#: prompt 要求的與 Python 接受的又會分家(這條測試第一次跑就抓到)。
#:
#: `calibration:` 與 `quality:` 刻意**不算錨點**:它們是關於**本報自己**
#: 的數字(模型校準、資料涵蓋度),不是市場量級。用它們錨住一條
#: 「這件事對台積電的影響有多大」的因果鏈,是把儀表板當成證據。
#: (`is_numeric_anchor` 另外還要求 value 真的是數字、屬於這則新聞。)
ANCHOR_PREFIXES = tuple(p for p, _, anchor in NAMESPACES if anchor)


def prompt_lines(indent: str = "  ") -> str:
    """給 prompt 用的條列。"""
    return "\n".join(f"{indent}`{p}` — {desc}" for p, desc, _ in NAMESPACES)


def schema_description() -> str:
    """給 `analysis_schema._EVIDENCE_IDS` 用的一句話說明。"""
    return ("支持這一段的 typed evidence ID。合法的命名空間:"
            + "、".join(f"`{p}`" for p, _, _ in NAMESPACES)
            + "。**不要拿新聞 ID 替行情數字背書**;編造的引用比沒有引用更危險。")


def anchor_sentence() -> str:
    """給 prompt 的量化錨點說明 —— 與 `ANCHOR_PREFIXES` 同一份宣告。"""
    return "、".join(f"`{p}`" for p in ANCHOR_PREFIXES)
