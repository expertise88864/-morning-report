"""舊颱風的「現況」新聞被重新收錄時不當成今天的事(2026-11-24 實信)。

10 月的「薔琵颱風來襲新北23日停班停課」11/23 被 Google News 重新收錄,標題的「23日」剛好撞上
當天,標題日期過濾放行,信裡寫成 11/23 停班停課。判準:線索帳本裡這個颱風名字 21 天前就出現過,
而標題在講停班停課、路徑、警報這類當下動態。災後補助、農損、復建照留。純函式。
"""
from __future__ import annotations

import datetime as dt
import re

MIN_AGE_DAYS = 21
MIN_HEADLINES = 3
#: 名字只從帳本裡帶引號、緊鄰「颱風」的寫法取,「最新颱風」「強烈颱風」這類泛稱不會被當成名字。
_NAME = re.compile(r"[「『“]([一-鿿]{2,4})[」』”]\s*颱風|颱風\s*[「『“]([一-鿿]{2,4})[」』”]")
_LIVE = re.compile(r"停班|停課|颱風假|來襲|逼近|路徑|海警|警報|侵襲|侵台|登陸|風雨|復活|死灰復燃|動態|應變|嚴防|休市")
_AFTER = re.compile(r"災後|復耕|復建|重建|補助|救助|農損|災損|受損|災防法|賠償|理賠|撿拾|檢討")


def _adjacent(name: str, text: str) -> bool:
    return bool(re.search(rf"{name}[」』”]?\s*颱風|颱風\s*[「『“]?{name}", text))


def old_names(ledger, today: dt.date) -> set[str]:
    """21 天前就出現過的颱風名字。引號裡也可能是「復活」「這天」,所以另要求舊標題至少三次寫成「X颱風」。"""
    heads = []
    for row in ledger if isinstance(ledger, list) else []:
        try:
            first = dt.date.fromisoformat(str(row["first_seen"])[:10])
        except (TypeError, KeyError, ValueError):
            continue
        if (today - first).days >= MIN_AGE_DAYS and "颱風" in str(row.get("headline") or ""):
            heads.append(str(row["headline"]))
    quoted = {a or b for h in heads for a, b in _NAME.findall(h)}
    return {n for n in quoted if sum(bool(re.search(rf"{n}[」』”]?\s*颱風", h)) for h in heads) >= MIN_HEADLINES}


def stale(title, names: set[str]) -> bool:
    text = str(title or "")
    return bool(_LIVE.search(text) and not _AFTER.search(text) and any(_adjacent(n, text) for n in names))


def drop(quotes: dict, today: dt.date):
    """回傳判斷「這一列是舊颱風現況」的函式,並從 quotes 的事件與線索拿掉(呼叫端給淺拷貝)。任何錯誤都不擋。"""
    try:
        names = old_names(quotes.get("STORY_LEDGER"), today)
    except Exception as exc:  # noqa: BLE001 - 晨報不可斷
        print(f"[typhoon-stale] 略過: {type(exc).__name__}", flush=True)
        names = set()

    def old(row) -> bool:
        return isinstance(row, dict) and any(stale(row.get(k), names) for k in ("title", "headline"))
    for key in ("STORY_LEDGER", "STRUCTURED_NEWS_EVENTS", "EVENT_TIMELINE") if names else ():
        if isinstance(quotes.get(key), list):
            quotes[key] = [r for r in quotes[key] if not old(r)]
    return old
