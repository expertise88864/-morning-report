"""停牌期間與復牌日的 00662 公允價(2026-11-17 使用者同意改預測公式)。

00662 為了 1 拆 5 分割,11/11–11/16 停牌、11/17 復牌。原本:
- 停牌那幾天信裡照樣印「今日合理價」—— 當天根本不能交易;
- 復牌當天只用前一晚 QQQ **一天**的漲跌,停牌那一週美股的變化全沒算進去。

改成:停牌日不估公允價(信裡寫停牌中);復牌日從「停牌前最後交易日」起累計 QQQ 與匯率的變化。
停牌要在這裡登記(與 corporate_splits 的分割登記同理);沒登記的停牌照舊 —— 已知限制。純函式,不做網路呼叫。
"""
from __future__ import annotations

import datetime as dt

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


def _last_before(series, keep) -> float | None:
    try:
        values = [float(v) for stamp, v in series.items() if keep(_day(stamp)) and float(v) > 0]
    except Exception:   # noqa: BLE001 - 取不到就由呼叫端沿用原值
        return None
    return values[-1] if values else None


def bases(qqq_series, fx_series, last_trade: dt.date, qqq_default, fx_default):
    """復牌日的比較基準:驅動停牌前最後成交價的那個美股收盤(美東日期早於最後交易日),
    與最後交易日當天(含)以前的匯率。取不到就沿用原本的前一日值。"""
    qqq = _last_before(qqq_series, lambda d: d is not None and d < last_trade)
    fx = _last_before(fx_series, lambda d: d is not None and d <= last_trade)
    return (qqq or qqq_default), (fx or fx_default)


def halt_note(symbol, day) -> str:
    for last, resume in _periods(symbol):
        if last < (_day(day) or last) < resume:
            return f"停牌中({last.month}/{last.day} 後停止交易,{resume.month}/{resume.day} 恢復),本日不估合理價"
    return ""
