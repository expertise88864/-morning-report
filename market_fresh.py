"""總經列的資料新鮮度(2026-11-05 使用者:「接著查」的結論)。

存檔比對:亞股日線在 06:15 少最後一天,週二到週六的信一直顯示前兩個交易日的收盤;Yahoo 的
台幣日線也和官方對不上(11/04:Yahoo 32.733、期交所 32.821)。這裡只動總經列的顯示與 prompt,
00662 公允價與開盤預測用的匯率不經過這裡。任何失敗都回原值 —— 晨報不可斷。
"""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

#: 美股收盤後才跑,只有亞股會遇到日線少一天。
ASIA = {"N225", "KOSPI", "SSE"}
_FX_URL = "https://openapi.taifex.com.tw/v1/DailyForeignExchangeRates"
#: OpenAPI 曾停更(2026-08-22 籌碼事故),太舊就不用;連假最長約 4 天。
_FX_MAX_AGE_DAYS = 4


def latest_close(name: str, ticker, bars, close: float, prev: float) -> tuple[float, float]:
    """回應裡的最新成交比日線新,就以它為收盤、日線最後一筆為前收。查不到就用日線。"""
    try:
        meta = ticker.history_metadata or {}
        price, stamp = float(meta["regularMarketPrice"]), meta["regularMarketTime"]
        zone = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
        traded = (stamp if isinstance(stamp, dt.datetime)
                  else dt.datetime.fromtimestamp(int(stamp), dt.timezone.utc)).astimezone(zone).date()
        last_bar = bars.index[-1].date()
        print(f"[macro-fresh] {name} 日線 {last_bar}={close} 最新成交 {traded}={price}", flush=True)
        if traded > last_bar and price > 0 and close and abs(price / close - 1) <= 0.2:
            return price, close
    except Exception as exc:  # noqa: BLE001 - 退回日線
        print(f"[macro-fresh] {name} 中繼資料不可用: {type(exc).__name__}", flush=True)
    return close, prev


def with_official_fx(macro: dict, get, today: dt.date | None = None) -> dict:
    """總經列的 USD/TWD 改用期交所官方每日匯率;抓不到、太舊或數字不合理就維持 Yahoo。"""
    today = today or dt.datetime.now(ZoneInfo("Asia/Taipei")).date()
    try:
        response = get(_FX_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        rows = sorted((r for r in response.json() if r.get("USD/NTD")), key=lambda r: r["Date"])
        last, before = rows[-1], rows[-2]
        close, prev = float(last["USD/NTD"]), float(before["USD/NTD"])
        day = dt.datetime.strptime(last["Date"], "%Y%m%d").date()
        if not (math.isfinite(close) and math.isfinite(prev) and close > 0 and prev > 0):
            print(f"[macro-fresh] 期交所匯率不合理: {day} {close}/{prev}", flush=True)
            return macro
        pct = (close - prev) / prev * 100
        if (today - day).days > _FX_MAX_AGE_DAYS or day > today or abs(pct) > 5:
            print(f"[macro-fresh] 期交所匯率不採用: {day} {close}", flush=True)
            return macro
    except Exception as exc:  # noqa: BLE001 - 退回 Yahoo
        print(f"[macro-fresh] 期交所匯率不可用: {type(exc).__name__}", flush=True)
        return macro
    row = macro.get("USDTWD")
    if isinstance(row, dict) and not row.get("error"):
        row.update(close=round(close, 3), prev_close=round(prev, 3), change_pct=round(pct, 2),
                   source="TAIFEX", observed_on=day.isoformat())
    return macro


def fx_for_prompt(quotes: dict) -> tuple:
    """分析要引用的 USD/TWD:總經列採用官方時用官方,否則維持原值。預測與公允價不經過這裡。"""
    row = (quotes.get("MACRO") or {}).get("USDTWD") or {}
    if row.get("source") == "TAIFEX":
        return row.get("close"), row.get("prev_close")
    return quotes.get("USDTWD"), quotes.get("USDTWD_prev")


def margin_card_text(margin: dict | None, session: str = "") -> str:
    """廣度卡固定顯示大盤融資餘額(先前只給模型,沒提到信裡就看不到)。
    融資資料查不到會往前退:日期和卡片的上一交易日不同時要標出來(外審 PR #43)。"""
    balance = (margin or {}).get("margin_balance_100m_twd")
    if not balance:
        return ""
    change, day = margin.get("margin_change_100m_twd"), str(margin.get("date") or "").replace("/", "-")
    label = f"（{day[5:].replace('-', '/')} 資料）" if day and day != str(session or "") else ""
    return f"　｜ 融資餘額{label} <b>{balance:,.1f} 億</b>" + (f"（{change:+,.1f} 億）" if change is not None else "")
