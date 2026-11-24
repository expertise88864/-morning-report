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


#: 標題同時在講別的颱風(比較、接棒、新生成)就留著:寧可漏擋,不可擋掉真的警報(外審 PR #65)。
_OTHER = re.compile(r"新颱|新生|雙颱|相似|類似|如同|好比|媲美|翻版|接棒|又有|另一|下一|剛走|剛過|過後|之後|遠離|低壓|低氣壓")


def _dated(ledger, today: dt.date) -> list[str]:
    """21 天前(含)就收到的颱風標題。線索的 headline 會換成後續報導,只有 timeline 的 d/t 是成對的(外審 PR #65)。"""
    heads = []
    for row in ledger if isinstance(ledger, list) else []:
        for entry in (row.get("timeline") if isinstance(row, dict) else None) or []:
            try:
                old = (today - dt.date.fromisoformat(str(entry["d"])[:10])).days >= MIN_AGE_DAYS
            except (TypeError, KeyError, ValueError):
                continue
            if old and "颱風" in str(entry.get("t") or ""):
                heads.append(str(entry["t"]))
    return heads


def old_names(ledger, today: dt.date) -> set[str]:
    """21 天前就出現過的颱風名字。引號裡也可能是「復活」「這天」,所以另要求舊標題至少三次寫成「X颱風」。"""
    heads = _dated(ledger, today)
    quoted = {a or b for h in heads for a, b in _NAME.findall(h)}
    return {n for n in quoted if sum(bool(re.search(rf"{n}[」』”]?\s*颱風", h)) for h in heads) >= MIN_HEADLINES}


def stale(title, names: set[str]) -> bool:
    """主體就是舊颱風:名字在標題開頭(前面最多一小段「快訊／」)、全句只提這一個颱風,而且現況詞就在
    那一小句裡(「薔琵颱風上月擦過台灣，海葵今逼近」的「逼近」不是在講薔琵;外審 PR #65)。"""
    text = str(title or "")
    if _AFTER.search(text) or _OTHER.search(text):
        return False
    if text.replace("颱風假", "").replace("颱風天", "").count("颱") != 1:
        return False
    for n in names:
        m = re.match(rf"(?:[^，。！!／/｜|》】]{{0,6}}[！!／/｜|》】])?\s*"
                     rf"(?:[「『“]?{n}[」』”]?\s*颱風|颱風\s*[「『“]?{n}[」』”]?)", text)
        if m and _LIVE.search(re.split(r"[，,。；;：:！!？?、／/｜|\s]", text[m.end():].lstrip(), maxsplit=1)[0]):
            return True
    return False


def drop(quotes: dict, today: dt.date):
    """回傳判斷「這一列是舊颱風現況」的函式,並從 quotes 的事件與線索拿掉(呼叫端給淺拷貝)。任何錯誤都不擋。"""
    try:
        names = old_names(quotes.get("STORY_LEDGER"), today)
    except Exception as exc:  # noqa: BLE001 - 晨報不可斷
        print(f"[typhoon-stale] 略過: {type(exc).__name__}", flush=True)
        names = set()

    def old(row) -> bool:
        return isinstance(row, dict) and any(stale(row.get(k), names) for k in ("title", "headline", "latest_title", "zh_title"))
    for key in ("STORY_LEDGER", "STRUCTURED_NEWS_EVENTS", "EVENT_TIMELINE") if names else ():
        if isinstance(quotes.get(key), list):
            quotes[key] = [r for r in quotes[key] if not old(r)]
    return old
