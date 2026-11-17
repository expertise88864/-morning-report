"""股票/ETF 分割的價格還原(2026-11-17 實信:00662 1 拆 5)。

00662 富邦NASDAQ 11/10 是分割前最後交易日(收 117.80),11/11–11/16 停牌,11/17 以 1 拆 5 恢復交易。
Yahoo 的 00662.TW 歷史**只改了最後兩天**(11/09 22.88、11/10 23.56),更早的仍是 113～126 —— 同一條序列
混著兩種刻度。結果:200 日均線 111.91 對上收盤 23.56 → 「跌破 -78.9%」;「較上次報告預測 -80.85%」;
而自我校正會把「實際開盤 23 vs 上週預測 116」記成 -80% 的誤差,隔天的公允價跟著歪掉。

做法:登記已知的分割(生效日、倍數、分割前最後收盤)。生效日之前的價格若還在舊刻度(高於「分割前收盤 ÷ √倍數」,
新舊刻度的幾何中點),就除以倍數;已經是新刻度的(Yahoo 改過的那兩天)不動。純函式,不做網路呼叫。
新的分割要在這裡登記;沒登記的分割仍會出錯 —— 這是已知限制。
"""
from __future__ import annotations

import math

#: 代號 → ((新刻度第一個交易日, 倍數, 分割前最後收盤), ...)
SPLITS: dict[str, tuple[tuple[str, float, float], ...]] = {
    "00662": (("2026-11-17", 5.0, 117.80),),
}
_COLUMNS = ("Open", "High", "Low", "Close", "Adj Close")


def _code(symbol) -> str:
    return str(symbol or "").upper().replace(".TWO", "").replace(".TW", "")


def value(symbol, day, price, as_of):
    """`day` 的價格換到 `as_of` 那天的刻度。只有跨過分割日(day < 生效日 <= as_of)才調整 ——
    兩邊都在分割前(測試資料、回放舊日期)本來就是同一個刻度。不是數字、沒登記、已是新刻度就原樣回傳。"""
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price):
        return price
    day, as_of = str(day or "")[:10], str(as_of or "")[:10]
    for effective, ratio, pre_close in SPLITS.get(_code(symbol), ()):
        if day and day < effective <= as_of and price > pre_close / math.sqrt(ratio):
            price = price / ratio
    return price


def series(symbol, mapping: dict) -> dict:
    """{日期: 價格} 整份還原。"""
    if _code(symbol) not in SPLITS:
        return mapping
    last = max((str(day)[:10] for day in mapping or {}), default="")
    return {day: value(symbol, day, price, last) for day, price in (mapping or {}).items()}


def frame(symbol, df):
    """yfinance 的 history DataFrame 還原(只動價格欄,不動成交量)。"""
    if _code(symbol) not in SPLITS or df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    days = [(idx.tz_localize(None) if getattr(idx, "tz", None) else idx).strftime("%Y-%m-%d") for idx in out.index]
    last = max(days, default="")   # 序列本身跨過分割日才會混兩種刻度
    for column in _COLUMNS:
        if column in out.columns:
            out[column] = [value(symbol, day, float(v), last) if v == v else v for day, v in zip(days, out[column])]
    return out


def history(yf, symbol, **kwargs):
    """`yf.Ticker(symbol).history(**kwargs)` 加上分割還原。呼叫端傳入自己的 `yf`(測試替身照樣有效)。"""
    return frame(symbol, yf.Ticker(symbol).history(**kwargs))
