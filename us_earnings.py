"""重點美股財報日(風險事件日曆用;2026-11-17 使用者要求查證輝達財報日期)。

`yf.Ticker(tk).calendar["Earnings Date"]` 只給日期、沒有時區,而且實測比公司公告晚一天
(NVDA 公告 11/17 美東盤後,calendar 給 11/18;AAPL、MSFT 同樣差一天)。信裡的日曆欄是台北時間,
原本把這個日期直接標成台北日期再加「盤後(美東)」—— 日期與時區都可能錯。

改用 `get_earnings_dates()`(帶美東時區的時間戳)換算成台北時間:美東 11/17 16:00 盤後 → 台北 11/18 05:00。
取不到時才退回 calendar 的日期,並標明「日期待確認」。每檔失敗只略過那一檔。純函式加呼叫端給的 `yf`。
"""
from __future__ import annotations

import datetime as dt

TICKERS = ("NVDA", "AAPL", "MSFT", "AVGO", "TSLA", "AMD", "GOOGL", "META", "MU", "QCOM")
_WEEKDAY = "一二三四五六日"


def _calendar_date(yf, ticker) -> dt.date | None:
    dates = (yf.Ticker(ticker).calendar or {}).get("Earnings Date") or []
    first = dates[0] if dates else None
    return first if isinstance(first, dt.date) else getattr(first, "date", lambda: None)()


def _exact(yf, ticker, near: dt.date):
    """公告時間(帶時區);找不到與 calendar 日期相差兩天內、尚未公布的那一筆就回 None。"""
    table = yf.Ticker(ticker).get_earnings_dates(limit=4)
    for stamp in getattr(table, "index", []):
        if getattr(stamp, "tzinfo", None) is not None and abs((stamp.date() - near).days) <= 2:
            return stamp
    return None


def events(yf, today: dt.date, end: dt.date, tz) -> list[dict]:
    out = []
    for ticker in TICKERS:
        try:
            near = _calendar_date(yf, ticker)
            if not near or not (today - dt.timedelta(days=2) <= near <= end + dt.timedelta(days=2)):
                continue
            try:
                stamp = _exact(yf, ticker, near)
            except Exception:   # noqa: BLE001 - 精確時間取不到就退回 calendar
                stamp = None
            if stamp is not None:
                local = stamp.astimezone(tz)
                if not today <= local.date() <= end:
                    continue
                us = stamp.date()
                session = "盤後" if stamp.hour >= 16 else "盤前" if stamp.hour < 9 else "盤中"
                out.append({"date": local.date(), "time": local.strftime("%H:%M"), "title": f"{ticker} 財報",
                            "note": f"美東 {us.month}/{us.day}（{_WEEKDAY[us.weekday()]}）{session}公布", "impact": "high"})
            elif today <= near <= end:
                out.append({"date": near, "time": "盤後(美東)", "title": f"{ticker} 財報",
                            "note": "日期待確認", "impact": "high"})
        except Exception:   # noqa: BLE001 - 一檔失敗只略過那一檔
            continue
    return out
