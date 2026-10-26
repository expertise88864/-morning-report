"""台股休市日(證交所 OpenAPI「市場開休市日期」;使用者 2026-10-26 同意接入)。

10/26 光復節補假休市,晨報仍寫「10/26 開盤預估」:目標交易日原本只跳週末,
國定假日要等事後對齊實際開盤才知道。颱風這類臨時休市仍無法事先得知。
抓取走呼叫端注入的 `get`(morning_report._http_get);失敗就退回只跳週末 —— 晨報不可斷。
"""
from __future__ import annotations

import datetime as dt
import re
import sys

URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
#: 表裡也列有開盤的日子(「國曆新年開始交易日」「農曆春節前最後交易日」),那些不是休市。
_TRADING = ("開始交易", "最後交易")


def closed_dates(rows) -> frozenset[str]:
    """`[{"Name","Date":"1151026",...}]` → `{"2026-10-26", ...}`;非 list 或壞列一律略過。"""
    out = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or any(k in str(row.get("Name") or "") for k in _TRADING):
            continue
        raw = str(row.get("Date") or "")
        if re.fullmatch(r"\d{7}", raw):
            try:
                out.add(dt.date(int(raw[:3]) + 1911, int(raw[3:5]), int(raw[5:])).isoformat())
            except ValueError:
                continue
    return frozenset(out)


def target_session(date_str: str, get) -> str:
    """下一個開盤日:跳過週末與證交所公告的休市日。"""
    from session_calendar import _infer_target_session_date
    try:
        closed = closed_dates(get(URL, timeout=15).json())
    except Exception as exc:            # noqa: BLE001 - 退回只跳週末,不中斷晨報
        print(f"::warning::[holidays] 證交所休市日抓取失敗,退回只跳週末: {type(exc).__name__}",
              file=sys.stderr)
        closed = frozenset()
    return _infer_target_session_date(date_str, closed)
