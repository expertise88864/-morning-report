"""Specialized analysis, fallback-letter layout (user 2026-10-19).

使用者:「使用特化分析,但盡量讓最後呈現的方式跟不是特化分析時的信件內容
格式等等相近或一樣」。這裡只放排版用的小工具:來源歸屬與「各一句」。
分析、驗證與觀察點帳本照舊;這些函式不讀寫 JSON 以外的任何狀態。
"""
from __future__ import annotations

import re

#: 逐則卡片內文的隱形標記(U+2060 word joiner):HTML 拿掉「本報解讀：」後留下它,
#: email_content_audit 才數得到每段卡片有沒有在轉 HTML 時遺失(原本數「→」)。
CARD_MARK = "\u2060"
CARD_LABEL = "本報解讀："
#: 七之五收成一小段:最多幾個觀察點(使用者 2026-10-19 選「最重要的 2~3 個」)。
OUTLOOK_WATCH_LIMIT = 3
_SENTENCE = re.compile(r"^.*?(?:[。！？!?]|$)", re.S)


def source_of(packet, source_item_id) -> str:
    """`（鉅亨）` —— 與備援信一樣在句末標發布者;查不到就不標。"""
    from analysis_render_depth import _attribution
    return _attribution({"source_item_id": str(source_item_id or "")}, packet) if source_item_id else ""


def cluster_source(packet, cluster_id: str) -> str:
    """事件群代表新聞的發布者(三大重點用)。"""
    for c in (((packet or {}).get("news_clusters") or {}).get("clusters") or []):
        if isinstance(c, dict) and str(c.get("cluster_id") or "") == str(cluster_id or ""):
            sid = c.get("representative_source_id") or next(iter(c.get("member_source_ids") or []), "")
            return source_of(packet, sid)
    return ""


def first_sentence(text: str) -> str:
    t = " ".join(str(text or "").split())
    m = _SENTENCE.match(t)
    return (m.group(0) if m else t).strip()


def with_source(text: str, tag: str) -> str:
    """`判斷（來源）。` —— 來源接在句末標點之前。"""
    t = str(text or "").strip().rstrip("。")
    return (t + tag + "。") if t else ""
