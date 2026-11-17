"""股票/ETF 分割的價格還原(2026-11-17 實信:00662 1 拆 5)。

00662 富邦NASDAQ 11/10 是分割前最後交易日(收 117.80),11/11–11/16 停牌,11/17 以 1 拆 5 恢復交易。
Yahoo 的 00662.TW 歷史**只改了最後兩天**(11/09 22.88、11/10 23.56),更早的仍是 113～126 —— 同一條序列
混著兩種刻度。結果:200 日均線 111.91 對上收盤 23.56 → 「跌破 -78.9%」;「較上次報告預測 -80.85%」;
而自我校正會把「實際開盤 23 vs 上週預測 116」記成 -80% 的誤差,隔天的公允價跟著歪掉。

做法:登記已知的分割(倍數、分割前最後收盤)。以「分割前收盤 ÷ √倍數」(新舊刻度的幾何中點)分辨一個價格
在哪個刻度:序列裡新舊混雜就把舊的換成新的;兩個價格要比較時換到同一個刻度。不看日期 —— 判斷靠價格本身。
生效日只作紀錄。純函式,不做網路呼叫。
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


def _threshold(split) -> float:
    _, ratio, pre_close = split
    return pre_close / math.sqrt(ratio)   # 新舊刻度的幾何中點


def _number(price) -> bool:
    return not isinstance(price, bool) and isinstance(price, (int, float)) and math.isfinite(price) and price > 0


def match(symbol, reference, price):
    """把 `price` 換到 `reference` 的刻度:兩者分屬分割前後兩種刻度時才換,同刻度原樣回傳。

    不看日期,只看價格本身在哪個刻度 —— 復牌日開盤前,Yahoo 的資料只到停牌期間(外審 PR #55);
    兩邊都是分割前的舊資料(測試、回放)本來就同刻度,也不會被誤換。"""
    if not (_number(reference) and _number(price)):
        return price
    for split in SPLITS.get(_code(symbol), ()):
        line, ratio = _threshold(split), split[1]
        if reference <= line < price:
            price = price / ratio
        elif price <= line < reference:
            price = price * ratio
    return price


def _rescale(symbol, prices: list) -> list:
    """一條序列裡同時有新、舊刻度的價格(Yahoo 只改了最後幾天)→ 舊刻度的全部換成新刻度;
    整條都是同一種刻度就原樣回傳。"""
    for split in SPLITS.get(_code(symbol), ()):
        line, ratio = _threshold(split), split[1]
        valid = [v for v in prices if _number(v)]
        if any(v <= line for v in valid) and any(v > line for v in valid):
            prices = [v / ratio if _number(v) and v > line else v for v in prices]
    return prices


def series(symbol, mapping: dict) -> dict:
    """{日期: 價格} 整份換成同一個刻度。"""
    if _code(symbol) not in SPLITS or not mapping:
        return mapping
    days = list(mapping)
    return dict(zip(days, _rescale(symbol, [mapping[d] for d in days])))


def frame(symbol, df):
    """yfinance 的 history DataFrame 換成同一個刻度(只動價格欄,不動成交量)。
    依據是 Close 欄:同一列的開高低收一起換。"""
    if _code(symbol) not in SPLITS or df is None or getattr(df, "empty", True) or "Close" not in df.columns:
        return df
    out = df.copy()
    closes = [float(v) for v in out["Close"]]
    scaled = _rescale(symbol, closes)
    factors = [s / c if _number(c) and _number(s) else 1.0 for c, s in zip(closes, scaled)]
    for column in _COLUMNS:
        if column in out.columns:
            out[column] = [v * f if v == v else v for v, f in zip(out[column], factors)]
    return out


def history(yf, symbol, **kwargs):
    """`yf.Ticker(symbol).history(**kwargs)` 加上分割還原。呼叫端傳入自己的 `yf`(測試替身照樣有效)。"""
    return frame(symbol, yf.Ticker(symbol).history(**kwargs))
