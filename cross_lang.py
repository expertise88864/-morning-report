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


def bridge(a: dict, b: dict) -> bool:
    """跨語言配對的第二次機會。**呼叫端已驗過實體別名組交集** ——
    這裡只補「標題重疊量不到跨語言」那一段。同語言一律不橋接
    (第 1 道防線,理由見模組 docstring)。
    """
    ta = str((a or {}).get("title") or "")
    tb = str((b or {}).get("title") or "")
    if _cjk_dominant(ta) == _cjk_dominant(tb):
        return False
    return shared_money(ta, tb)
