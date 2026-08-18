# -*- coding: utf-8 -*-
"""**這件事屬於科技類股還是其他類股**(2026-08-18)。

使用者要回舊版信的「科技類股 / 其他類股」兩段寫法。那個分段在舊版是
**模型自己貼的標籤**,新版要改由資料決定 —— 於是「什麼算科技」這個
判準必須只有一份,而它**本來就已經存在**:

  * 台股:`tw_universe` 每一檔帶的 `industry`(TWSE 上市公司基本資料),
    `morning_report` 早就用它挑「非科技類股的領先股」補新聞
    (`fetch_sector_leader_news`)與指定 prompt 段落
    (`assign_event_sections`)。本模組把那個集合搬過來當唯一一份,
    原本的名字改成別名 —— 兩邊分歧會讓同一檔股票在「補新聞」與
    「排版」兩處被分到不同類股。
  * 非台股個股:`instrument_registry` 宣告的美股/韓股個股,依它自己的
    說明「**收的是這份報告真的會談到的:半導體鏈與大型科技股**」——
    所以「被宣告的外國個股」本身就是科技的宣告,不需要第二張名單。
    指數 / ETF / 商品不是公司新聞的主體,一律不算(它們談的是整個市場)。

查不到產業的主體(未宣告的外國公司、總經事件)**歸其他,不猜** ——
猜錯會把國泰金放進科技板塊,那是 2026 年初自測抓到過的錯。

`assign_event_sections` 另有一份「美股覆蓋公司」清單(`GOOGLE_NEWS_COMPANIES`
的非數字標籤)供**既有路徑的 prompt 分段**使用,範圍比 registry 寬。
兩者服務不同路徑,這裡不動它;台股那一半的集合已經統一成這裡這份。
"""
from __future__ import annotations

#: TWSE 產業別裡算科技的那些。**這是全案唯一一份**
#: (`morning_report._TECH_INDUSTRIES_FOR_SECTOR_NEWS` 現在指向它)。
TECH_INDUSTRIES: frozenset = frozenset({
    "半導體業", "電腦及週邊設備業", "光電業", "通信網路業", "電子零組件業",
    "電子通路業", "資訊服務業", "其他電子業", "數位雲端",
})


def is_tech_industry(industry) -> bool:
    return str(industry or "").strip() in TECH_INDUSTRIES


def is_tech_foreign(name) -> bool:
    """**已宣告的外國個股**算科技(registry 的收錄範圍就是半導體鏈與大型科技股)。

    指數/ETF/商品回 False:它們不是「哪間公司昨天發生什麼事」的主體。
    未宣告的名字(`resolve_status` 回 `invalid`)也回 False —— 沒有依據
    就不歸類,寧可放到其他類股。
    """
    try:
        import instrument_registry as _ir
        cid, _scope, status = _ir.resolve_status(str(name or "").strip())
    except Exception:                   # noqa: BLE001 - 判準載不到就當不知道
        return False
    if status == "invalid" or not cid:
        return False
    return ":EQUITY:" in str(cid) and not str(cid).startswith("TW:")
