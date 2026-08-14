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

import re

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
    # **外國央行要先於台灣央行比對**:「日本央行升息」的「央行」與
    # 「升息」會分別誤中 tw_policy 與 fed_policy(第二十三輪 P1-8)。
    # 「日本央行」四個字比對長度贏過「升息」兩個字,最長優先就能分對。
    # **沒被點名的央行會掉進台灣或 Fed**(2026-08-09 P2)。實測:
    # 「瑞士央行維持利率不變」→ `tw_policy`(裸「央行」命中台灣那一列)、
    # 「印度央行意外降息」→ `fed_policy`(「降息」兩個字比「央行」長)。
    # 兩種錯法都會讓一則外國貨幣政策進台灣的分析,而重複計權的判準
    # (`DRIVER_FAMILIES`)是建立在驅動代號上的。
    #
    # 修法**不新增機制**:名單裡的「X央行」四個字本來就贏得過裸「央行」
    # 與「降息」—— 最長優先自己會分對。名單是**宣告**,不是從「央行前面
    # 有兩個中文字」推導:「決議後央行」那種前綴會讓推導判成外國。
    #
    # **`美國央行` 刻意不收**:美國的央行就是 Fed,那一則屬於 `fed_policy`。
    ("foreign_cb", "海外央行政策",
     "日本央行", "日銀", "BOJ", "歐洲央行", "ECB", "中國央行", "人民銀行",
     "人行降準", "韓國央行", "英國央行", "英格蘭銀行",
     "瑞士央行", "印度央行", "加拿大央行", "澳洲央行", "紐西蘭央行",
     "巴西央行", "俄羅斯央行", "土耳其央行", "印尼央行", "泰國央行",
     "菲律賓央行", "越南央行", "新加坡金管局", "馬來西亞央行",
     "墨西哥央行", "阿根廷央行", "南非央行", "以色列央行", "香港金管局",
     "南韓央行", "歐元區央行", "德國央行", "法國央行"),
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

#: **發布形狀**的標題:報數字(月增/年增/高於預期)或報決議(升息/
#: 按兵不動)。「PPI 飆升拖累科技股」也在標題寫了 PPI,但那是**市場
#: 反應** —— 只憑「標題命中驅動詞」分不出兩者(外審 2026-08-14)。
#: 認不出來時退回既有的最小 ID 決勝,所以這張表漏了詞是**降級不是
#: 誤判**:頂多退回舊行為,不會把發布踢出清單。
_RELEASE_TITLE = re.compile(
    "月增|年增|月減|年減|季增|新增|高於預期|低於預期|符合預期|優於預期|"
    "遜於預期|不如預期|公布|發布|出爐|初值|終值|決議|升息|降息|按兵不動|"
    "維持利率|萬人")

#: 「CPI 年增2.9% 美股應聲大漲」**同時**報結果與報反應 —— 也命中
#: `_RELEASE_TITLE`,只憑發布詞分不出它與純發布(外審第二輪)。
#: 反應語句的受詞是**資產**(拖累科技股、道瓊應聲大漲),純發布標題
#: 沒有這些詞。命中只**降級**(輸給純發布),不出局。
_REACTION_TITLE = re.compile(
    "拖累|重挫|大漲|大跌|飆漲|勁揚|下挫|跳水|崩跌|反彈|應聲|承壓|"
    "提振|推升|激勵|衝擊|震盪|收黑|收紅|賣壓|買盤|殺盤|補漲|回檔")

#: 數據發布的標題會帶**實際數值**(月增0.9%、新增18.5萬人);
#: 「CPI 數據公布 高於預期」沒有數字,多半是轉述或反應。決議類
#: (fed_policy/tw_policy)豁免 ——「按兵不動」可以整句沒有數字。
_DATA_RELEASE_DRIVERS = frozenset({"us_labor", "us_inflation"})


#: **具名機構**的驅動代號。同樣長度時它們贏過通用動詞。
#:
#: 「日銀升息」:「日銀」與「升息」都是兩個字,而 `fed_policy` 在表裡
#: 排在前面 —— 於是日本央行的決策被歸成 Fed(2026-08-09 外審抓到)。
#: 靠調整表的順序去修等於讓歸類取決於順序,而這個函式的說明自己就寫著
#: 那不是一個講得出理由的判準。
#:
#: 講得出理由的判準是:**「誰」比「做什麼」更能決定這是誰的政策。**
#: 升息降息每一國都會做;「日銀」只有一個。
NAMED_INSTITUTION_DRIVERS = frozenset({"foreign_cb"})


def driver_of(text: str) -> str:
    """這段文字屬於哪一個底層驅動(認不出來回空字串)。

    **最長的關鍵詞優先**:「出口管制」要贏過「管制」,否則歸類會取決於
    表的順序 —— 而那不是一個講得出理由的判準。
    同樣長度時**具名機構贏過通用動詞**(見 `NAMED_INSTITUTION_DRIVERS`)。
    """
    t = str(text or "")
    low = t.lower()
    best_code, best_rank = "", (0, 0)
    for row in DRIVER_TABLE:
        code, words = row[0], row[2:]
        named = 1 if code in NAMED_INSTITUTION_DRIVERS else 0
        for w in words:
            wl = str(w).lower()
            if not wl or (len(wl), named) <= best_rank:
                continue
            # 第二十三輪 P1-8:ASCII 詞要 token 邊界 ——
            # `war` 曾命中 `award` 而被歸成地緣衝突。
            if wl.isascii():
                import re as _re2
                if not _re2.search(r"(?<![a-z0-9])" + _re2.escape(wl)
                                   + r"(?![a-z0-9])", low):
                    continue
            elif wl not in low:
                continue
            best_code, best_rank = code, (len(wl), named)
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
    # 第二十三輪 P1-8:**同一天可以有多個總經發布**(CPI + Fed 決議),
    # 只挑一個等於忽略其他發布。全部列出;第一個(照
    # `MACRO_RELEASE_DRIVERS` 的順序,同驅動取最小 ID)是**主發布**,
    # 情境樹的三個分支要條件在它上面;其餘也要被情境或重點涵蓋。
    # **一個驅動一天只有一次「發布」**(2026-08-14 生產:PPI 日全場的
    # 敘事都繞著通膨,`driver_of` 讀 title+summary 把 47+ 個事件群全歸成
    # `us_inflation` —— 於是每一群都被當成「總經發布」要求進三大重點或
    # dismissed,55 條駁回裡 47 條是這一條,結構上不可能滿足)。
    # 「被通膨帶動的台積電新聞」與「PPI 發布本身」是兩回事:前者由
    # `shared_driver_groups` 處理(共用驅動不算獨立確認),只有後者是
    # 發布。挑法:**標題自己就是那個發布**的群優先(發布新聞的標題會
    # 寫 PPI/CPI/決議;被帶動的新聞只在 summary 提到),沒有才退回
    # 最小 ID。原意「同一天可以有多個發布」指 CPI+Fed 兩個**不同**
    # 驅動 —— 每個驅動至多一筆,那一層不變。
    def _title_text(cid):
        cl = next((c for c in (clusters or [])
                   if isinstance(c, dict)
                   and str(c.get("cluster_id") or "") == cid), {})
        return " ".join(str((by_id.get(str(m)) or {}).get("title") or "")
                        for m in (cl.get("member_source_ids") or []))

    macro_all = []
    for code in MACRO_RELEASE_DRIVERS:
        cands = sorted(cid for cid, c in per_cluster.items() if c == code)
        if not cands:
            continue
        titled = [cid for cid in cands if driver_of(_title_text(cid)) == code]
        # 標題命中驅動詞的群裡,再優先挑**發布形狀**的標題 ——
        # 反應標題(「PPI 飆升拖累科技股」)也會命中驅動詞,ID 較小時
        # 會搶走真正的發布,情境樹就錨在反應而不是發布上。
        released = [cid for cid in titled
                    if _RELEASE_TITLE.search(_title_text(cid))]
        # 純發布再優先於「結果+反應」的混合標題;數據類驅動另要求
        # 標題帶實際數值。四層決勝,每一層認不出來只**退回上一層**
        # (最壞退回最小 ID)—— 表漏了詞是降級不是誤判。
        pure = [cid for cid in released
                if not _REACTION_TITLE.search(_title_text(cid))
                and (code not in _DATA_RELEASE_DRIVERS
                     or re.search(r"[0-9][0-9.,]*", _title_text(cid)))]
        macro_all.append((pure or released or titled or cands)[0])
    macro = macro_all[0] if macro_all else ""
    return {
        "drivers": {cid: {"driver": code, "label": labels.get(code, code)}
                    for cid, code in sorted(per_cluster.items())},
        "shared_driver_groups": shared,
        # 空字串 = 今天沒有總經發布 —— 那時不要求聯合情境。
        "macro_release_cluster_id": macro,
        "macro_release_cluster_ids": macro_all,
        "basis": ("驅動由宣告式關鍵詞表歸類(`event_graph.DRIVER_TABLE`),"
                  "不用語意相似度:漏歸類只是退回原本的行為,"
                  "誤歸類會讓真的獨立訊號被當成重複計權而消失"),
    }


def conflicting_asset_sides(obj: Optional[dict]) -> dict:
    """方向衝突**逐側**的新聞 ID → `{標的: {"bullish": [...], "bearish": [...]}}`。

    `conflicting_assets` 只回聯集,而聯集答得出「有沒有衝突」,答不出
    **哪幾則是利多那一側**。少了那一半,「兩側各一條主張」就只能驗
    模型自己寫的 `direction` 標籤 —— 而標籤是它自己填的:同一批利空
    新聞寫兩條主張、其中一條標成 `bullish`,形式上就滿足了兩側。
    """
    # 第二十三輪 P1-7:**`2330` 與「台積電」是同一個標的。** 原樣分組
    # 會讓「2330 bullish」與「台積電 bearish」不觸發淨效果。
    # 正規化到別名組的第一個成員(組的代表寫法)。
    import entity_alias as _ea

    _canon = _ea.canonical
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
                seen.setdefault(_canon(aid), {}).setdefault(d, []).append(sid)
    return {aid: {d: sorted(set(ids)) for d, ids in sorted(dirs.items())}
            for aid, dirs in sorted(seen.items())
            if len(dirs) >= 2}


def conflicting_assets(obj: Optional[dict]) -> dict:
    """同一個標的在**不同分析單位裡方向相反** → `{asset_id: [cluster/新聞]}`。

    這是「淨效果」要求的觸發條件:兩段各自寫完就結束,而讀者要的是
    合起來是什麼。**只在真的相反時要求** —— 都同向的話,淨效果就是
    那個方向,再要一段只是湊字數。

    (逐側的名單見 `conflicting_asset_sides`;這裡回聯集,兩者同一份判定。)
    """
    return {aid: sorted({s for ids in sides.values() for s in ids})
            for aid, sides in conflicting_asset_sides(obj).items()}
