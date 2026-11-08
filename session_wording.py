"""報告日不是交易日時(週六、連假),「今日開盤」要寫成下一個交易日。

11-07(週六)實信寫了「今日開盤方向偏多」「今日 2330 映射買盤」,10-31 也寫「今日開盤」:
寫稿規則早就禁止,模型仍照寫。提示詞先講清楚,程式再把固定說法改掉。
"""
from __future__ import annotations

import datetime as dt
import re

_TPE = dt.timezone(dt.timedelta(hours=8))
_WEEKDAY = "一二三四五六日"
#: 只改緊接著講「開盤、盤勢」的今日:中間只容空白、英數(代號、ADR)與「的」。
#: 「今日結論」「今日改中性」(報告本身)、「今日宣布開放盤中零股」(新聞日期)都不動(PR #45 外審)。
_TODAY_MARKET = re.compile(r"(今日|今天)(?=[\sA-Za-z0-9的]{0,8}(?:開盤|開高|開低|開出|開平|映射|盤中|早盤|盤勢))")


def _days(quotes, today: str | None) -> tuple[dt.date, dt.date] | None:
    try:
        day = dt.date.fromisoformat(today or dt.datetime.now(_TPE).strftime("%Y-%m-%d"))
        # 只認主流程算好的目標日:不自己推,提示詞才不會隨執行時鐘變動
        session = dt.date.fromisoformat(str((quotes or {}).get("TARGET_SESSION") or "")[:10])
    except (TypeError, ValueError):
        return None
    return (day, session) if session > day else None


def _label(day: dt.date, session: dt.date) -> str:
    return f"週{_WEEKDAY[session.weekday()]}" if (session - day).days < 7 else f"{session.month}/{session.day}"


def prompt_note(quotes, today: str | None = None) -> str:
    days = _days(quotes, today)
    if not days:
        return ""
    day, session = days
    when = f"{session.month}/{session.day}（週{_WEEKDAY[session.weekday()]}）"
    return (f"\n【時間定位】今天 {day.month}/{day.day}（週{_WEEKDAY[day.weekday()]}）台股休市;下一交易日是 {when}。"
            f"開盤預測、ADR 映射、反轉條件都屬於 {when},寫「{_label(day, session)}開盤」,不可寫「今日開盤」「今天」。\n")


def rewrite(text: str, quotes, today: str | None = None) -> str:
    days = _days(quotes, today)
    if not days or not isinstance(text, str):
        return text
    return _TODAY_MARKET.sub(_label(*days), text)
