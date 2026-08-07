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


#: `resolve_status()` 的三態(第二十五輪 P1-7)。
#:
#: 上一版只有二態,而「查不到」被寫成 `(canonical, scope)` 放行 ——
#: universe 缺席那天,`9999`、`999999` 都成了合法標的。
#: **「沒驗」與「驗過是標的」不是同一件事**,把它們塞進同一個回傳值,
#: 呼叫端就永遠分不出來。
VERIFIED = "verified"       # 查得到:確定是可交易標的
UNVERIFIED = "unverified"   # 查不到 universe:形狀像,但今天驗不了
INVALID = "invalid"         # 確定不是標的(概念詞、職稱、縮寫…)


def resolve_status(aid, packet=None) -> tuple:
    """`(canonical_id, scope, status)` —— **三態**。

    台股代號要**真的在當日 universe 裡**才是 `verified`。拿不到
    universe 時回 `unverified`(不是放行)—— 呼叫端據此決定要不要
    生成逐標的方向,而不是預設它是真的。
    """
    a = str(aid or "").strip()
    if not a:
        return None, None, INVALID
    if a in _KNOWN:
        cid, scope = _KNOWN[a]
        return cid, scope, VERIFIED
    if a.isdigit() or (len(a) >= 4 and a[:-1].isdigit()):
        codes = {str(x.get("code") or "")
                 for x in ((packet or {}).get("tw_universe") or [])
                 if isinstance(x, dict)}
        if a in codes:
            return f"TW:EQUITY:{a}", EQUITY, VERIFIED
        if codes:
            return None, None, INVALID          # universe 在,而它不在裡面
        return f"TW:EQUITY:{a}", EQUITY, UNVERIFIED
    return None, None, INVALID


def resolve(aid, packet=None):
    """`(canonical_id, scope)` —— 舊呼叫端的相容出口。

    **`unverified` 在這裡看起來與 `verified` 一樣** —— 那正是三態要解決
    的問題,所以新的判準一律走 `resolve_status()`。
    """
    cid, scope, _ = resolve_status(aid, packet)
    return cid, scope


def needs_event_evidence(scope) -> bool:
    """這個範疇的標的要不要在證據裡出現過?**指數不用**(見模組說明)。"""
    return scope in (EQUITY, ETF)
