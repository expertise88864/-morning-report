"""報告日不是交易日時(週六、連假),「今日開盤」要寫成下一個交易日;
休市後第一個交易日(週一、連假後),「昨日台股」「昨夜美股」要寫成上週五(11-09 實信)。

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
_TODAY_MARKET = re.compile(r"(今日|今天)(?=(?:[\sA-Za-z0-9的]|台股|加權|大盤|指數|台積電|台指期){0,8}(?:開盤|開高|開低|開出|開平|映射|盤中|早盤|盤勢))")


def _days(quotes, today: str | None) -> tuple[dt.date, dt.date] | None:
    try:
        day = dt.date.fromisoformat(today or dt.datetime.now(_TPE).strftime("%Y-%m-%d"))
        # 只認主流程算好的目標日:不自己推,提示詞才不會隨執行時鐘變動
        session = dt.date.fromisoformat(str((quotes or {}).get("TARGET_SESSION") or "")[:10])
    except (TypeError, ValueError):
        return None
    return (day, session) if session > day else None


#: 休市後講「昨日」的台股數字;美股另算(台美休市日不同,PR #48 外審)。「昨夜三大重點」段名與週末新聞的昨夜不動
#: 「收盤」「成交」台美都說得通,不改(PR #48 外審第二輪:「美股昨日收盤」)
_TW_YESTERDAY = re.compile(r"(?<!美股)昨日(?=台股|加權|大盤|法人|外資|投信|融資)")
_US_YESTERDAY = re.compile(r"昨日(?=美股)|(?:(?<=半導體端)|(?<=半導體)|(?<=美股)|(?<=費半))昨(?:夜|日)|"
                           r"昨夜(?=美股|費半|SOX|TSM|ADR|美國科技股|半導體)")


def _since(quotes, key: str, today: str | None) -> tuple[str, str]:
    """上一次收盤離目標交易日超過一天 → (稱呼, M/D),例「上週五」「週二」「2/11」;其餘回空。"""
    quotes = quotes or {}
    raw = quotes.get(key)
    raw = raw.get("date") if isinstance(raw, dict) else raw   # 美股看 QQQ 報價本身的日期
    try:
        session = dt.date.fromisoformat(str(quotes.get("TARGET_SESSION") or "")[:10])
        last = dt.date.fromisoformat(str(raw or "")[:10])
    except (TypeError, ValueError):
        return "", ""
    if _days(quotes, today) or not 1 < (session - last).days:
        return "", ""
    when = f"{last.month}/{last.day}"
    if (session - last).days >= 7:
        return when, when
    return ("上" if last.weekday() >= session.weekday() else "") + f"週{_WEEKDAY[last.weekday()]}", when


def _label(day: dt.date, session: dt.date) -> str:
    return f"週{_WEEKDAY[session.weekday()]}" if (session - day).days < 7 else f"{session.month}/{session.day}"


def prompt_note(quotes, today: str | None = None) -> str:
    days = _days(quotes, today)
    (tw, tw_day), (us, us_day) = _since(quotes, "LAST_TRADING_SESSION", today), _since(quotes, "QQQ", today)
    if tw or us:
        parts = ([f"台股上一交易日是{tw}({tw_day}),寫「{tw}台股」,不可寫「昨日台股」"] if tw else []) + (
            [f"美股最近一次收盤是{us}({us_day}),寫「{us}美股」,不可寫「昨夜美股」「昨夜」"] if us else [])
        return "\n【時間定位】" + ";".join(parts) + "(週末發生的新聞照實際日期寫)。\n"
    if not days:
        return ""
    day, session = days
    when = f"{session.month}/{session.day}（週{_WEEKDAY[session.weekday()]}）"
    return (f"\n【時間定位】今天 {day.month}/{day.day}（週{_WEEKDAY[day.weekday()]}）台股休市;下一交易日是 {when}。"
            f"開盤預測、ADR 映射、反轉條件都屬於 {when},寫「{_label(day, session)}開盤」,不可寫「今日開盤」「今天」。\n")


def rewrite(text: str, quotes, today: str | None = None) -> str:
    days = _days(quotes, today)
    if not isinstance(text, str):
        return text
    if days:
        return _TODAY_MARKET.sub(_label(*days), text)
    tw, us = _since(quotes, "LAST_TRADING_SESSION", today)[0], _since(quotes, "QQQ", today)[0]
    text = _TW_YESTERDAY.sub(tw, text) if tw else text
    return _US_YESTERDAY.sub(us, text) if us else text


def yesterday(quotes, today: str | None = None) -> str:
    """程式自己寫的台股句子用:「昨日」或上一交易日的稱呼。"""
    return _since(quotes, "LAST_TRADING_SESSION", today)[0] or "昨日"
