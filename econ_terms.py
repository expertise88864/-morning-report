# -*- coding: utf-8 -*-
"""**信裡的專有名詞要先講中文**(2026-08-05 使用者第四次反映)。

使用者的原話:「為什麼晨報仍有部分是英文的,例如 WTI」「什麼是
AVERAGE HOURLY EARNINGS M/M、non-farm employment change、
unemployment rate 等等(也可以以中文表示,後面附上英文)」。

## 為什麼這不能交給模型

這些字串**不是模型寫的**:ForexFactory 的財經日曆、行情代碼表、
規則式事件都是 Python 直接排進 HTML 的。prompt 改再多也碰不到 ——
這與 2026-08-04 那次「Top5 卡片每檔 15 個數字而一句話都沒有」
是同一個形狀:**問題在 Python 排版的那一塊,而我一直在改 prompt。**

## 格式

一律「中文(英文原名)」。英文原名保留是刻意的:使用者要對得上
外電與看盤軟體上的名稱,而那些地方只有英文。
"""
from __future__ import annotations

import re

#: 財經數據與指標的中文名。**鍵一律小寫、去掉標點**後比對 ——
#: 來源字串的大小寫與空白不穩定(`Non-Farm` / `Non Farm` / `NON-FARM`)。
_TERMS = {
    # ── 美國就業
    "non farm employment change": "非農就業人數變動",
    "nonfarm payrolls": "非農就業人數",
    "unemployment rate": "失業率",
    "average hourly earnings m/m": "平均時薪月增率",
    "average hourly earnings y/y": "平均時薪年增率",
    "adp non farm employment change": "ADP 民間就業人數變動",
    "unemployment claims": "初次請領失業金人數",
    "jolts job openings": "職缺數",
    "participation rate": "勞動參與率",
    # ── 物價與利率
    "cpi m/m": "消費者物價指數月增率",
    "cpi y/y": "消費者物價指數年增率",
    "core cpi m/m": "核心消費者物價指數月增率",
    "ppi m/m": "生產者物價指數月增率",
    "core pce price index m/m": "核心個人消費支出物價指數月增率",
    "federal funds rate": "聯邦資金利率",
    "fomc statement": "FOMC 會後聲明",
    "fomc meeting minutes": "FOMC 會議紀要",
    "fomc press conference": "FOMC 記者會",
    # ── 景氣
    "ism manufacturing pmi": "ISM 製造業採購經理人指數",
    "ism services pmi": "ISM 服務業採購經理人指數",
    "flash manufacturing pmi": "製造業採購經理人指數初值",
    "retail sales m/m": "零售銷售月增率",
    "core retail sales m/m": "核心零售銷售月增率",
    "advance gdp q/q": "GDP 季增年率初值",
    "prelim um consumer sentiment": "密大消費者信心指數初值",
    "consumer confidence": "消費者信心指數",
    "durable goods orders m/m": "耐久財訂單月增率",
    "building permits": "營建許可",
    "crude oil inventories": "原油庫存",
    "natural gas storage": "天然氣庫存",
    "trade balance": "貿易收支",
    # ── 央行官員與特別發布(2026-08-27 使用者:「Fed Chairman Warsh
    #    Speaks 後面要有中文翻譯」「Prelim Benchmark Payrolls Revision
    #    也要有中文翻譯」)。人名保留原文 —— 譯名(華許/沃許)不統一,
    #    保留英文反而對得上外電。
    "fed chairman warsh speaks": "聯準會主席 Warsh 演說",
    "fed chair warsh speaks": "聯準會主席 Warsh 演說",
    "prelim benchmark payrolls revision": "非農就業基準修正初值",
    "prelim gdp q/q": "GDP 季增年率修正值",
    "prelim gdp price index q/q": "GDP 物價指數修正值",
    "revised gdp q/q": "GDP 季增年率終值",
    "jackson hole symposium": "傑克森霍爾央行年會",
    "treasury currency report": "財政部匯率報告",
    "core pce price index y/y": "核心個人消費支出物價指數年增率",
    "chicago pmi": "芝加哥採購經理人指數",
    "pending home sales m/m": "成屋待完成銷售月增率",
    "new home sales": "新屋銷售",
    "existing home sales": "成屋銷售",
    "cb consumer confidence": "經濟諮商會消費者信心指數",
    "richmond manufacturing index": "里奇蒙製造業指數",
    "empire state manufacturing index": "紐約州製造業指數",
    "philly fed manufacturing index": "費城聯準銀製造業指數",
    # ── 單獨出現在敘述裡的縮寫。**它們是上面那些詞的前綴** ——
    #    `cpi m/m` 必須贏過 `cpi`,而那條性質需要這幾個詞才測得出來。
    "cpi": "消費者物價指數",
    "ppi": "生產者物價指數",
    "pce": "個人消費支出物價指數",
    "pmi": "採購經理人指數",
    "gdp": "國內生產毛額",
    "fomc": "聯邦公開市場委員會",
    "ism": "美國供應管理協會",
    # ── 指標與商品(這些出現在總經表與敘述裡)
    "wti": "西德州原油",
    "brent": "布蘭特原油",
    "vix": "VIX 恐慌指數",
    "sox": "費城半導體指數",
    "dxy": "美元指數",
    "gold": "黃金",
    "kospi": "韓國綜合股價指數",
    "nikkei 225": "日經 225 指數",
    "hang seng": "恆生指數",
}

#: 只有這幾個**不加英文原名**:它們在中文語境已經是專名,
#: 後面再掛一次英文只會讓句子更長。
_NO_SUFFIX = {"黃金"}

_NORM = re.compile(r"[^a-z0-9/ ]+")


def _key(text: str) -> str:
    """**多個空白要壓成一個。** 第二十一輪 P2-7:regex 允許
    `Non  Farm`、`Non - Farm`,而字典鍵只有單一空白 —— 於是 pattern
    命中之後 `zh()` 又查不到,原樣返回。**兩邊的正規化要一致。**"""
    return " ".join(_NORM.sub(" ", str(text or "").lower()).split())


def zh(term: str, *, with_original: bool = True) -> str:
    """`"Non-Farm Employment Change"` → `"非農就業人數變動（Non-Farm
    Employment Change）"`。**認不得就原樣回傳** —— 硬翻比不翻更糟。"""
    raw = str(term or "").strip()
    hit = _TERMS.get(_key(raw))
    if not hit:
        return raw
    if not with_original or hit in _NO_SUFFIX:
        return hit
    return f"{hit}（{raw}）"


#: **罕見/難懂事件的一句話解說**(2026-08-27 使用者:「最好附上這是
#: 什麼數據/解釋什麼/什麼目的/作用之類的」)。
#:
#: 與 `_TERMS` 分開:翻譯是每個詞都要,解說只給**看名字猜不出用途**的
#: 那幾個 —— CPI 不需要解說,基準修正需要。鍵與 `_TERMS` 同一套正規化。
_EXPLAIN = {
    "prelim benchmark payrolls revision":
        "勞工統計局每年用完整稅務資料回頭校正過去 12 個月的非農就業數 ——"
        "大幅下修代表先前的就業其實比公布的弱,會直接改變市場對 Fed 的預期",
    "fed chairman warsh speaks":
        "聯準會主席公開談話 —— 措辭的鷹鴿變化會即時改變利率預期,"
        "影響力可蓋過同日數據",
    "fed chair warsh speaks":
        "聯準會主席公開談話 —— 措辭的鷹鴿變化會即時改變利率預期,"
        "影響力可蓋過同日數據",
    "jackson hole symposium":
        "各國央行首長年度聚會 —— 歷史上多次在此預告政策轉向",
    "treasury currency report":
        "美國財政部半年度匯率政策報告 —— 被列入觀察名單的經濟體"
        "(含台灣)匯率政策會受壓",
}


def explain(term: str) -> str:
    """這個事件是什麼、為什麼要看 —— 認不得回空字串(不硬編)。

    **要掃描,不是精確查表**(2026-08-28 實信):日曆的標題帶著幣別前綴
    (`[USD] Prelim Benchmark Payrolls Revision`),精確查表一律落空 ——
    翻譯出得來(`annotate` 是掃描)、解說卻整批不見。掃描用的是與
    `annotate` **同一個** `_PATTERN`(長詞優先),不另造一套會漂移的比對。
    """
    raw = str(term or "")
    hit = _EXPLAIN.get(_key(raw))
    if hit:
        return hit
    for m in _PATTERN.finditer(raw):        # 長詞優先由 _PATTERN 保證
        hit = _EXPLAIN.get(_key(m.group(0)))
        if hit:
            return hit
    return ""


#: 一次掃完所有詞。**逐詞 `re.sub` 會巢狀套疊** —— `CPI m/m` 先被
#: `cpi m/m` 換成「消費者物價指數月增率（CPI m/m）」,下一輪的 `cpi`
#: 又進去括號裡再換一次,變成
#: 「消費者物價指數月增率（消費者物價指數（CPI） m/m）」。
#: 合成一條 alternation(長詞在前)之後,每個字元只會被處理一次。
def _pattern():
    # `re.escape` 對空白的處理跨版本不同(3.7 前會加反斜線)——
    # 兩種形式都換掉,判準才不依賴直譯器版本。
    parts = [re.escape(k).replace("\\ ", " ").replace(" ", r"[\s\-]+")
             for k in sorted(_TERMS, key=len, reverse=True)]
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(parts)
                      + r")(?![A-Za-z0-9])", re.IGNORECASE)


_PATTERN = _pattern()


def annotate(text: str) -> str:
    """把一段文字裡認得出來的英文名詞就地換成「中文（英文）」。

    **一次掃描、長詞優先** —— 逐詞替換會讓短詞鑽進長詞的譯文括號裡
    再換一次(實測:`CPI m/m` 變成雙層括號)。
    """
    return _PATTERN.sub(lambda m: zh(m.group(0)), str(text or ""))
