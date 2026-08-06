# -*- coding: utf-8 -*-
"""**這是不是一個標的、以及它是哪一種標的**(第二十四輪 P1-12)。

先前沒有「標的」這個型別 —— 判斷散在四處各憑字串形狀猜:

    _ASSET_LIKE     任何 4–6 位數字或 2–6 個大寫字母
    _KNOWN_ASSETS   一張手寫白名單
    tw_universe     當日台股代號
    _CONCEPT_TERMS  一張手寫的「這其實是概念不是標的」黑名單

於是 `AI`/`GPU`/`CHIP` 冒充過標的(靠黑名單一個一個補),而白名單裡的
`QQQ`/`SPY`/`TSM` **直接繞過事件相關性檢查** —— 一則與它們無關的新聞
可以宣稱「受影響標的:QQQ」而完全沒有人擋。

## 兩個問題要分開問

  1. **這是不是一個標的?** —— 型別問題,查表(canonical ID)。
  2. **它跟這則事件有沒有因果關係?** —— 證據問題,看它在不在證據裡。

先前把兩者混成一個白名單:「是已知標的」被當成「與這件事有關」。
那正是白名單繞過檢查的原因。

## 為什麼指數可以豁免第 2 問

`TAIEX` / 櫃買 / market-wide 是**整個市場**。總經事件(CPI、Fed、關稅)
影響的就是它,而新聞標題不會寫「加權指數」—— 要求它出現在證據裡等於
禁止談總經傳導。個股與 ETF 沒有這個性質:它們要被點名才算被影響。
"""
from __future__ import annotations

#: 標的的三種範疇。**豁免事件相關性檢查的只有 `index`**,理由見模組說明。
EQUITY, ETF, INDEX = "equity", "etf", "index"

#: 非台股個股的已知標的 → (canonical_id, scope)。
#: canonical id 的形狀是 `<市場>:<類別>:<代號>` —— 「2330」與「TSM」是
#: 兩個不同市場的兩個標的,先前它們在同一個扁平命名空間裡。
_KNOWN = {
    "TAIEX": ("TW:INDEX:TAIEX", INDEX),
    "加權指數": ("TW:INDEX:TAIEX", INDEX),
    "OTC": ("TW:INDEX:OTC", INDEX),
    "櫃買指數": ("TW:INDEX:OTC", INDEX),
    "market-wide": ("GLOBAL:INDEX:MARKET", INDEX),
    "SOX": ("US:INDEX:SOX", INDEX),
    "費半": ("US:INDEX:SOX", INDEX),
    "QQQ": ("US:ETF:QQQ", ETF),
    "SPY": ("US:ETF:SPY", ETF),
    "TSM": ("US:EQUITY:TSM", EQUITY),
}


def resolve(aid, packet=None):
    """`(canonical_id, scope)`;不是標的回 `(None, None)`。

    台股代號要**真的在當日 universe 裡**才算標的 —— 先前任何 4–6 位數
    都放行,於是 `999999` 冒充過逐標的分析。拿不到 universe 時不判定
    (降級不誤擋),但那是**沒驗**不是驗過。
    """
    a = str(aid or "").strip()
    if not a:
        return None, None
    if a in _KNOWN:
        return _KNOWN[a]
    if a.isdigit() or (len(a) >= 4 and a[:-1].isdigit()):
        codes = {str(x.get("code") or "")
                 for x in ((packet or {}).get("tw_universe") or [])
                 if isinstance(x, dict)}
        if a in codes:
            return f"TW:EQUITY:{a}", EQUITY
        return (None, None) if codes else (f"TW:EQUITY:{a}", EQUITY)
    return None, None


def needs_event_evidence(scope) -> bool:
    """這個範疇的標的要不要在證據裡出現過?**指數不用**(見模組說明)。"""
    return scope in (EQUITY, ETF)
