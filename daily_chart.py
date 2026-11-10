"""今日一圖(使用者 2026-11-10:「很多簡報每天附一張最值得看的圖」)。

從每日歷史紀錄裡挑**今天最不尋常**的一個指標(與自己近 60 個交易日相比,偏離最多的),
畫成近 20 個交易日的長條。信件是純 HTML(沒有圖片附件),所以用表格畫:每一天一格,
高度代表數值,最後一格(今天)深色。說明文字由程式寫,只講數字與它在區間裡的位置,不下結論。
純函式,不做網路呼叫;資料不夠就回空字串。
"""
from __future__ import annotations

import math

#: (歷史欄位, 名稱, 單位, 小數位)。順序只在偏離程度相同時有意義。
SERIES = (("taifex_foreign_oi", "外資台指期淨部位", " 口", 0), ("vix", "VIX 恐慌指數", "", 2),
          ("actual_taiex_prev_close", "加權指數收盤", " 點", 0), ("usdtwd", "美元兌新台幣", " 元", 2),
          ("txo_pc_oi_ratio", "台指選擇權賣權/買權未平倉比", "", 2))
BARS, LOOKBACK, MIN_POINTS = 20, 60, 30


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _points(history: list, key: str, today: dict) -> list[tuple[str, float]]:
    rows = {str(r.get("date") or ""): _number(r.get(key)) for r in history or [] if isinstance(r, dict)}
    if today.get("date"):
        rows[str(today["date"])] = _number(today.get(key)) if key in today else rows.get(str(today["date"]))
    return [(day, value) for day, value in sorted(rows.items()) if day and value is not None][-LOOKBACK:]


def pick(history: list, today: dict | None = None) -> dict:
    """偏離最多的那個指標;今天沒有值的指標不選(圖的最後一格必須是今天)。"""
    today, best = today or {}, {}
    for key, label, unit, digits in SERIES:
        points = _points(history, key, today)
        if len(points) < MIN_POINTS or (today.get("date") and points[-1][0] != str(today["date"])):
            continue
        earlier = [v for _, v in points[:-1]]
        mean = sum(earlier) / len(earlier)
        spread = math.sqrt(sum((v - mean) ** 2 for v in earlier) / len(earlier))
        score = abs(points[-1][1] - mean) / spread if spread > 0 else 0.0
        if score > best.get("score", -1.0):
            best = {"key": key, "label": label, "unit": unit, "digits": digits, "score": score, "points": points[-BARS:]}
    return best


def _caption(chart: dict) -> str:
    values = [v for _, v in chart["points"]]
    fmt = f"{{:,.{chart['digits']}f}}"
    latest, low, high, days = values[-1], min(values), max(values), len(values)
    if latest >= high:
        where = f"近 {days} 個交易日最高"
    elif latest <= low:
        where = f"近 {days} 個交易日最低"
    else:
        where = f"高於近 {days} 個交易日中的 {sum(v < latest for v in values[:-1])} 天"
    return (f"{chart['label']} {fmt.format(latest)}{chart['unit']}，{where}"
            f"（區間 {fmt.format(low)}～{fmt.format(high)}）。")


def render(chart: dict, esc) -> str:
    """表格長條圖;`chart` 是 `pick()` 的結果,空的就回空字串。"""
    points = (chart or {}).get("points") or []
    if len(points) < 2:
        return ""
    values = [v for _, v in points]
    low, span = min(values), (max(values) - min(values)) or 1.0
    bars = "".join(   # 樣式越少越好:信件大小離 Gmail 剪信上限不遠
        f"<td style='padding:0 1px;'><div style='height:{8 + round((v - low) / span * 48)}px;"
        f"background:{'#0369a1' if i == len(values) - 1 else '#93c5fd'};'></div></td>" for i, v in enumerate(values))
    return ("<div style='margin:18px 0 6px;padding:12px 14px;border:1px solid #e2e8f0;border-radius:10px;'>"
            "<div style='font-size:14px;font-weight:700;color:#0f172a;'>今日一圖</div>"
            f"<div style='font-size:13px;color:#334155;line-height:1.7;margin:2px 0 8px;'>{esc(_caption(chart))}</div>"
            "<table role='presentation' style='width:100%;border-collapse:collapse;table-layout:fixed;height:60px;'>"
            f"<tr style='vertical-align:bottom;'>{bars}</tr></table>"
            "<div style='font-size:11px;color:#94a3b8;margin-top:4px;'>"
            f"{esc(points[0][0][5:].replace('-', '/'))} → {esc(points[-1][0][5:].replace('-', '/'))}"
            "（每格一個交易日，深色是最新；高度是區間內的相對位置，不從零起算）</div></div>")
