# -*- coding: utf-8 -*-
"""**進 packet 的是哪些新聞、為什麼是這些**(第十九輪 P1-3 拆出)。

`evidence_packet` 負責「packet 長什麼樣」;這裡負責一個獨立的決定:
**當日新聞超過額度時,誰留下來。**

拆開的理由是第十九輪 P1-3 那個缺陷的形狀:先前是「排序 → 留前 220 →
對留下來的算必分析清單」。於是排在第 221 的央行政策公告既不會進 packet,
也不會成為必分析事件 —— 而覆蓋率仍然顯示 100%。**分母一開始就把真正
重要的事件排除掉了**,而那個順序錯誤在 `build()` 裡看不出來。

現在是:分群(完整池)→ 必分析 → 強制保留代表 → 其餘依序補滿。
"""
from __future__ import annotations

from typing import Optional

import news_clusters as _nc
import news_facts as _nf

# 第二十輪 P2-3:**上一版的註解宣稱「沒有循環」,而循環是真的。**
# `evidence_packet` 底部 `from news_normalize import ...`、這裡頂層又
# `from evidence_packet import ...` —— 先 import evidence_packet 剛好成功
# (常數已定義),先 import news_normalize 就炸(它反向進入一個
# 尚未定義 `normalize_news` 的半初始化模組)。實測確認。
# **宣稱要回頭驗**;修法是延遲到呼叫時才取(那時兩個模組都已載完)。


def normalize_news(news: Optional[list], sanitize=None) -> tuple:
    """(正規化後的新聞, 截斷摘要)。確定性排序、確定性截斷。

    排序鍵刻意是 (等級, 發布時間倒序, source_item_id) —— 最後一項是
    **決勝子句**:少了它,兩則同等級同時間的新聞順序會依賴 dict 的插入順序,
    而那會讓 evidence_sha 在無關的上游變動下抖動。
    """
    from evidence_packet import (
        MAX_FULLTEXT_CHARS, MAX_NEWS_ITEMS, MAX_SUMMARY_CHARS,
        _GRADE_RANK, _grade, _identity, _sid)
    items, seen = [], set()
    for i, n in enumerate(news or []):
        if not isinstance(n, dict):
            continue
        sid = _sid(n, i)
        if sid in seen:          # 上游已去重,這裡只是防守
            continue
        seen.add(sid)
        # **每一個外部字串都要過消毒器。** 標題、摘要、全文、來源名、
        # 實體、URL 全部是抓來的,任何一個都可能帶注入內容。
        clean = sanitize or _identity
        summary = clean(str(n.get("summary") or ""))
        fulltext = clean(str(n.get("fulltext") or ""))
        items.append({
            "source_item_id": sid,
            "title": clean(str(n.get("title") or ""))[:300],
            "summary": summary[:MAX_SUMMARY_CHARS],
            "summary_truncated": len(summary) > MAX_SUMMARY_CHARS,
            "fulltext": fulltext[:MAX_FULLTEXT_CHARS],
            "fulltext_truncated": len(fulltext) > MAX_FULLTEXT_CHARS,
            "published": str(n.get("published") or ""),
            "source": clean(str(n.get("source") or "")),
            "source_grade": _grade(n),
            "official": bool(n.get("official")),
            "entities": sorted({clean(str(e)) for e in (n.get("entities") or [])})[:12],
            "url": clean(str(n.get("link") or n.get("url") or "")),
        })
        # **新聞裡的數字要變成可引用、可核對的事實**(深度加強第二批)。
        # 沒有這一步,「80 億美元訂單」在 registry 裡是 value=None ——
        # 模型抄成 8 億,檢查器只看得到「引用了 n3」。
        items[-1]["numeric_facts"] = _nf.facts_for_item(items[-1])
    items.sort(key=lambda x: (_GRADE_RANK[x["source_grade"]],
                             _neg_time(x["published"]), x["source_item_id"]))
    # **同一家來源、幾乎同一個標題 = 同一篇改版重發。** 上游以 ID 去重,
    # 而改版常拿到新 ID —— 同一篇佔兩個名額、在事件群裡灌高 size。
    # 排序後保留第一則(等級高/較新的那則);**跨來源永不去重**
    # (兩家寫一樣的標題是常態,那是分群的工作)。
    seen_fp, deduped, near_dropped = set(), [], 0
    for x in items:
        fp = _nf.title_fingerprint(x["source"], x["title"])
        if fp[1] and fp in seen_fp:
            near_dropped += 1
            continue
        seen_fp.add(fp)
        deduped.append(x)
    items = deduped
    # 第十九輪 P1-3:**先分群、先保障,再截斷。** 先前是排序後直接留前 220,
    # 而必分析清單是在**截斷後**才算的 —— 於是排在第 221 的央行政策公告
    # 既不會進 packet,也不會成為必分析事件,而覆蓋率仍然顯示 100%。
    # 分母一開始就把真正重要的事件排除掉了。
    info = _nc.required_analysis(items)
    forced = _forced_ids(items, info)
    kept = [x for x in items if x["source_item_id"] in forced]
    for x in items:
        if len(kept) >= MAX_NEWS_ITEMS:
            break
        if x["source_item_id"] not in forced:
            kept.append(x)
    kept.sort(key=lambda x: (_GRADE_RANK[x["source_grade"]],
                             _neg_time(x["published"]), x["source_item_id"]))
    dropped = [x for x in items if x not in kept]
    trunc = {"news_total": len(items), "news_kept": len(kept),
             "news_dropped": len(dropped),
             "news_dropped_by_grade": _count_by_grade(dropped),
             "required_forced_in": len(forced),
             "near_duplicates_dropped": near_dropped,
             "summaries_truncated": sum(1 for x in kept if x["summary_truncated"])}
    return kept, trunc, info


#: 每個必分析事件群強制保留幾則。**兩則**:定義 `cluster_id` 的那則
#: (最小 ID,否則群的身分會在截斷後改變)以及官方那則(如果不同)。
#: 全部保留會讓一個 20 則的群吃掉十分之一的額度。
_FORCED_PER_CLUSTER = 2


def _forced_ids(items: list, info: dict) -> set:
    """必分析事件群**不得被截斷擠掉**的代表。"""
    by_id = {x["source_item_id"]: x for x in items}
    need = set(info.get("required_cluster_ids") or ())
    out: set = set()
    for c in (info.get("clusters") or []):
        if c.get("cluster_id") not in need:
            continue
        members = list(c.get("member_source_ids") or ())
        # 最小 ID 決定 `cluster_id`,一定要留;官方那則是這個群之所以
        # 被列為必分析的原因,也要留。
        keep = members[:1] + [m for m in members
                              if (by_id.get(m) or {}).get("official")]
        out.update(keep[:_FORCED_PER_CLUSTER])
    return out


def _neg_time(published: str) -> str:
    """讓「新的排前面」可以用單一 sort key 表示(字串反轉不可行,改用補數位)。"""
    # ISO 時間字串:用固定長度的補數,確保新的排前面且完全確定性。
    t = (published or "")[:32].ljust(32, "0")
    return "".join(chr(0x10FFFD - ord(c)) if ord(c) < 0x10FFFD else c for c in t)


def _count_by_grade(items: list) -> dict:
    out: dict = {}
    for x in items:
        out[x["source_grade"]] = out.get(x["source_grade"], 0) + 1
    return dict(sorted(out.items()))
