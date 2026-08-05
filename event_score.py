# -*- coding: utf-8 -*-
"""**「昨夜三大重點」要是三個事件,不是三個數字**(重構規格 Commit C)。

## 使用者原話

> 我要的是真正國際上昨夜三大發生得重大事件 而不是數據文字堆疊

2026-08-05 那封信的第一段寫的是 QQQ 漲 1.2%、台積電 ADR 跌 0.4% ——
那些是**價格變化**,不是事件。價格變化沒有主詞、沒有動作、沒有原因;
它是別的事件造成的**結果**,而讀者想知道的是那個事件。

## 為什麼是計分而不是叫模型「挑重要的」

模型自評的重要性不能當判準 —— 這個 repo 已經為此改過一次
(`news_clusters.required_analysis` 的 `coverage_basis`)。所以三大重點的
候選由 Python **從資料本身**算出來,模型只能在候選裡挑、並且要說明。

## 多軸,不是單一分數

單一「重要性」把好幾個不同的問題壓成一個數字,而它們該分開看:

    佐證   —— 幾個獨立編輯台證實了它(官方公告直接封頂)
    廣度   —— 影響一家公司,還是整個市場
    新意   —— 今天第一次發生,還是第 5 天的追蹤稿
    在地   —— 與台股/台灣有沒有直接關係(**不是**與使用者的持股)
    量級   —— 有沒有具體數字可以判斷幅度(沒有數字就只能寫形容詞)

每一軸都算得出來、說得出理由,而且**權重是宣告的**。分數不是用來
排名次給人看的,是用來**決定哪三件事不能不談**。

## 隱私

在地軸看的是**台股市場**(台灣的上市公司、台灣的政策),
刻意**不看使用者的持股清單** —— 持股不得進 packet、prompt、state 或
log(R15b)。「與 2330 有關」是因為它是台股權值股,不是因為誰持有它。
"""
from __future__ import annotations

from typing import Optional

#: 各軸的權重。**宣告在這裡,不是散在計算式裡** —— 散開的權重改一個
#: 就沒有人看得出總分為什麼變了。合計 1.0。
WEIGHTS = {
    "corroboration": 0.28,   # 幾個獨立編輯台說了同一件事
    "breadth": 0.22,         # 影響一家公司,還是整個市場
    "novelty": 0.20,         # 今天第一次,還是第 5 天的追蹤
    "locality": 0.18,        # 與台股/台灣的直接關係
    "magnitude": 0.12,       # 有沒有具體數字可以判斷幅度
}

#: 台灣相關的判準詞。**看的是市場,不是持股**(見模組說明的隱私段)。
_TW_MARKERS = ("台股", "台灣", "臺灣", "加權", "台積", "TSMC", "櫃買",
               "證交所", "央行", "行政院", "金管會", "新台幣", "台幣",
               "經濟部", "主計總處", "TAIEX", "Taiwan", "taiwan")

#: 影響面涵蓋整個市場的判準詞(對比「某一家公司的財報」)。
_BROAD_MARKERS = ("關稅", "升息", "降息", "利率", "通膨", "CPI", "PPI",
                  "就業", "非農", "GDP", "出口管制", "制裁", "戰爭",
                  "地震", "颱風", "央行", "FOMC", "Fed", "政策",
                  "tariff", "inflation", "rate", "sanction", "war")

#: 有數字可談的判準:帶單位的數字。**不做語意判斷** —— 只看形狀。
_NUM_MARKERS = ("%", "％", "億", "兆", "萬", "美元", "元", "點", "bp",
                "basis point", "percent")


#: **價格變化的詞彙**。價格變化是別的事件造成的**結果** ——
#: 它沒有主詞、沒有動作,寫進「昨夜三大重點」就是使用者說的「數據文字堆疊」。
_PRICE_WORDS = ("收漲", "收跌", "收紅", "收黑", "漲幅", "跌幅", "走高",
                "走低", "開高", "開低", "終場", "連漲", "連跌", "收在",
                "上漲", "下跌", "重挫", "大漲", "大跌", "翻紅", "翻黑",
                "closed up", "closed down", "rose", "fell", "gained",
                "slipped", "rallied")

#: **事件的動詞**。有人做了一件事,才是事件。
#: (`is_price_move` 是「有價格詞**而且**沒有事件詞」——
#: 「央行宣布調升存款準備率」有 `調升` 也有 `宣布`,它是事件。)
_EVENT_VERBS = ("宣布", "決議", "公布", "發表", "簽署", "通過", "裁定",
                "起訴", "召回", "罷工", "地震", "颱風", "併購", "收購",
                "法說", "財報", "營收", "制裁", "管制", "禁令", "延期",
                "取消", "停產", "擴產", "得標", "下修", "上修", "調升",
                "調降", "訪問", "會談", "談判", "停火", "開戰", "辭職",
                "上任", "裁員", "增資", "減資", "分割", "上市", "下市",
                # **統計發布也是事件。** 黃金 fixture(2026-08-05)抓到:
                # 「美7月就業增幅低於預期 失業率走高」被判成價格文 ——
                # 「走高」是價格詞,而它講的是失業率,不是股價。
                # 誤殺比漏放危險,而這一類正是那天真正的頭條。
                "非農", "就業", "失業率", "通膨", "物價", "GDP", "PMI",
                "採購經理", "零售銷售", "進出口", "貿易", "景氣",
                "年增", "月增", "新增", "增幅", "降幅", "低於預期",
                "高於預期", "不如預期", "優於預期", "數據",
                "announce", "announced", "approve", "approved", "sign",
                "signed", "ruling", "recall", "strike", "earthquake",
                "merger", "acquire", "acquired", "sanction", "ban",
                # 第二十三輪 P2-6:`report`/`reported` 太廣 ——
                # 「Market report: Nasdaq rose 2%」會因此逃過價格文判定。
                "resign", "layoff", "raise", "cut")


def is_price_move(title: str) -> bool:
    """這個標題**只有價格變化**,沒有任何人做任何事。

    使用者的原話:「不是數據文字堆疊」。價格變化是結果,不是事件 ——
    讀者想知道的是造成它的那件事。
    """
    t = str(title or "")
    low = t.lower()
    has_price = any(w in t or w in low for w in _PRICE_WORDS)
    has_event = any(v in t or v in low for v in _EVENT_VERBS)
    return has_price and not has_event


def _text(members) -> str:
    return " ".join(str(m.get("title") or "") + " " + str(m.get("summary") or "")
                    for m in members if isinstance(m, dict))


def _corroboration(cluster: dict) -> float:
    """官方公告封頂;否則看**已驗證的獨立編輯台數**。

    刻意用嚴格的那個數(`independent_sources`)—— 這一軸的意思是
    「這件事有多可信」,而未驗證的來源撐不起可信度。
    """
    if cluster.get("official"):
        return 1.0
    n = int(cluster.get("independent_sources") or 0)
    return min(1.0, n / 3.0)


def _breadth(cluster: dict, body: str) -> float:
    """整個市場 > 一個產業 > 一家公司。判準是**總經/政策詞**與實體數。"""
    hits = sum(1 for m in _BROAD_MARKERS if m in body)
    ents = len({str(e) for m in (cluster.get("_members") or [])
                for e in (m.get("entities") or [])})
    return min(1.0, 0.34 * min(hits, 2) + 0.16 * min(ents, 2))


def _novelty(cluster: dict) -> float:
    """第 0 天是新事件;延續事件只剩**增量**的價值,而增量比較小。

    刻意不是 0 —— 延續事件仍然可能是今天最重要的事(戰事第 5 天的
    停火談判)。只是它的「新意」不該與第一天相同。
    """
    days = int(cluster.get("continuing_days") or 0)
    return 1.0 if days <= 0 else max(0.35, 1.0 - 0.15 * days)


def _locality(body: str) -> float:
    hits = sum(1 for m in _TW_MARKERS if m in body)
    return min(1.0, 0.4 * min(hits, 3) + (0.0 if hits else 0.0))


def _magnitude(body: str) -> float:
    """有帶單位的數字 → 幅度講得出來;沒有 → 只能寫形容詞。"""
    return 1.0 if any(m in body for m in _NUM_MARKERS) else 0.0


def score_one(cluster: dict, members: Optional[list] = None) -> dict:
    """一個事件群的多軸分數。回 `{score, axes, why}`。

    **`why` 是給人看的一句話** —— 分數本身不進信,但「為什麼這件事
    排進前三」要說得出來(否則它就只是另一個模型說了算的數字)。
    """
    c = dict(cluster or {})
    ms = [m for m in (members or []) if isinstance(m, dict)]
    c["_members"] = ms
    body = _text(ms)
    axes = {
        "corroboration": round(_corroboration(c), 3),
        "breadth": round(_breadth(c, body), 3),
        "novelty": round(_novelty(c), 3),
        "locality": round(_locality(body), 3),
        "magnitude": round(_magnitude(body), 3),
    }
    total = round(sum(WEIGHTS[k] * v for k, v in axes.items()), 4)
    top = sorted(axes.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
    return {"score": total, "axes": axes,
            "why": "、".join(f"{k}={v}" for k, v in top)}


def rank(clusters: Optional[list], news: Optional[list],
         top_n: int = 3) -> dict:
    """把事件群依多軸分數排名。回 `{ranked, top_cluster_ids, weights, basis}`。

    **完全確定性**:同分時用 `cluster_id` 決勝,而不是依輸入順序 ——
    輸入順序沒有語意,靠它決勝會讓同一天的兩次執行排出不同的三大重點。
    """
    by_id = {str(n.get("source_item_id")): n for n in (news or [])
             if isinstance(n, dict) and n.get("source_item_id")}
    ranked, price_only = [], []
    for c in (clusters or []):
        if not isinstance(c, dict):
            continue
        ms = [m for m in (by_id.get(str(x))
                          for x in (c.get("member_source_ids") or [])) if m]
        s = score_one(c, ms)
        row = {"cluster_id": str(c.get("cluster_id") or ""),
               "representative_source_id":
                   str(c.get("representative_source_id") or ""),
               **s}
        # **價格變化不是事件**,不進三大重點的候選。它仍然是行情脈絡的
        # 一部分(`market:` 命名空間裡有完整數字),只是不能佔那三格。
        if ms and all(is_price_move(m.get("title")) for m in ms):
            row["excluded"] = "price_move_only"
            price_only.append(row)
        else:
            ranked.append(row)
    ranked.sort(key=lambda r: (-r["score"], r["cluster_id"]))
    price_only.sort(key=lambda r: r["cluster_id"])
    return {
        "ranked": ranked,
        # **排除了什麼要說出來** —— 靜默的排除讀起來像「沒有這種東西」。
        "excluded_price_moves": [r["cluster_id"] for r in price_only],
        "top_cluster_ids": [r["cluster_id"] for r in ranked[:top_n]],
        "weights": dict(WEIGHTS),
        "basis": ("多軸計分(佐證/廣度/新意/在地/量級),權重宣告在 "
                  "`event_score.WEIGHTS`;同分用 cluster_id 決勝。"
                  "**候選由資料算出,不採用模型自評的重要性**"),
    }
