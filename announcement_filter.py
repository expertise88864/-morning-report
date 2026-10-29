"""觀測站例行公告不寫成新聞卡(使用者 2026-10-29:「常常是買什麼設備、增資等等」)。

取得設備／廠務工程／子公司增資或轉投資／授信資產處分／發行公司債／背書保證這類公告,
每天幾十則,多數金額相對公司規模很小。**金額沒到公司市值 1% 就不送進分析**;
財報、自結營收、法說、併購、重大合約、人事等照常(不符合例行樣式就不過濾)。
純函式,不做網路呼叫;判不出金額的例行公告視同小額。
"""
from __future__ import annotations

import re

#: 金額至少是市值的這個比例才算重大(使用者 2026-10-29 選定)。
MATERIAL_SHARE = 0.01
#: 查不到市值時的絕對門檻(新台幣)。
MATERIAL_TWD = 5e9
_ROUTINE = re.compile(
    r"(?:取得|購置|購買).{0,24}(?:設備|機器|廠務|工程|使用權資產|不動產|有價證券)"
    r"|處分.{0,24}(?:設備|機器|使用權資產|有價證券|債權)|增資|轉投資|授信|聯貸|公司債|背書保證|資金貸與|租賃"
    r"|資本(?:支出)?預算")
#: 股本／治理類程序公告(金額是資本額不是交易額;座談、委員、發言人等異動):一律不寫成新聞卡。
_PROCEDURAL = re.compile(r"限制員工權利新股|註銷|變更登記|競業禁止|庫藏股.{0,6}(?:轉讓|註銷)|法人座談|受邀參加"
                         r"|發言人|審計委員|薪資報酬委員|員工持股|員工認股權|股東會|真除|代理人|會計主管|稽核主管"
                         r"|更新取得|代子公司.{0,40}盈餘分配")
#: 就算長得像例行公告,有這些字就不是(「自結營收」「合併」「訂單」…)。
_SUBSTANTIVE = re.compile(r"自結|營收|財報|財務報告|法說|合併|收購|併購|股利|配息|董事長|總經理|執行長"
                          r"|重大合約|訂單|得標|停工|停產|火災|罰|訴訟|減產")
_AMOUNT = re.compile(r"(?:(新台幣|台幣|NT\$|美元|USD|US\$|人民幣|RMB|日圓|日幣)\s*)?約?\s*([\d,]+(?:\.\d+)?)\s*"
                     r"(兆|億|萬|千)?\s*(元|美元|日圓)?")
_UNIT = {"兆": 1e12, "億": 1e8, "萬": 1e4, "千": 1e3}
_RATE = {"美元": 32.0, "USD": 32.0, "US$": 32.0, "人民幣": 4.5, "RMB": 4.5, "日圓": 0.21, "日幣": 0.21}


def amount_twd(text: str) -> float:
    """公告裡最大的一筆金額(新台幣);要有幣別或「元」才算,避免把股數、日期當金額。"""
    best = 0.0
    text = str(text or "")
    for alias, canonical in (("美金", "美元"), ("新臺幣", "新台幣"), ("臺幣", "台幣")):   # PR #32:「美金」
        text = text.replace(alias, canonical)
    for cur, num, unit, suffix in _AMOUNT.findall(text):
        if not (cur or suffix):
            continue
        try:
            value = float(num.replace(",", "")) * _UNIT.get(unit, 1.0)
        except ValueError:
            continue
        best = max(best, value * _RATE.get(cur or suffix, 1.0))
    return best


def routine(item: dict, market_caps: dict) -> bool:
    """這則觀測站公告是不是「例行、小額」(不送進分析)。"""
    title = re.sub(r"\s+", "", str(item.get("title") or ""))   # 觀測站標題常斷在「使用 權資產」
    if _SUBSTANTIVE.search(title):   # 先看重大事項:「股東會決議通過合併案」不是程序公告(PR #32)
        return False
    if _PROCEDURAL.search(title):
        return True
    if not _ROUTINE.search(title):
        return False
    amount = amount_twd(title + " " + str(item.get("summary") or item.get("excerpt") or ""))
    codes = [str(item.get("code") or "")] + [str(c) for c in item.get("entities") or []]
    cap = next((market_caps[c] for c in codes if market_caps.get(c)), 0)
    return amount < (cap * MATERIAL_SHARE if cap else MATERIAL_TWD)


def market_caps(universe) -> dict:
    out = {}
    for row in universe or []:
        try:
            cap = float((row or {}).get("market_cap") or 0)
        except (TypeError, ValueError):
            continue
        if cap > 0:
            out[str(row.get("code") or "")] = cap
    return out
