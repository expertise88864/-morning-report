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
    # **同一課學兩次**(2026-08-11 生產):`prediction:` 在 08-08 那次
    # 之後補上了「不帶標的段」與真實欄位,而 `valuation:` 的說明還是
    # 光禿禿的「00662 估值」—— 模型於是寫出
    # `valuation:00662.implied_change_pct`(自己加了標的段,又自己發明了
    # 欄位名),兩條引用被判不存在、整份特化分析作廢。
    # 說明要說出**它到底有什麼**;守衛見 `tests/test_evidence_namespace_realizable.py`。
    ("valuation:", "00662 的估值欄位,**不帶標的段**"
                   "(`valuation:fair_price`、`valuation:implied_change_pct`、"
                   "`valuation:premium_pct`)", True),
    # **說明錯了,模型就會照著錯的猜。** 先前寫「2330 與加權的開盤預測」,
    # 而加權指數的預測其實在 `market:TAIEX_PRED.*`;模型於是造出
    # `prediction:TAIEX.pred_open`、`prediction:2330.mid`(以為要帶標的段),
    # 三條引用全被判不存在(2026-08-08 生產)。
    # **範例 ID 自己要存在。** 先前這裡寫 `prediction:pred_open` ——
    # 那個欄位從來沒有被產生過(真正的鍵是 `mid`/`last_2330`/`model1_1to1`
    # 這些)。模型照著範例寫,三條引用全被判不存在,整份特化分析作廢
    # (2026-08-10 current-head 生產驗收:10 條驗證失敗)。
    # 守衛見 `tests/test_evidence_namespace_realizable.py`:說明裡的每一個 ID
    # 都要能從代表性 packet 生得出來。
    ("prediction:", "2330 開盤預測的欄位,**不帶標的段**"
                    "(`prediction:mid`、`prediction:last_2330`);"
                    "加權指數的預測在 `market:TAIEX_PRED.*`", True),
    ("universe:", "台股個股當日漲跌", True),
    ("calibration:", "ADR→2330 開盤預測的近 N 日誤差"
                     "(`calibration:mean_abs_delta_pct`、"
                     "`calibration:by_date.<MM/DD>.delta_pct`)", False),
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


def unrealizable(ids) -> set:
    """宣告了、卻一個 ID 都生不出來的命名空間。

    2026-08-08 生產:`calibration:` 宣告著,而校準表是一整塊 markdown
    字串 —— 攤平不出葉節點,模型照宣告猜名字、五條引用全被判不存在,
    整份特化分析作廢退回舊路徑,連續多日。**宣告的同時就要能實現** ——
    資料齊全時非空是程式缺陷(測試盯);真缺資料時空掉正常(生產只記錄)。
    """
    have = {str(i) for i in (ids or ())}
    return {p for p, _, _ in NAMESPACES if p.endswith(":")
            and not any(i.startswith(p) for i in have)}
