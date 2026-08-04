# -*- coding: utf-8 -*-
"""**Top5 卡片的一句話解讀**(2026-08-04 使用者回饋)。

## 為什麼需要它

Top5 每檔股票排出約 15 個數字(連續買賣、大戶週變化、量比、營收年增、
本益比、殖利率、股價淨值比、DCF、持股比例、融資餘額、空方回補……)
而**一句話都沒有**。使用者兩次反映同一件事:「很多地方還都只是在呈現數字、
堆疊數據而已,沒有詳細分析影響」。

Prompt 改再多也碰不到這裡 —— 這一塊是 Python 直接排版的,不經過 LLM。

## 它**不是**分析,是把已算好的數字翻成人話

判準全部來自卡片上已經有的欄位,規則是固定的門檻。所以:

  * **只描述,不建議。** 不出現「可以買」「值得留意」「偏多」這類詞。
  * **衝突優先講。** 「外資買但大戶減」「上漲卻量縮」這種互相矛盾的組合,
    比一致的訊號更值得寫出來 —— 一致時讀者自己看表也看得懂,
    矛盾時不講出來就會被當成一致。
  * **沒有把握就不寫。** 欄位缺就跳過那一段;全部都缺就回空字串,
    由呼叫端整行不顯示。**寧可少一句,不要生一句空話。**

這個 repo 栽過的地方是「啟發式被寫得像洞見」,所以措辭一律是事實陳述,
而卡片下方本來就有「此分數僅供觀察參考,不是買進訊號」的註腳。
"""
from __future__ import annotations

from typing import Optional

#: 量比門檻。與卡片註腳寫的一致(< 0.8 量縮、> 1.5 放量)——
#: **兩邊要用同一組數字**,否則同一張卡會自己說兩套。
VOL_QUIET, VOL_HEAVY = 0.8, 1.5

#: 大戶持股週變化的「有意義」門檻(百分點)。低於它就是雜訊,不寫。
TDCC_MOVE = 0.10


def _num(v) -> Optional[float]:
    """數值就回它自己,否則 `None`。**`bool` 不是數值。**"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _chips(foreign_streak, invest_streak, tdcc_wow) -> str:
    """籌碼往哪邊走。**買賣方向不一致時要講出來。**"""
    fs, is_, wow = (_num(foreign_streak) or 0), (_num(invest_streak) or 0), _num(tdcc_wow)
    who = []
    if abs(fs) >= 3:
        who.append(f"外資連{abs(int(fs))}天{'買' if fs > 0 else '賣'}")
    if abs(is_) >= 2:
        who.append(f"投信連{abs(int(is_))}天{'買' if is_ > 0 else '賣'}")
    if not who:
        return ""
    same_way = (fs > 0) == (is_ > 0) if (abs(fs) >= 3 and abs(is_) >= 2) else True
    line = "、".join(who)
    if not same_way:
        return f"{line},兩邊方向相反,法人內部沒有共識。"
    if wow is not None and abs(wow) >= TDCC_MOVE:
        # 大戶與法人同向 = 籌碼集中;反向 = 有人在對手邊接
        holder_up = wow > 0
        if holder_up == (fs > 0 or is_ > 0):
            return f"{line},大戶持股同步{'增加' if holder_up else '減少'},籌碼往同一個方向集中。"
        return (f"{line},但大戶持股反而{'增加' if holder_up else '減少'}"
                f"{abs(wow):.2f} 個百分點,買盤與大戶站在對邊。")
    return f"{line}。"


def _volume(vol_ratio, day_pct) -> str:
    """量價配不配。**上漲量縮**是這張卡最該講的一種不一致。"""
    vr, dp = _num(vol_ratio), _num(day_pct)
    if vr is None:
        return ""
    if vr < VOL_QUIET:
        if dp is not None and dp > 0:
            return f"成交量只有近 20 日均量的 {vr:.2f} 倍,漲勢沒有量能跟上。"
        return f"成交量只有近 20 日均量的 {vr:.2f} 倍,買賣雙方都在觀望。"
    if vr > VOL_HEAVY:
        return f"成交量放大到近 20 日均量的 {vr:.2f} 倍,追價成本已經墊高。"
    return ""


def _value(per, div_yield, rev_yoy, is_financial: bool = False) -> str:
    """便宜還是貴,配上成長性。**金融股的營收年增不可比,不寫。**"""
    p, y, r = _num(per), _num(div_yield), _num(rev_yoy)
    bits = []
    if p is not None and 0 < p < 12:
        bits.append(f"本益比 {p:.1f} 倍偏低")
    elif p is not None and p > 25:
        bits.append(f"本益比 {p:.1f} 倍偏高")
    if y is not None and y >= 5:
        bits.append(f"殖利率 {y:.1f}% 有撐")
    if not bits:
        return ""
    if r is not None and not is_financial:
        grow = "營收仍在成長" if r > 0 else "但營收年減"
        return "、".join(bits) + f",{grow}。"
    return "、".join(bits) + "。"


def readout(stock: dict, smart_money: Optional[dict] = None, *,
            is_financial: bool = False) -> str:
    """把這張卡上的數字翻成一句話。**沒東西可說就回空字串。**

    刻意只吃 `stock` 與 `smart_money` 兩個 dict(卡片本來就有的),
    不自己去撈資料 —— 這樣它是純函式,測得起來,也不會偷偷新增一次查詢。
    """
    s = stock or {}
    sm = smart_money or {}
    parts = [
        _chips(s.get("foreign_streak"), s.get("invest_streak"),
               s.get("tdcc_wow_pct") if s.get("tdcc_wow_pct") is not None
               else sm.get("tdcc_wow_pct")),
        _volume(s.get("vol_ratio_20d"), s.get("day_pct")),
        _value(s.get("per"), s.get("dividend_yield"), s.get("rev_yoy_pct"),
               is_financial=is_financial),
    ]
    return "".join(p for p in parts if p)
