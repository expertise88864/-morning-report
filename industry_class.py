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


#: **已宣告、但不是科技股的外國個股。**
#:
#: `instrument_registry` 原本的收錄範圍是「半導體鏈與大型科技股」,所以
#: 「被宣告」本身就足以當科技的依據。2026-08-18 把涵蓋面補到 NASDAQ-100
#: 權重前段班之後,那個等式不再成立:`COST` 是零售、`TMUS` 是電信業者。
#: 這張表是**例外的宣告**,不是猜的 —— 沒被列在這裡的宣告外國個股仍算科技。
#: (台股的 `通信網路業` 算科技是另一回事:那個分類收的是網通**設備商**,
#: 不是電信業者。)
NON_TECH_FOREIGN: frozenset = frozenset({"COST", "TMUS"})


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
    if str(name or "").strip() in NON_TECH_FOREIGN:
        return False
    return ":EQUITY:" in str(cid) and not str(cid).startswith("TW:")


#: **產業級科技新聞的標題判準**(2026-08-29)。只給「無可指名主體」的
#: 新聞用 —— 有主體時公司的產業別優先,這張表不參與。宣告式關鍵字,
#: 與本模組其他判準同一個做法;寧漏勿誤(漏了只是掉到「其他類股」,
#: 誤收會把金融/航運塞進科技段)。
_TECH_HEADLINE_KEYWORDS = (
    "半導體", "晶片", "晶圓", "記憶體", "DRAM", "HBM", "NAND", "GPU",
    "CPU", "ASIC", "AI", "資料中心", "數據中心", "伺服器", "載板",
    "CCL", "銅箔基板", "PCB", "封裝", "CoWoS", "EUV", "光刻", "面板",
    # 「代工」不可裸列(r1 外審):成衣代工/製鞋代工也叫代工 ——
    # 跨產業通用詞要收**科技複合形**,與地名判準「田中鎮不收裸田中」同理
    "晶圓代工", "電子代工", "半導體代工",
    "算力", "雲端運算", "NVIDIA", "輝達", "台積電",
)


def is_tech_headline(title) -> bool:
    """這個標題是不是產業級的科技新聞(無主體時的退路判準)。

    ASCII 關鍵字要**詞邊界**:裸子字串的 `AI` 會在 `SAID`、`AIRLINE`
    裡命中 —— 與 `event_identity` 的別名比對同一個教訓。
    """
    import re as _re
    t = str(title or "")
    for k in _TECH_HEADLINE_KEYWORDS:
        if not k.isascii():
            if k in t:
                return True
        elif _re.search(r"(?<![A-Za-z0-9])" + _re.escape(k)
                        + r"(?![A-Za-z0-9])", t, _re.I):
            return True
    return False
