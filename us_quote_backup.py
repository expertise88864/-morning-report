"""美股日線的備用來源:Nasdaq 官網的歷史報價(使用者 2026-11-12:「一個掛掉就看不到資料了」)。

11-12 那班 Yahoo 的 11/11 收盤是空的(yfinance 與 chart 端點是同一家,一起缺),QQQ、TSM、SPY
三檔整段停擺。這裡是**不同供應商**:只在 Yahoo 兩條路都拿不到「應有交易日」的收盤時才問。
規則與 Yahoo 補救相同:應有交易日與前一交易日兩天的收盤都要在、都要是正數,否則拒收 ——
不拿別天的價格頂替、不自己推算。端點免金鑰;被擋或格式變了就拒收,晨報照舊標示行情未更新。
"""
from __future__ import annotations

import datetime as dt

from us_quote_recovery import Rejected

URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"
ASSET_CLASS = {"QQQ": "etf", "SPY": "etf", "TSM": "stocks"}
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Accept": "application/json"}


def _price(value, what: str) -> float:
    try:
        number = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        raise Rejected(f"{what}_invalid") from None
    if not 0 < number < 1e7:
        raise Rejected(f"{what}_invalid")
    return number


def fetch(symbol: str, session: str, previous: str, http_get) -> dict:
    """回傳與 Yahoo 補救同一個形狀的報價;任何一項對不上就拋 `Rejected`。"""
    if symbol not in ASSET_CLASS:
        raise Rejected("unsupported_symbol")
    day, prev = dt.date.fromisoformat(session), dt.date.fromisoformat(previous)
    if prev >= day:
        raise Rejected("invalid_sessions")
    response = http_get(URL.format(symbol=symbol), timeout=12, retries=1, headers=_HEADERS,
                        params={"assetclass": ASSET_CLASS[symbol], "fromdate": prev.isoformat(),
                                "todate": (day + dt.timedelta(days=1)).isoformat(), "limit": 10})
    response.raise_for_status()
    data = (response.json() or {}).get("data") or {}
    if str(data.get("symbol") or "").upper() != symbol:
        raise Rejected("provenance_mismatch")
    rows = {}
    for row in ((data.get("tradesTable") or {}).get("rows") or []):
        try:
            date = dt.datetime.strptime(str(row.get("date")), "%m/%d/%Y").date().isoformat()
        except (ValueError, AttributeError):
            raise Rejected("date_invalid") from None
        if date in rows or date > session:
            raise Rejected("duplicate_or_future_bar")
        rows[date] = row
    if session not in rows or previous not in rows:
        raise Rejected("session_bar_missing" if session not in rows else "previous_bar_missing")
    close = _price(rows[session].get("close"), "session_close")
    prev_close = _price(rows[previous].get("close"), "previous_close")
    out = {"ticker": symbol, "date": session, "close": round(close, 4), "prev_close": round(prev_close, 4),
           "change_pct": round((close / prev_close - 1) * 100, 2),
           "quote_source": "nasdaq_backup", "previous_date": previous}
    for field in ("high", "low"):
        raw = rows[session].get(field)
        out[field] = _price(raw, field) if raw not in (None, "", "N/A") else None
    volume = str(rows[session].get("volume") or "").replace(",", "")
    out["volume"] = int(volume) if volume.isdigit() else None
    return out
