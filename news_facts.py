# -*- coding: utf-8 -*-
"""**新聞裡的數字要變成可引用、可核對的事實**(2026-08-05 深度加強第二批)。

## 借鏡與本地化

外部專案處理新聞的共同做法是把文章拆成**結構化要素**再往下游送:
Giveme5W1H 抽 who/what/when、事件聚合系統(Chronicle 等)用內容指紋
去重與聚類。這個 repo 不引入 ML 相依(確定性、CI 可測是硬約束),
所以取其中**純規則就做得到**的一塊:**帶單位的數字**。

## 為什麼是數字

「Broadcom 獲 80 億美元訂單」這則新聞先前在 registry 裡是一個
`value=None` 的 ID —— 模型引用它時,檢查器知道「引用了 n3」,
**不知道 80 億在哪裡**。於是:

  * 模型寫「約 80 億美元」—— `numeric_consistency` 找不到出處,誤判未命中;
  * 模型寫「約 8 億美元」—— 抄錯十倍,**同樣**只是未命中,分不出誰對;
  * 縱向鏈想錨在這個數字上 —— 沒有 ID 可引。

逐則抽出 `(值, 單位, 上下文)` 掛成 `fact:<sid>.<k>`,三個問題一起解:
數字有出處、抄錯抓得到(registry 有值)、鏈有新聞側的量化錨點。

## 刻意不做的

**不抽沒有單位的數字。** 「2026」「第 3 名」這種沒有單位的數字
噪音遠大於訊號;帶單位才是可核對的量。**不做語意配對** ——
「80 億美元訂單」與「營收 80 億美元」在這裡是同一個事實形狀,
分辨它們是模型引用時的責任(quote 欄位給它上下文)。
"""
from __future__ import annotations

import re
from typing import Optional

#: 每則新聞最多抽幾個事實。**不是效能考量,是語意的**:一則新聞塞十幾個
#: 數字時,後面的多半是背景(歷史比較、同業數字),全部掛 ID 會讓
#: 「引用了一個 fact:」失去「引用了這則新聞的重點數字」的意思。
MAX_FACTS_PER_ITEM = 6

#: 帶單位的數字。**長單位在前**(億美元要先於億、美元),否則會被短的
#: 搶先吃掉。單位表沿用 `analysis_metrics._MAGNITUDE` 的家族並擴充
#: 財經常用單位;**刻意不含「年」「季」「月」「日」** —— 那些是日期,
#: 不是量。
_FACT_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(個百分點|兆美元|億美元|億元|萬美元|萬張|萬口|萬戶|兆元|基點|奈米"
    r"|bps|%|億|兆|萬|倍|nm|美元|元|口|張|噸|檔|席)")

#: 語境裡不具鑑別力的字元(標點與空白)。去掉之後,「3 億元、3 億元」
#: 的第二個語境才會與第一個一樣(都是空的)。
_CONTEXT_NOISE = re.compile(r"[\s,、,。;;::)()(\[\]【】「」]+")

#: 上下文引文的半徑(字元)。夠看出「這個數字在講什麼」即可 ——
#: 整句會把摘要的一半塞進 registry。
_QUOTE_RADIUS = 14


def extract(text: str) -> list:
    """`[(值, 單位, 上下文引文)]`,依出現順序、去重、封頂。**純函式。**"""
    t = str(text or "")
    out, seen, prev_end = [], set(), 0
    for m in _FACT_RE.finditer(t):
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:              # pragma: no cover - regex 已保證
            continue
        unit = m.group(2)
        quote = t[max(0, m.start() - _QUOTE_RADIUS):m.end() + _QUOTE_RADIUS]
        # 第二十輪 P2-2:**去重鍵只有 (值, 單位) 會併掉不同的事實。**
        # 「營收 80 億美元」與「資本支出 80 億美元」是兩個數字,
        # 而先前只留一個 —— 第二個沒有自己的 ID,模型引用不到。
        #
        # 語境取「**上一個數字結束到這個數字開始**」之間的文字並去掉
        # 標點:真正的重複(「3 億元、3 億元」)語境是空的、仍然只算一次;
        # 不同的事實(「營收…」「資本支出…」)語境不同,各自留下。
        lead = _CONTEXT_NOISE.sub("", t[prev_end:m.start()])[-8:]
        prev_end = m.end()
        key = (value, unit, lead)
        if key in seen:
            continue
        seen.add(key)
        out.append({"value": value, "unit": unit, "quote": quote.strip()})
        if len(out) >= MAX_FACTS_PER_ITEM:
            break
    return out


def facts_for_item(item: Optional[dict]) -> list:
    """一則(已消毒的)新聞的數字事實。**標題優先** —— 標題裡的數字
    是編輯挑過的重點,摘要接在後面補。"""
    it = item if isinstance(item, dict) else {}
    # 第二十一輪 P2-2:**跨 title/summary 的去重先前退回 (值, 單位)** ——
    # 標題「營收 80 億美元」與摘要「資本支出 80 億美元」是兩個事實,
    # 而第二個會消失。改成把兩段接起來一次抽:語境鍵在同一次掃描裡
    # 生效,兩段之間用句號斷開(語境不會互相污染)。
    title = str(it.get("title") or "").strip()
    summary = str(it.get("summary") or "").strip()
    joined = title + ("。" if title and summary else "") + summary
    return extract(joined)


def title_fingerprint(source: str, title: str) -> tuple:
    """同一家來源、幾乎同一個標題 = 同一篇改版重發(內容指紋去重,
    借自 RSS 聚合器的 URL/標題/內容指紋做法)。

    上游以 `source_item_id` 去重 —— 而改版重發常常拿到**新的 ID**,
    於是同一篇文章佔兩個名額、在事件群裡灌高 `size`。
    指紋 = (來源, 標題 token 集合):同源且 token 全同才算重複,
    **跨來源永不去重**(那是分群的工作,而且兩家寫一樣的標題是常態)。
    """
    import news_clusters as _nc
    return (str(source or ""), frozenset(_nc._tokens(title)))
