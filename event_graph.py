# -*- coding: utf-8 -*-
"""**事件之間的關係,由 Python 先算出來**(重構規格 Commit D)。

## 三個要解的問題

**(a) 共同驅動被當成兩個獨立證據。** 「Fed 官員鴿派發言」與「美債殖利率
回落」是同一個底層驅動的兩個表現。兩者都寫進立場、各加一次權重,
讀者看到的是「兩個獨立訊號都指向同一個方向」—— 而那是**同一件事說兩次**。
`alignment_readings.double_count_risk` 已經要求模型自己說,但**模型要先
知道哪兩件事共用驅動**才說得出來。

**(b) 逐標的的淨效果沒有人算。** 使用者的原話是要知道
「對 2330 的影響、對 0050 的影響、**利多還是利空**」。同一天可以有
一則對台積電正面、一則負面 —— 兩段各自寫完就結束了,而讀者要的是
**合起來是什麼**。

**(c) 總經事件的情境樹是分開的。** 非農就業報告不是「一件會影響台股的
事」,它是**分岔本身**:數字強 → 升息預期 → 台股承壓;數字弱 → 反之。
三個情境分支若不是條件在同一個事件上,那個情境樹講的是三件不同的事。

## 判準都是看得出來的

驅動的歸類靠一張**宣告式的表**(關鍵詞 → 驅動代號),不靠語意相似度。
與 `news_clusters` 同樣的理由:相似度模型會把「台積電法說」與
「台積電董事會」歸成一類,而那是兩件事。**漏歸類只是退回今天的行為;
誤歸類會讓一個真的獨立訊號被當成重複計權而消失。**
"""
from __future__ import annotations

from typing import Optional

#: `(驅動代號, 說明, 關鍵詞…)`。**同一個底層驅動的不同表現**。
#:
#: 只收「一個宏觀變數帶動一群現象」的那種 —— 個股層級的關聯留給模型的
#: `relates_to`(那需要語意判斷,而且誤判的代價低)。
DRIVER_TABLE = (
    ("fed_policy", "聯準會政策路徑",
     "聯準會", "Fed", "FOMC", "美聯儲", "降息", "升息", "鮑爾", "Powell",
     "點陣圖", "利率決議", "rate cut", "rate hike"),
    ("us_rates", "美債殖利率",
     "美債", "殖利率", "公債", "10年期", "Treasury yield", "yield curve"),
    ("us_labor", "美國就業",
     "非農", "就業報告", "失業率", "初領失業", "nonfarm", "payroll",
     "jobless", "unemployment"),
    ("us_inflation", "美國通膨",
     "CPI", "PCE", "核心通膨", "通膨數據", "inflation", "PPI"),
    ("tariff", "關稅與貿易措施",
     "關稅", "貿易戰", "301", "反傾銷", "tariff", "trade war"),
    ("export_control", "出口管制與制裁",
     "出口管制", "禁令", "制裁", "實體清單", "export control", "sanction",
     "entity list"),
    ("tw_policy", "台灣貨幣與財政政策",
     "央行", "理監事", "存準率", "青安", "打炒房", "選擇性信用管制"),
    ("fx_twd", "新台幣匯率",
     "新台幣", "台幣匯率", "升值", "貶值", "USDTWD"),
    ("energy", "原油與能源價格",
     "油價", "原油", "OPEC", "布蘭特", "天然氣", "crude", "Brent"),
    ("geopolitics", "地緣衝突",
     "台海", "軍演", "戰爭", "飛彈", "衝突", "停火", "入侵",
     "war", "missile", "ceasefire"),
    ("ai_capex", "AI 資本支出循環",
     "資本支出", "capex", "AI 伺服器", "資料中心", "GPU 需求",
     "data center", "AI server"),
)

#: **驅動家族**:不同的驅動代號,但**同一條傳導鏈上的不同位置**。
#:
#: 「Fed 官員鴿派發言」是 `fed_policy`、「美債殖利率回落」是 `us_rates`、
#: 「非農低於預期」是 `us_labor` —— 三個不同的代號,而它們是同一件事的
#: 三個表現(就業數據 → 降息預期 → 殖利率)。三段各自進立場、各加一次
#: 權重,讀者看到的是「三個獨立訊號同向」,而那是**同一件事說三次**。
#:
#: 家族**只用來提醒重複計權**,不用來合併事件 —— 它們確實是三則不同的
#: 新聞、三條不同的因果鏈,只是不該被當成三次獨立的確認。
DRIVER_FAMILIES = {
    "us_monetary": ("fed_policy", "us_rates", "us_labor", "us_inflation"),
    "trade_policy": ("tariff", "export_control"),
    "tw_macro": ("tw_policy", "fx_twd"),
}

#: `驅動代號 → 家族代號`(不在任何家族裡的,自己就是一族)。
_FAMILY_OF = {d: fam for fam, ds in DRIVER_FAMILIES.items() for d in ds}

#: **總經發布**:它們不是「會影響市場的事件」,而是**分岔本身**。
#: 情境樹的三個分支要條件在同一個發布上,否則那是三件不同的事。
MACRO_RELEASE_DRIVERS = ("us_labor", "us_inflation", "fed_policy", "tw_policy")


def driver_of(text: str) -> str:
    """這段文字屬於哪一個底層驅動(認不出來回空字串)。

    **最長的關鍵詞優先**:「出口管制」要贏過「管制」,否則歸類會取決於
    表的順序 —— 而那不是一個講得出理由的判準。
    """
    t = str(text or "")
    low = t.lower()
    best_code, best_len = "", 0
    for row in DRIVER_TABLE:
        code, words = row[0], row[2:]
        for w in words:
            wl = str(w).lower()
            if wl and wl in low and len(wl) > best_len:
                best_code, best_len = code, len(wl)
    return best_code


def _cluster_text(cluster: dict, by_id: dict) -> str:
    parts = []
    for m in (cluster.get("member_source_ids") or []):
        n = by_id.get(str(m)) or {}
        parts.append(str(n.get("title") or ""))
        parts.append(str(n.get("summary") or ""))
    return " ".join(parts)


def build(clusters: Optional[list], news: Optional[list]) -> dict:
    """回 `{drivers, shared_driver_groups, macro_release_cluster_id}`。

    `shared_driver_groups` 只收**兩群以上**共用同一個驅動的 ——
    一群自己一個驅動不構成重複計權的風險。
    """
    by_id = {str(n.get("source_item_id")): n for n in (news or [])
             if isinstance(n, dict) and n.get("source_item_id")}
    labels = {row[0]: row[1] for row in DRIVER_TABLE}
    per_cluster, groups = {}, {}
    for c in (clusters or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("cluster_id") or "")
        code = driver_of(_cluster_text(c, by_id))
        if not cid or not code:
            continue
        per_cluster[cid] = code
        # **依家族分組,不依代號** —— 就業/降息預期/殖利率是同一件事的
        # 三個表現,而它們是三個代號。
        groups.setdefault(_FAMILY_OF.get(code, code), []).append(cid)
    shared = [{"driver": fam,
               "label": labels.get(fam) or "、".join(
                   labels.get(d, d) for d in DRIVER_FAMILIES.get(fam, (fam,))),
               "cluster_ids": sorted(cids),
               "member_drivers": sorted({per_cluster[x] for x in cids})}
              for fam, cids in sorted(groups.items()) if len(cids) >= 2]
    # **總經發布只挑一個。** 挑法確定性:先照 `MACRO_RELEASE_DRIVERS` 的
    # 順序(就業 > 通膨 > 央行),同一個驅動裡取最小 cluster_id。
    macro = ""
    for code in MACRO_RELEASE_DRIVERS:
        hits = sorted(cid for cid, c in per_cluster.items() if c == code)
        if hits:
            macro = hits[0]
            break
    return {
        "drivers": {cid: {"driver": code, "label": labels.get(code, code)}
                    for cid, code in sorted(per_cluster.items())},
        "shared_driver_groups": shared,
        # 空字串 = 今天沒有總經發布 —— 那時不要求聯合情境。
        "macro_release_cluster_id": macro,
        "basis": ("驅動由宣告式關鍵詞表歸類(`event_graph.DRIVER_TABLE`),"
                  "不用語意相似度:漏歸類只是退回原本的行為,"
                  "誤歸類會讓真的獨立訊號被當成重複計權而消失"),
    }


def conflicting_assets(obj: Optional[dict]) -> dict:
    """同一個標的在**不同分析單位裡方向相反** → `{asset_id: [cluster/新聞]}`。

    這是「淨效果」要求的觸發條件:兩段各自寫完就結束,而讀者要的是
    合起來是什麼。**只在真的相反時要求** —— 都同向的話,淨效果就是
    那個方向,再要一段只是湊字數。
    """
    seen: dict = {}
    for n in ((obj or {}).get("top_news_analysis") or []):
        if not isinstance(n, dict):
            continue
        sid = str(n.get("source_item_id") or "")
        for a in (n.get("affected_assets") or []):
            if not isinstance(a, dict):
                continue
            aid, d = str(a.get("asset_id") or ""), str(a.get("direction") or "")
            if aid and d in ("bullish", "bearish"):
                seen.setdefault(aid, {}).setdefault(d, []).append(sid)
    return {aid: sorted({s for ids in dirs.values() for s in ids})
            for aid, dirs in sorted(seen.items())
            if len(dirs) >= 2}
