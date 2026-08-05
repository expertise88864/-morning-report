# -*- coding: utf-8 -*-
"""**「三家媒體同時報導」不等於「三個獨立來源」**(第二十二輪 P2-2)。

## 問題

分群的 `unique_sources` 數的是**不同的來源字串**。於是:

    經濟日報 + 聯合報 + 聯合新聞網   → 3 家 → 「多方證實」
    工商時報 + 中國時報               → 2 家 → 佐證等級升一級
    三家轉載同一則中央社稿             → 3 家 → 「多家同時報導」

第一組是同一個報系的同一批編輯台;第二組同理;第三組是**同一篇稿子**
被貼了三次。三種都會讓信裡出現一句「多家媒體同時報導」——而讀者會把
那句話當成獨立查證。**佐證等級是可信度宣稱,不能靠字串去重算出來。**

## 這裡的判準

一個「獨立群組」= 一個**編輯決策**。同集團共用編輯台、通訊社稿件由
通訊社決定、聚合器根本不做編輯決策。

## 未知來源怎麼算 —— 刻意不算

不在表裡的媒體**不計入獨立數**,另外報 `unverified`。理由是兩種錯誤的
代價不對稱:

  * 高估獨立性 → 信裡出現「三家獨立證實」而事實是一篇稿子貼三次
    (**假的信心**,而讀者無從察覺);
  * 低估獨立性 → 多一句「未經其他媒體證實」的但書(**保守**,而且
    `unverified` 那個數字讓人看得出來是「沒驗」不是「驗過」)。

這與 repo 既有的「universe 抓不到時不判,而那是沒驗不是驗過」同一個
做法 —— 說得出自己驗不了什麼,比假裝驗過好。
"""
from __future__ import annotations

from typing import Optional

#: `(群組代號, 成員別名…)`。**同一個編輯台算一組。**
#:
#: 只收有把握的:誤併(把兩個真的獨立的媒體算成一組)只會少算獨立數,
#: 那是保守側;而漏收一家只是讓它進 `unverified`,同樣保守。
#: **兩種錯誤都不會製造假的信心** —— 這張表刻意往保守側偏。
OWNER_GROUPS = (
    ("udn", "聯合報", "經濟日報", "聯合新聞網", "udn", "UDN", "聯合晚報"),
    ("chinatimes", "中國時報", "工商時報", "中時新聞網", "中時電子報",
     "中時", "chinatimes"),
    ("ltn", "自由時報", "自由財經", "ltn", "LTN"),
    # 東森電視與 ETtoday 自 2016 年起是不同公司,共用品牌與人脈。
    # **不確定時歸同一組**:少算獨立數是保守側。
    ("ebc", "東森新聞", "ETtoday", "ettoday", "東森財經"),
    ("tvbs", "TVBS", "tvbs"),
    ("setn", "三立新聞", "SETN", "setn"),
    ("cnyes", "鉅亨", "鉅亨網", "cnyes", "Anue"),
    ("moneydj", "MoneyDJ", "moneydj", "理財網"),
    ("technews", "科技新報", "TechNews", "technews", "財經新報"),
    ("digitimes", "DIGITIMES", "digitimes", "電子時報"),
    ("businessweekly", "商業周刊", "商周"),
    ("wealth", "財訊"),
    ("cw", "天下雜誌", "天下"),
    # 道瓊系:WSJ / Barron's / MarketWatch 同一個編輯集團。
    ("dowjones", "華爾街日報", "Wall Street Journal", "WSJ", "wsj",
     "Barron's", "Barrons", "MarketWatch", "marketwatch"),
    ("nbcu", "CNBC", "cnbc", "NBC News"),
    ("nikkei", "日經", "Nikkei", "nikkei", "日本經濟新聞"),
    ("scmp", "南華早報", "SCMP", "scmp"),
    ("ft", "金融時報", "Financial Times", "FT"),
    ("bbc", "BBC", "bbc"),
    ("cnn", "CNN", "cnn"),
    ("nyt", "紐約時報", "New York Times", "NYT"),
)

#: **通訊社**。三家報紙轉載同一則路透稿,是**一個**編輯決策 ——
#: 獨立性歸於通訊社,不歸轉載的報紙。
WIRES = ("中央社", "CNA", "cna", "路透", "Reuters", "reuters",
         "彭博", "Bloomberg", "bloomberg", "美聯社", "AP通訊",
         "法新社", "AFP", "共同社", "Kyodo")

#: **聚合器不是來源**。它不做編輯決策,只是把別人的稿子排在一起。
AGGREGATORS = ("google", "Google News", "google news", "yahoo", "Yahoo",
               "Yahoo奇摩", "MSN", "msn", "新聞雲", "feedly")

#: **官方**。發布者本身就是事實的來源(公告、統計、決議),
#: 不需要其他媒體佐證 —— 佐證等級直接是最高的那一級。
OFFICIAL = ("中央銀行", "證交所", "臺灣證券交易所", "櫃買中心", "期交所",
            "公開資訊觀測站", "MOPS", "TWSE", "TPEx", "TAIFEX",
            "金管會", "財政部", "主計總處", "經濟部", "行政院",
            "Federal Reserve", "Fed", "FOMC", "Treasury", "SEC",
            "BLS", "BEA", "ECB", "BOJ", "日本銀行")


def _norm(name) -> str:
    return str(name or "").strip()


def _hit(name: str, table) -> str:
    """`name` 裡出現表中的哪一個別名(最長的優先 —— 「中時新聞網」
    要贏過「中時」,否則短別名會先命中而分組結果依表的順序而定)。"""
    low = name.lower()
    best = ""
    for alias in table:
        a = str(alias).lower()
        if a and a in low and len(a) > len(best):
            best = alias
    return best


def is_aggregator(name) -> bool:
    """Google:NVDA、類股-金融-台股、Yahoo 這種**查詢別名或聚合器**。"""
    n = _norm(name)
    if not n:
        return False
    if n.lower().startswith("google:") or n.startswith("類股-"):
        return True
    return bool(_hit(n, AGGREGATORS))


def is_wire(name) -> bool:
    n = _norm(name)
    return bool(n) and bool(_hit(n, WIRES))


def is_official(name) -> bool:
    n = _norm(name)
    return bool(n) and bool(_hit(n, OFFICIAL))


def owner_of(name) -> str:
    """這個發布者屬於哪一個**獨立群組**(不認得回空字串)。

    順序是刻意的:官方 → 通訊社 → 集團 → 不認得。
    官方公告即使被通訊社轉發,來源仍是官方。
    """
    n = _norm(name)
    if not n or is_aggregator(n):
        return ""
    if is_official(n):
        return "official:" + _hit(n, OFFICIAL)
    if is_wire(n):
        return "wire:" + _hit(n, WIRES)
    low = n.lower()
    best_group, best_len = "", 0
    for group in OWNER_GROUPS:
        code, aliases = group[0], group[1:]
        for a in aliases:
            al = str(a).lower()
            if al and al in low and len(al) > best_len:
                best_group, best_len = code, len(al)
    return best_group


def _names(item) -> list:
    """一則新聞可能在三個欄位裡帶發布者身分(見 `_news_source_grade`)。"""
    if not isinstance(item, dict):
        return []
    return [str(item.get("source_name") or ""), str(item.get("source") or ""),
            str(item.get("publisher") or "")]


#: 內文裡的**通訊社署名**。三家報紙各自貼一則中央社稿時,`source_name`
#: 是三家報紙 —— 而編輯決策只有一個(中央社的)。署名比發布欄位更接近
#: 真相,所以它先判。
#:
#: 「根據路透報導」這種**引述**也算:那篇稿子的事實來源仍然是路透,
#: 它不構成第二次獨立查證。往保守側偏是刻意的。
_WIRE_BYLINE = ("中央社記者", "(中央社", "(中央社", "中央社報導",
                "路透社報導", "路透報導", "根據路透", "彭博報導",
                "根據彭博", "Reuters reported", "according to Reuters",
                "Bloomberg reported", "according to Bloomberg")


def wire_byline(item) -> str:
    """這則稿子的內文有沒有通訊社署名(沒有回空字串)。"""
    if not isinstance(item, dict):
        return ""
    body = (str(item.get("title") or "") + " "
            + str(item.get("summary") or "") + " "
            + str(item.get("fulltext") or ""))[:2000]
    for mark in _WIRE_BYLINE:
        if mark in body:
            return mark
    return ""


def owner_of_item(item) -> str:
    """一則新聞的獨立群組。

    順序:**通訊社署名 → `source_name` → `source`**。署名先判的理由見
    `_WIRE_BYLINE` —— 轉載的編輯決策屬於通訊社,不屬於轉載的報紙。
    """
    mark = wire_byline(item)
    if mark:
        return "wire:" + (_hit(mark, WIRES) or mark)
    for n in _names(item):
        g = owner_of(n)
        if g:
            return g
    return ""


def independence(items: Optional[list]) -> dict:
    """這一群新聞代表**幾個獨立的編輯決策**。

    回 `{groups, count, unverified, aggregator_only}`:

      * `count` —— 認得出來的獨立群組數。**這是唯一可以寫進信裡的數字。**
      * `unverified` —— 不認得發布者的則數。**沒驗,不是驗過。**
      * `aggregator_only` —— 只查得到聚合器別名的則數(同上,但成因不同:
        那是抓取管線沒有解出真正的發布者,是**我們自己的**缺口)。
    """
    groups, unverified, agg = set(), 0, 0
    for it in (items or []):
        g = owner_of_item(it)
        if g:
            groups.add(g)
            continue
        names = [n for n in _names(it) if n.strip()]
        if names and all(is_aggregator(n) for n in names):
            agg += 1
        else:
            unverified += 1
    return {"groups": sorted(groups), "count": len(groups),
            "unverified": unverified, "aggregator_only": agg,
            # **兩個數字給兩種用途,保守的方向剛好相反。**
            #
            #   `count`     —— 寫進信裡的佐證等級。保守 = **少算**
            #                  (寧可多一句但書,不可造出假的信心)。
            #   `potential` —— 覆蓋率地板(必分析清單)。保守 = **多算**
            #                  (地板算少了,重要事件會從清單掉出去,
            #                  而那正是這個清單存在的理由)。
            #
            # 用同一個數字餵這兩個用途,必然有一邊是錯的方向。
            # 只查得到聚合器別名的不算:那個別名重複出現是抓取管線的
            # 機械行為,不代表另一個發布者。
            "potential": len(groups) + unverified}
