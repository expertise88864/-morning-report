"""天氣卡片的重複訊息(使用者 2026-10-24:「解決今日重複訊息」)。

颱風天同時出現三種說法:氣象署警特報、本報依預報算的「風雨接近停班停課參考標準」、
人事總處的正式公告。正式公告出來之後,本報的預測只是同一件事的猜測版;而人事總處
每個縣市一行、內容一字不差,讀起來像同一句話印四次。純函式,不做網路呼叫。
"""
from __future__ import annotations

import re

OFFICIAL_PREFIX = "人事總處公告:"


def _county(name: str) -> str:
    """`彰化市`/`台中北區` → `彰化`/`台中`(與人事總處的縣市名比對用)。"""
    return str(name or "").replace("臺", "台")[:2]


def announced(suspension: list | None) -> set[str]:
    """人事總處**已作決定**的縣市(只認官方列;新聞備援的說法不算定論)。

    官方表也列「尚未宣布消息」這種待定列(PR #27 外審)—— 那裡仍要看本報的預測。
    """
    out = set()
    for row in suspension or []:
        title = str((row or {}).get("title") or "")
        county, _, status = title[len(OFFICIAL_PREFIX):].partition(" ")
        if (title.startswith(OFFICIAL_PREFIX) and "尚未" not in status
                and re.search(r"(?:停止|照常)(?:上班|上課)", status)):
            out.add(_county(county))
    return out


def without_announced(locs: list[dict] | None, suspension: list | None) -> list[dict]:
    """拿掉已有正式公告的地點 —— 那裡不再需要本報的停班停課預測。"""
    done = announced(suspension)
    return [loc for loc in (locs or []) if _county(loc.get("name")) not in done]


def merged(suspension: list | None) -> list[dict]:
    """同一種狀態的官方公告併成一行:`人事總處公告:臺中市、彰化縣 今天已達停止上班及上課標準。`"""
    order: list = []                      # 原列(非官方)或官方狀態字串,保留出現順序
    groups: dict[str, tuple[dict, list]] = {}
    for row in suspension or []:
        title = str((row or {}).get("title") or "")
        county, _, status = title[len(OFFICIAL_PREFIX):].partition(" ")
        if not title.startswith(OFFICIAL_PREFIX) or not status:
            order.append(row)
            continue
        if status not in groups:
            groups[status] = (row, [])
            order.append(status)
        groups[status][1].append(county)
    return [dict(groups[x][0], title=f"{OFFICIAL_PREFIX}{'、'.join(groups[x][1])} {x}")
            if isinstance(x, str) else x for x in order]
