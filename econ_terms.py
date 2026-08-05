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
    return _NORM.sub(" ", str(text or "").lower()).strip()


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
