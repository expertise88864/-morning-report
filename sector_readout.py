# -*- coding: utf-8 -*-
"""**類股熱度表的一句話解讀**(2026-08-05 深度優化第三批)。

與 `top5_readout` 同一個理由:那張表每列排出成交值、佔比、中位數、
兩檔領先股 —— 而**衝突不講出來就會被當成一致**。2026-08-05 的實信正是
例子:半導體成交佔 40.5%、中位 +2.5%,而領先的台積電 **-2.1%**、
聯發科 **-1.1%** —— 資金湧進去的類股裡,兩檔權值都收黑,買盤在中小型
不在龍頭。表上四個數字都在,合起來的那句話沒有人說。

規矩沿用 `top5_readout`:**衝突優先講、只描述不建議、沒話說就沉默**。
判準是機械化的禁用詞掃描,不是我讀過覺得還好。
"""
from __future__ import annotations

from typing import Optional

#: 「資金集中」的門檻(單一類股佔全市場成交的 %)。**本模組自訂**:
#: 台股常態下半導體約 25–35%,超過 35% 已是明顯的單邊集中。
CONCENTRATION_PCT = 35.0

#: 中位與領先股「方向相反」才算分歧 —— 幅度差用 `signal_tensions` 的
#: 門檻已另有張力,這裡只講**表上看得到**的符號矛盾。
_MIN_LEADER_DROP = 0.5


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def readout(sector_heat: Optional[dict]) -> str:
    """整張表的一句話。**空字串 = 沒話說**,由呼叫端整行不顯示。"""
    sh = sector_heat if isinstance(sector_heat, dict) else {}
    ranked = sh.get("ranked") or []
    sectors = sh.get("sectors") or {}
    if not ranked or not isinstance(sectors, dict):
        return ""
    top_name = str(ranked[0])
    top = sectors.get(top_name) or {}
    bits: list = []

    # 1. **衝突優先**:錢最多的類股裡,中位與權值領頭方向相反。
    med = _num(top.get("median_pct"))
    leaders = [m for m in (top.get("leaders") or []) if isinstance(m, dict)]
    down_leads = [m for m in leaders
                  if (_num(m.get("pct")) or 0) <= -_MIN_LEADER_DROP]
    if med is not None and med > 0 and down_leads:
        names = "、".join(f"{m.get('code')}{m.get('name')}" for m in down_leads[:2])
        # 第二十一輪 P2-6:**輸入撐不起「買盤在中小型」這個因果結論** ——
        # 非 leader 未必是中小型,而且這一段直接進 HTML,不經 claim 稽核。
        # 只描述表上看得到的事:漲勢不是這兩檔權值帶的。
        bits.append(f"{top_name}中位上漲而權值領頭{names}收黑,"
                    "漲勢並非由這兩檔權值股主導")
    elif med is not None and med < 0 and leaders and all(
            (_num(m.get("pct")) or 0) >= _MIN_LEADER_DROP for m in leaders[:2]):
        bits.append(f"{top_name}權值領頭收紅而中位下跌,類股內部分化")

    # 2. 集中度:單一類股吃掉超過門檻的成交。
    share = _num(top.get("value_share_pct"))
    if share is not None and share >= CONCENTRATION_PCT:
        # 同上:「被抽走」是因果宣稱(其他類股的量可能同時擴張)。
        # 只說分布,不說成因。
        bits.append(f"該類股占全市場成交{share:.0f}%,成交分布高度集中")

    if not bits:
        return ""
    return ";".join(bits[:2]) + "。"


#: 禁用詞與 `top5_readout` 同一套精神 —— 由測試掃描,不靠人讀。
BANNED = ("可以買", "建議", "值得", "偏多", "偏空", "看好", "看空",
          "應該", "進場", "布局", "加碼", "減碼", "目標價", "上看")
