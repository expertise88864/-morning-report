# -*- coding: utf-8 -*-
"""**跨語言的同一件事,靠數字認**(深度優化:橫向 P2-7)。

## 問題

分群的標題重疊實測:同語言同事件 0.69/0.90,**跨語言同事件 0.33** ——
低於 0.5 門檻,所以 CNBC 的英文報導與經濟日報的中文報導**必然**各成
一群。後果是三層的:同一件事兩個分析單位(橫向重複計權)、獨立來源數
被砍半(CNBC + 經濟日報本來是兩個獨立群組,卻各自算 1 = 「僅單一來源」)、
全文預算花兩份。第二十五輪 P2-7 指名這個缺口。

## 為什麼是數字,不是語意相似度

「台積電法說會」與「台積電董事會」語意相似度很高,而它們是兩件事 ——
這個 repo 已經為此拒絕過相似度模型一次。但**金額不會騙人**:
「SK海力士砸383億美元」與 "SK Hynix to spend $38 billion" 講的是同一筆
錢,383億美元 = $38.3B,而兩家獨立媒體在同一天報出同量級金額、
主體又是同一家公司的機率,壓倒性地指向同一件事。

## 三道防線(誤併比漏併危險,每一道都往保守偏)

1. **只橋接跨語言的配對**。同語言的低重疊配對已經被 0.5 門檻判過
   「不是同一件事」——同一家公司同一天的兩則中文新聞常共用收盤價,
   拿數字橋接會把「南亞科財報」與「南亞科盤中爆量」併成一件事。
2. **只認 1,000 萬以上的金額**。收盤價(457 元)、目標價、漲跌點數
   都在門檻之下 —— 它們是**行情數字**,同一天出現在同一家公司的
   每一則新聞裡,不指認任何事件。資本支出、併購、發債、罰款在門檻上。
3. **幣別要相同,數值容差 2.5%**。容差是因為媒體會四捨五入
   ($38 billion vs 383億美元 = $38.3B,差 0.8%);幣別不同就不是
   同一筆錢(383億**台幣**與 $38.3B 差 30 倍)。

呼叫端(`news_clusters._same_event`)已先要求**實體別名組有交集** ——
數字錨點是第四道判準,不是唯一判準。
"""
from __future__ import annotations

import re

#: 金額下限(見 docstring 第 2 道防線)。
MIN_ANCHOR_VALUE = 1e7

#: 數值容差(第 3 道防線):媒體四捨五入的量級,不是「差不多就好」。
REL_TOLERANCE = 0.025

_CJK = re.compile(r"[一-鿿]")

#: 中文金額:可帶「人民幣」前綴;裸「元」在台灣財經媒體 = 新台幣。
_ZH_MONEY = re.compile(
    r"(人民幣)?([0-9][0-9,]*(?:\.[0-9]+)?)\s*(兆|億|萬)?"
    r"(美元|美金|新台幣|台幣|日圓|日元|歐元|元)")
_ZH_UNIT = {"兆": 1e12, "億": 1e8, "萬": 1e4, None: 1.0, "": 1.0}
_ZH_CURR = {"美元": "USD", "美金": "USD", "新台幣": "TWD", "台幣": "TWD",
            "日圓": "JPY", "日元": "JPY", "歐元": "EUR", "元": "TWD"}

#: 英文金額:`$38 billion`、`US$1.2bn`、`$165B`。只認美元符號 ——
#: `euro`/`yen` 拼寫在英文財經標題裡極少見,漏掉只是不橋接(保守側)。
_EN_MONEY = re.compile(
    r"(?:US|NT)?\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)\s*"
    r"(trillion|billion|million|tn|bn|mn|[TBM])?(?![a-zA-Z])")
_EN_UNIT = {"trillion": 1e12, "billion": 1e9, "million": 1e6,
            "tn": 1e12, "bn": 1e9, "mn": 1e6,
            "t": 1e12, "b": 1e9, "m": 1e6, None: 1.0, "": 1.0}


def money_anchors(text) -> set:
    """文字裡的 `(幣別, 金額)` 集合,只收 `MIN_ANCHOR_VALUE` 以上。"""
    t = str(text or "")
    out = set()
    for cny, num, unit, curr in _ZH_MONEY.findall(t):
        val = float(num.replace(",", "")) * _ZH_UNIT.get(unit or "", 1.0)
        code = "CNY" if cny else _ZH_CURR.get(curr, "")
        if code and val >= MIN_ANCHOR_VALUE:
            out.add((code, val))
    for num, unit in _EN_MONEY.findall(t):
        val = float(num.replace(",", "")) * _EN_UNIT.get((unit or "").lower(), 1.0)
        # `NT$` 由 regex 吃進前綴但不分組 —— 英文語境的 NT$ 極少,
        # 而把 NT$ 當 USD 會讓幣別防線失效;乾脆整個當 USD 之前先查。
        code = "TWD" if re.search(r"NT\$\s?" + re.escape(num), t) else "USD"
        if val >= MIN_ANCHOR_VALUE:
            out.add((code, val))
    return out


def shared_money(a_text, b_text) -> bool:
    """兩段文字有沒有**同幣別、同量級**的金額(容差見 `REL_TOLERANCE`)。"""
    aa, bb = money_anchors(a_text), money_anchors(b_text)
    for ca, va in aa:
        for cb, vb in bb:
            if ca == cb and abs(va - vb) <= REL_TOLERANCE * max(va, vb):
                return True
    return False


def _cjk_dominant(title) -> bool:
    t = str(title or "")
    return len(_CJK.findall(t)) > len(re.findall(r"[A-Za-z]", t))


#: **雙語事件類別**。同一筆金額可以是資本支出、營收、融資或罰款 ——
#: 那是四件事。外審補審 F6:先前只比金額,於是「Micron 投資 $10B」與
#: 「美光營收 100 億美元」被併成一群,`independent_sources` 變 2、
#: 佐證等級升到 multi_source —— **虛增的可信度會寫進信裡**。
#:
#: 金額只用來**產生候選配對**;合併還要求類別一致。認不出類別的一律
#: 不橋接(保守側:漏併只是退回今天的行為,誤併會造出假的佐證)。
EVENT_CATEGORIES = (
    ("capex", ("投資", "擴產", "建廠", "設廠", "新廠", "資本支出", "擴建",
               "加碼", "invest", "capex", "spend", "build", "expansion",
               "plant", "fab", "factory")),
    ("revenue", ("營收", "營業額", "銷售額", "財報", "獲利", "毛利",
                 "revenue", "sales", "earnings", "profit", "income")),
    ("financing", ("發債", "公司債", "融資", "增資", "募資", "上市",
                   "bond", "debt", "financing", "raise", "ipo", "offering")),
    ("mna", ("併購", "收購", "合併", "入股", "出售",
             "acquire", "acquisition", "merger", "buyout", "stake")),
    ("penalty", ("罰款", "裁罰", "和解金", "賠償",
                 "fine", "penalty", "settlement", "damages")),
    ("subsidy", ("補助", "補貼", "獎勵", "撥款",
                 "subsidy", "grant", "award", "funding")),
)


def _word_hit(word: str, blob: str) -> bool:
    """ASCII 詞要 token 邊界(第二輪外審 F4):`raise` 不得命中 `praise`。
    中文無詞界,維持子字串 —— 但歧義詞已由 `_AMBIGUOUS` 排除。"""
    w = word.lower()
    if not w.isascii():
        return w in blob
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", blob))


#: **歧義詞:含類別詞當子字串,語意卻是別的東西。** 「投資人」含「投資」
#: 但講的是股東;先把它們從文字挖掉再判類別(第二輪外審 F4:
#: 「美光營收100億美元,投資人關注」被判成 capex,與英文的 invest 併群)。
_AMBIGUOUS = ("投資人", "投資者", "投資機構", "投資銀行", "外資", "法人",
              "投顧", "分析師")


def event_category(title) -> str:
    """標題屬於哪一類事件(認不出來、或**同時命中多類**時回空字串)。

    多重命中回空是刻意的(第二輪外審 F4):「營收 100 億,將投資擴產」
    同時是 revenue 與 capex —— 那時「第一個命中就贏」會讓兩邊各自
    取到不同的類別,或更糟,取到同一個錯的類別而併群。
    **分不出來就不橋接**:漏併只是退回今天的行為,誤併會造出假的佐證。
    """
    blob = str(title or "").lower()
    for amb in _AMBIGUOUS:
        blob = blob.replace(amb.lower(), " ")
    hits = {code for code, words in EVENT_CATEGORIES
            if any(_word_hit(w, blob) for w in words)}
    return hits.pop() if len(hits) == 1 else ""


def _entities(item) -> list:
    ents = (item or {}).get("entities")
    return [str(x) for x in (ents or []) if str(x).strip()]


def action_anchor(a: dict, b: dict) -> bool:
    """非貨幣事件的跨語言錨:**同一個動作、同一個對象**。

    金額橋只接得起「有一筆大錢」的事件 —— 而軍售、制裁、出口管制、
    峰會、選舉、資安事件多半沒有金額,於是同一件事的中英報導永遠是
    兩群:`independent_sources` 少算,交叉驗證看起來比實際弱。

    錨用的是既有的雙語判準,不是新的相似度:
      * `event_actions.event_action()` 的關鍵詞表本來就中英並列;
      * 對象簽章用 `event_identity.object_signature`,而主體正規化
        (`canonical_subjects`)把 "United States" 與「美國」收成同一個。

    **只認算得出對象的事件**:沒有對象的動作(例如台海情勢)只說得出
    「這是同一類事」,說不出「這是同一件事」—— 而誤併會造出假的獨立
    來源數,那比漏併貴。判準交給 `object_signature` 一句話:
    它對 `NEEDS_OBJECT` 以外的動作回空字串,所以下面那個 `bool(sa)`
    就是這條規則本身。**不另外再寫一次 `act in NEEDS_OBJECT`** ——
    那個分支不可能單獨失敗,而不可能失敗的守衛只會讓人以為驗過了。
    """
    import event_actions as _ea
    import event_identity as _ei
    act = _ea.event_action(str((a or {}).get("title") or ""),
                           (a or {}).get("summary"))
    if not act:
        return False
    if act != _ea.event_action(str((b or {}).get("title") or ""),
                               (b or {}).get("summary")):
        return False
    sa = _ei.object_signature(act, _ei.canonical_subjects(_entities(a)))
    sb = _ei.object_signature(act, _ei.canonical_subjects(_entities(b)))
    return bool(sa) and sa == sb


def bridge(a: dict, b: dict) -> bool:
    """跨語言配對的第二次機會。**呼叫端已驗過實體別名組交集** ——
    這裡只補「標題重疊量不到跨語言」那一段。

    三道防線 + 第四道:同語言不橋(第 1)、金額 ≥1e7(第 2)、
    幣別相同 + 容差(第 3)、**事件類別一致**(第 4,外審補審 F6)——
    金額只產生候選,類別才決定是不是同一件事。
    """
    ta = str((a or {}).get("title") or "")
    tb = str((b or {}).get("title") or "")
    if _cjk_dominant(ta) == _cjk_dominant(tb):
        return False
    # **金額不是唯一的錨**(2026-08-09 P2):軍售、制裁、出口管制、峰會
    # 多半沒有金額,而它們正是最需要交叉驗證的那一類事件。
    if action_anchor(a, b):
        return True
    if not shared_money(ta, tb):
        return False
    ca, cb = event_category(ta), event_category(tb)
    return bool(ca) and ca == cb
