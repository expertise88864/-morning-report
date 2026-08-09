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

import re as _re
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


def _alias_in(alias_low: str, name_low: str) -> bool:
    """第二十三輪 P1-4:**ASCII 別名要 token 邊界。** 裸子字串讓
    `ft`(Financial Times)命中 `SoftBank` 與 `Microsoft` —— 錯認的
    發布者會污染獨立數、佐證等級、近似去重與事件排序。
    中文別名維持子字串(中文無詞界,「鉅亨」要能命中「鉅亨網」)。"""
    if not alias_low.isascii():
        return alias_low in name_low
    return bool(_re.search(r"(?<![a-z0-9])" + _re.escape(alias_low)
                           + r"(?![a-z0-9])", name_low))


def _hit(name: str, table) -> str:
    """`name` 裡出現表中的哪一個別名(最長的優先 —— 「中時新聞網」
    要贏過「中時」,否則短別名會先命中而分組結果依表的順序而定)。"""
    low = name.lower()
    best = ""
    for alias in table:
        a = str(alias).lower()
        if a and _alias_in(a, low) and len(a) > len(best):
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
            if al and _alias_in(al, low) and len(al) > best_len:
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


#: Google News 的標題尾綴:`「標題 - 發布者」`。**發布者身分就藏在標題裡**,
#: 而先前只看 `source_name` 欄位 —— 欄位空的時候整則被記成
#: `aggregator_only`(「抓取管線沒解出發布者」),實際上解得出來。
_TITLE_TAIL = _re.compile(r"\s[-–—|]\s([^-–—|]{2,24})\s*$")


def title_publisher(item) -> str:
    """聚合器條目的標題尾綴裡認得出來的發布者(認不得回空字串)。

    **只在來源是聚合器時看標題**:一般媒體的「 - 副標」是內容,
    拿它當發布者會把「A公司財報 - 法說會前瞻」記成一家叫
    「法說會前瞻」的媒體。**只回註冊表認得的**:不認得的尾綴可能是
    副標也可能是小站,認錯的代價(假的獨立數)大於漏認(unverified)。
    """
    if not isinstance(item, dict):
        return ""
    src = str(item.get("source") or "")
    if not (src.lower().startswith("google:") or is_aggregator(src)):
        return ""
    m = _TITLE_TAIL.search(str(item.get("title") or ""))
    tail = m.group(1).strip() if m else ""
    return tail if tail and owner_of(tail) else ""


def bare_title(item) -> str:
    """分群比對用的標題:聚合器條目剝掉發布者尾綴。

    同一件事經兩家媒體出現在 Google News 時,尾綴不同(「 - 經濟日報」vs
    「 - 中時新聞網」)—— 尾綴的字元在標題重疊比對裡**懲罰合併**,
    短標題會因此掉到 0.5 門檻之下,同一件事拆成兩群。
    """
    t = str((item or {}).get("title") or "")
    return _TITLE_TAIL.sub("", t) if title_publisher(item) else t


def owner_of_item(item) -> str:
    """一則新聞的獨立群組。

    順序:**通訊社署名 → `source_name` → `source` → 聚合器標題尾綴**。
    署名先判的理由見 `_WIRE_BYLINE` —— 轉載的編輯決策屬於通訊社,
    不屬於轉載的報紙。尾綴最後判:欄位有值時欄位比標題可信。
    """
    mark = wire_byline(item)
    if mark:
        return "wire:" + (_hit(mark, WIRES) or mark)
    for n in _names(item):
        g = owner_of(n)
        if g:
            return g
    tail = title_publisher(item)
    return owner_of(tail) if tail else ""


#: 兩段式頂級網域的**第二層**(`com`.tw、`co`.uk、`ac`.jp…)。
#:
#: 上一版是把 `com.tw`/`co.uk` 這些**逐個列舉**,於是沒列到的
#: (`co.nz`、`com.br`、`co.in`…)會讓兩個不同的發布者被收成同一個
#: `co.nz` —— 而那是**少算**獨立來源、讓事件從必分析清單掉出去的方向
#: (2026-08-09 外審)。改成規則:國碼頂級網域(兩個字母)底下的
#: 這幾個第二層,一律多留一層。規則涵蓋列舉,而且不會漏掉下一個國家。
#:
#: **這是啟發式,不是 Public Suffix List** —— 所以判不準時的**方向**
#: 才是重點:`publisher_key` 只在**有把握**時收合,判不準就保留整個
#: host。而「保留整個 host」正是這個修正之前的行為,所以這條規則
#: **不可能比舊版更會誤併**:它只在確定的情況下少算一次重複。
#: PSL 是一份一萬多行、需要定期更新的資料檔,為了一個只用來替不認得的
#: 發布者去重的鍵而背它(還要決定更新頻率與離線落後時的行為),
#: 維護成本大於它擋掉的風險。
_SECOND_LEVEL = frozenset({
    # 通用(絕大多數國碼頂級網域共用)
    "com", "co", "org", "net", "gov", "edu", "ac", "or", "ne", "gr",
    "go", "mil", "int", "info", "biz",
    # 各國自訂的分類第二層
    "idv", "game", "ebiz", "club",      # .tw
    "lg",                               # .jp
    "re", "pe",                         # .kr
    "sch", "nhs", "police",             # .uk
})

_HOSTLIKE = _re.compile(r"^(?:https?://)?([a-z0-9.-]+\.[a-z]{2,})(?:[/?#]|$)")


def publisher_key(name) -> str:
    """不認得的發布者拿來**去重**的鍵:像網址就收成註冊網域。

    2026-08-09 P2:上一版用整串小寫字串當鍵 —— 於是同一個站的
    `news.example.com`、`www.example.com`、`https://example.com/a/b`
    算成**三個**「可能獨立」的來源。那個數字是覆蓋率地板
    (`potential`),灌高之後不重要的事件會擠進必分析清單。

    **不像網址的就原樣回傳**:「Example News」與 `example.com` 是不是
    同一家,這裡答不出來 —— 而猜錯會把兩個真的獨立來源併成一個,
    那是**少算**佐證的方向,與這個數字要的保守方向相反。
    """
    n = str(name or "").strip().lower()
    m = _HOSTLIKE.match(n)
    if not m:
        return n
    host = m.group(1).strip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    tld = parts[-1]
    if len(tld) > 2:
        # **通用頂級網域(`.com`/`.tech`/`.news`…)只有一段後綴。**
        # 上一版在這裡放了一份 gTLD 白名單 —— 那是同一個毛病換個位置:
        # 沒收到的 `.tech` 會讓 `news.example.tech` 與 `www.example.tech`
        # 變成兩個鍵(外審第四輪)。**用長度分辨就夠了**:兩段式的公有
        # 後綴實際上只存在於國碼頂級網域底下。
        return ".".join(parts[-2:])
    if parts[-2] in _SECOND_LEVEL:
        # 認得的兩段式後綴(`com.tw`、`co.uk`、`idv.tw`…)
        return ".".join(parts[-3:])
    if len(parts) == 3:
        # 國碼底下直接註冊(`news.example.de`)
        return ".".join(parts[-2:])
    # **判不準 → 保留整個 host。** 三段式的公有後綴(`k12.ca.us`)
    # 用規則追不完,而收合錯的代價是把兩個發布者算成一個 ——
    # 那是**少算**獨立來源、讓事件從必分析清單掉出去的方向。
    # 保留整個 host = 這個修正之前的行為,不會比舊版更糟。
    return host


def independence(items: Optional[list]) -> dict:
    """這一群新聞代表**幾個獨立的編輯決策**。

    回 `{groups, count, unverified, aggregator_only}`:

      * `count` —— 認得出來的獨立群組數。**這是唯一可以寫進信裡的數字。**
      * `unverified` —— 不認得發布者的則數。**沒驗,不是驗過。**
      * `aggregator_only` —— 只查得到聚合器別名的則數(同上,但成因不同:
        那是抓取管線沒有解出真正的發布者,是**我們自己的**缺口)。
    """
    groups, unknown_names, agg = set(), set(), 0
    for it in (items or []):
        g = owner_of_item(it)
        if g:
            groups.add(g)
            continue
        names = [n for n in _names(it) if n.strip()]
        if names and all(is_aggregator(n) for n in names):
            agg += 1
        else:
            # 第二十三輪 P2-5:**未知來源以正規化的發布者字串去重。**
            # 同一個不認得的網站的三篇改寫稿先前算三個「可能獨立」,
            # 會灌高覆蓋率地板、擠進必分析清單。
            # **同一個站的三種寫法是一個來源**(2026-08-09 P2):
            # 整串字串當鍵的話,`news.example.com` / `www.example.com` /
            # `https://example.com/a/b` 算三個「可能獨立」。
            key = next((publisher_key(n) for n in names if n.strip()), "")
            unknown_names.add(key or f"__blank__{len(unknown_names)}")
    unverified = len(unknown_names)
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
