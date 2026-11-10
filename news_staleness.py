"""標題寫的資料月份太舊的新聞不當成新消息(使用者 2026-11-10)。

11-10 實信把「日本 8 月電動車銷量創新高」當成當天的事。統計數字通常隔月公布:
11 月看到 10 月、9 月的數據是正常的,8 月以前就是舊聞被重新轉載。只看標題、只認
「N 月 + 統計名詞」;日期(11 月 17 日)、累計(前 8 月)、未來月份都不算。純函式。
"""
from __future__ import annotations

import datetime as dt
import re

#: 與現在相差幾個月以上算舊(11 月時:8 月以前)
MIN_GAP = 3
_DATA_MONTH = re.compile(
    r"(?<![\d前至到~～\-])(?<![前至到]\s)(\d{1,2})\s*月(?:份)?(?!\s*\d{1,2}\s*[日號])(?!底|初|中旬|起|前|後|以來)"
    r"[^，。、；：\s]{0,6}?(?:營收|銷量|銷售|出口|進口|外銷訂單|訂單|CPI|PPI|PMI|數據|財報|獲利|出貨|產值|失業率|零售|非農|通膨)")


def stale(title, today: dt.date) -> bool:
    for month in _DATA_MONTH.findall(str(title or "")):
        gap = (today.month - int(month)) % 12
        if 1 <= int(month) <= 12 and MIN_GAP <= gap <= 8:   # 9～11 個月前多半是在講明年
            return True
    return False


def fresh(news: list, today) -> list:
    """`today` 可以是日期或 ISO 字串;看不懂就原樣回傳(不猜今天是幾月)。"""
    try:
        day = today if isinstance(today, dt.date) else dt.date.fromisoformat(str(today or "")[:10])
    except ValueError:
        return list(news or [])
    kept = [n for n in news or [] if not stale((n or {}).get("title"), day)]
    if len(kept) != len(news or []):
        print(f"[news-staleness] 資料月份過舊的新聞略過 {len(news or []) - len(kept)} 則", flush=True)
    return kept
