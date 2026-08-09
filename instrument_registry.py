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

import re as _re

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
    # **非台股的個股要被宣告**(第二十八輪外審 P1-2)。
    # 上一版的判準是「長得像 2–6 位大寫字母」+ 有限的黑名單 ——
    # 於是 `ASEAN`、`BRICS` 這種國際組織只要出現在標題裡就能當標的。
    # 黑名單追不完開放字彙;**清單是宣告**,而宣告要在這裡。
    # 收的是這份報告真的會談到的:半導體鏈與大型科技股。
    **{t: (f"US:EQUITY:{t}", EQUITY) for t in (
        "NVDA", "AMD", "INTC", "MU", "AVGO", "QCOM", "TXN", "ADI", "MRVL",
        "AMAT", "LRCX", "KLAC", "ASML", "ARM", "SMCI", "DELL", "HPE",
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NFLX",
        "ORCL", "CRM", "NOW", "PLTR", "SNOW", "NET", "COIN", "MSTR",
        "WDC", "STX", "SNDK", "GFS", "UMC", "ASX", "HIMX", "CHT",
        # 與期間縮寫撞名、而且真的是標的的那些(見 `analysis_validate`
        # 的 `_AMBIGUOUS_ABBREV`):宣告在這裡,判準才有東西可依。
        "MTD",
    )},
    # 非美股的已上市公司:別名組裡沒有代號,要在這裡宣告
    # (外審第二輪:`SK海力士`、`三星電子` 是真的上市公司,
    #  一律拒絕會把合法的半導體分析送進修補)。
    **{t: ("KR:EQUITY:000660", EQUITY) for t in (
        "SK海力士", "SK Hynix", "SK hynix", "海力士",
    )},
    **{t: ("KR:EQUITY:005930", EQUITY) for t in (
        "三星電子", "Samsung Electronics", "三星",
    )},
    **{t: (f"US:ETF:{t}", ETF) for t in ("SMH", "SOXX", "VOO", "IVV",
                                         "DIA", "IWM", "TLT", "GLD")},
    **{t: (f"US:INDEX:{t}", INDEX) for t in ("SPX", "NDX", "DJIA", "VIX")},
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


#: 台股代號的形狀(這裡只用來認「別名組裡有沒有一個代號」)。
_TW_CODE_SHAPE = _re.compile(r"[0-9]{4,6}[A-Z]?")


#: **明確不是標的**的別名組(它們是主體:機構、政策制定者)。
#: 這個集合的存在是為了讓下面那條守衛問得出「每一組都表態了嗎」——
#: 新增一組別名時,要嘛它含一個宣告過的標的,要嘛它出現在這裡。
NON_INSTRUMENT_ALIAS_GROUPS = frozenset({"聯準會"})


def is_declared(aid) -> bool:
    """這個字是**宣告過的**標的嗎(台股代號另有 universe 驗證)。

    第二十八輪外審 P1-2:`_ASSET_LIKE` 讓任何 2–6 位大寫字母先被當成
    「長得像標的」,再靠有限的黑名單排除 —— 而黑名單追不完開放字彙:
    `ASEAN`、`BRICS` 是國際組織,`XYZAB` 誰也不是。
    **未知的大寫字串應該是「未知實體」,不是「可能是標的」。**

    宣告有兩個來源,都在這個 repo 裡寫得出來:
      * `_KNOWN`(這個表);
      * `entity_alias` 的別名組裡出現過的寫法(「輝達/NVIDIA/NVDA」)——
        那張表本來就是「同一個主體的不同寫法」的宣告。
    """
    a = str(aid or "").strip()
    if not a:
        return False
    if a in _KNOWN or a.upper() in _KNOWN:
        return True
    # **別名表是「主體」的身分表,不是標的表**(外審第二輪):
    # 它含「聯準會 / Fed / FOMC / 美聯儲」—— 那是一個機構,不是可交易標的。
    # 所以只認**那一組裡有一個成員本身就是宣告過的標的**(在 `_KNOWN`
    # 裡,或是台股代號)的組。「台積電」算(組裡有 2330)、
    # 「輝達」算(組裡有 NVDA)、「聯準會」不算。
    import entity_alias as _ea
    gi = _ea.group_of(a)
    if gi < 0:
        return False
    return any(str(m) in _KNOWN or str(m).upper() in _KNOWN
               or _TW_CODE_SHAPE.fullmatch(str(m))
               for m in _ea.ALIAS_GROUPS[gi])


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


def resolve(aid, packet=None, *, allow_unverified: bool = False):
    """`(canonical_id, scope)` —— 舊呼叫端的相容出口。

    **預設 fail-closed**(第二十八輪外審 P2-2):上一版直接丟掉 status,
    於是 `resolve("9999", {})` 在沒有 universe 的日子回
    `("TW:EQUITY:9999", "equity")` —— 任何殘留或未來的呼叫端只要走這個
    出口,上一輪修掉的 bypass 就會重新打開。
    要「沒驗也接受」請明講 `allow_unverified=True`。
    """
    cid, scope, status = resolve_status(aid, packet)
    if status != VERIFIED and not allow_unverified:
        return None, None
    return cid, scope


def needs_event_evidence(scope) -> bool:
    """這個範疇的標的要不要在證據裡出現過?**指數不用**(見模組說明)。"""
    return scope in (EQUITY, ETF)
