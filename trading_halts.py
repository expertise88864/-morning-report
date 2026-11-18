"""停牌與台股連假後的開盤估算(00662 停牌 2026-11-17、連假 2026-11-18 使用者同意改預測公式)。

00662 為了 1 拆 5 分割,11/11–11/16 停牌、11/17 復牌。原本:
- 停牌那幾天信裡照樣印「今日合理價」—— 當天根本不能交易;
- 復牌當天只用前一晚 QQQ **一天**的漲跌,停牌那一週美股的變化全沒算進去。
台股連假(例:春節)美股照常交易,也是同一個問題:00662 與 2330 只算最後一晚的漲跌。

改成:停牌日不估公允價(信裡寫停牌中);休市後第一個交易日從「最後交易日」起累計美股與匯率的變化。
- 停牌要在這裡登記(與 corporate_splits 同理);沒登記的停牌照舊 —— 已知限制。
- 連假靠證交所休市日表(tw_holidays);表抓不到時退回只跳週末 → 只會少算、不會重複算。
- 只有最後交易日之後真的有兩個以上美股收盤才累計;平常日子與原本完全相同。
網路呼叫只經呼叫端給的 `yf`(closes)。
"""
from __future__ import annotations

import datetime as dt

import tw_holidays

#: 代號 → ((停牌前最後交易日, 復牌日), ...)
HALTS: dict[str, tuple[tuple[str, str], ...]] = {
    "00662": (("2026-11-10", "2026-11-17"),),
}


def _day(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _periods(symbol):
    for last, resume in HALTS.get(str(symbol or "").upper().replace(".TW", ""), ()):
        yield dt.date.fromisoformat(last), dt.date.fromisoformat(resume)


def halted(symbol, day) -> bool:
    """`day` 這個台股交易日是否停牌。"""
    day = _day(day)
    return bool(day) and any(last < day < resume for last, resume in _periods(symbol))


def resumed_since(symbol, day) -> dt.date | None:
    """`day` 是復牌日 → 回傳停牌前最後交易日;否則 None。"""
    day = _day(day)
    return next((last for last, resume in _periods(symbol) if day == resume), None)


def last_trade(symbol, day) -> dt.date | None:
    """`day` 之前、這檔最後一個有交易的台股交易日(停牌登記優先,其次休市日表)。"""
    day = _day(day)
    return (resumed_since(symbol, day) or tw_holidays.previous_session(day)) if day else None


def _points(series) -> list:
    try:
        return [(_day(stamp), float(v)) for stamp, v in series.items() if float(v) > 0]
    except Exception:   # noqa: BLE001 - 取不到就由呼叫端沿用原值
        return []


def closes(yf, symbol, period="3mo"):
    """收盤序列(去時區);取不到回 None,不拋例外 —— 讓 QQQ 與匯率各自成敗(外審 PR #59)。"""
    try:
        s = yf.Ticker(symbol).history(period=period, auto_adjust=False)["Close"].dropna()
        s.index = s.index.tz_localize(None) if s.index.tz else s.index
        return s
    except Exception:   # noqa: BLE001 - 由呼叫端降級
        return None


def rebase(us_series, fx_series, last: dt.date | None, close, prev, pct, fx=None, fx_prev=None, fx_pct=0.0):
    """休市(連假、停牌)後改比較基準,回傳 (prev, pct, fx_prev, fx_pct);沒有跳空或取不到就原樣回傳。
    - 美股:最後交易日之後有兩個以上收盤 → 基準改成驅動最後成交價的那個收盤(美東日期早於最後交易日)。
    - 匯率:獨立判斷(外審 PR #59)—— 最後交易日之後有兩個以上匯率觀測,或美股已跳空 → 基準改成
      最後交易日當天(含)以前的匯率。平常日子兩者都只有一筆新資料,與原本相同。"""
    if not last:
        return prev, pct, fx_prev, fx_pct
    points = [(d, v) for d, v in _points(us_series) if d]
    base = [v for d, v in points if d < last]
    us_gap = bool(close and base and sum(d >= last for d, _ in points) >= 2)
    if us_gap:
        prev, pct = base[-1], close / base[-1] - 1
    fx_points = [(d, v) for d, v in _points(fx_series) if d]
    fx_base = [v for d, v in fx_points if d <= last]
    if fx and fx_base and (us_gap or sum(d > last for d, _ in fx_points) >= 2):
        fx_prev, fx_pct = fx_base[-1], fx / fx_base[-1] - 1
    return prev, pct, fx_prev, fx_pct


def halt_note(symbol, day) -> str:
    for last, resume in _periods(symbol):
        if last < (_day(day) or last) < resume:
            return f"停牌中({last.month}/{last.day} 後停止交易,{resume.month}/{resume.day} 恢復),本日不估合理價"
    return ""
