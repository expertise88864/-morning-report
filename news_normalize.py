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
import source_registry as _sr
import news_coverage as _coverage
import finance_editorial as _finance

# 第二十輪 P2-3:**上一版的註解宣稱「沒有循環」,而循環是真的。**
# `evidence_packet` 底部 `from news_normalize import ...`、這裡頂層又
# `from evidence_packet import ...` —— 先 import evidence_packet 剛好成功
# (常數已定義),先 import news_normalize 就炸(它反向進入一個
# 尚未定義 `normalize_news` 的半初始化模組)。實測確認。
# **宣稱要回頭驗**;修法是延遲到呼叫時才取(那時兩個模組都已載完)。


#: 抓取層放編輯人工標註的欄位名。**`entities` 不在裡面** ——
#: 這正是 2026-08-17 查出來的根因:抓取層寫的是 `company_label`
#: (本報追蹤清單命中的代號)、`cnyes_stocks`(編輯標註的全部代號)、
#: `cnyes_keywords`(編輯標註的主題詞),而這裡先前只照抄 `entities`。
#: 於是**整條管線的實體在生產永遠是空的**,而三個依賴它的機制一起死掉:
#:   * `news_clusters._same_event` 要求實體有交集才併群 → **完全不分群**
#:     (2026-08-17 生產:402 則新聞 = 402 群);
#:   * `required_analysis` 要「官方或 ≥3 個獨立來源」的群 —— 每群只有
#:     一則,那個條件永遠選不出東西;
#:   * `analysis_recap` 存昨日觀點時「沒有實體就不存」(接不回來的觀點
#:     是死重量)→ 當天 `eligible 7 / items 0`,**縱向敘事的燃料每天都是
#:     空的**。
#: fixture 一直都給 `entities`,所以測試全綠 —— 生產的呼叫形狀與測試的
#: 呼叫形狀不同,這個 repo 記過的形狀。
#:
#: **只收編輯人工標註,不從內文猜公司名**:猜錯的實體會讓兩件不相干的
#: 事併成一群,而併錯比不併更難查。
_EDITORIAL_ENTITY_FIELDS = ("cnyes_stocks", "cnyes_keywords")


def entities_of(n: dict, clean=None) -> list:
    """這則新聞講的是誰(編輯標註 → 實體)。

    **planner 與 packet 共用同一份**(外審 2026-08-17):全文規劃器
    (`fetch_plan.plan_for_run`)在正規化**之前**就分群 —— 判準寫兩份的話,
    規劃器看到的仍然是「每則新聞自成一群」,26 篇全文額度會被同一事件的
    重複報導吃掉,而其他重大事件連全文都拿不到。
    `clean` 省略時不消毒:規劃器階段的字串不會進 prompt,而消毒器要到
    packet 那一層才存在。
    """
    clean = clean or (lambda x: x)
    out = {clean(str(e)) for e in (n.get("entities") or []) if str(e).strip()}
    lbl = str(n.get("company_label") or "").strip()
    if lbl:
        out.add(clean(lbl))
    for field in _EDITORIAL_ENTITY_FIELDS:
        for v in (n.get(field) or []):
            s = str(v).strip()
            if s:
                out.add(clean(s))
    return sorted(x for x in out if x)[:12]


#: 世界新聞來源名的前綴(抓取端「世界-<類別>」)。
WORLD_SOURCE_PREFIX = "世界-"


def world_cat_of(n) -> str:
    """這則新聞是不是「世界大事」的料、屬哪一類;不是就回空字串。

    **唯一的一份判準**(Codex 2026-09-04 P2):legacy 取材段(`morning_report`
    的 `_world_cat_of`)與特化路徑的深度守衛(`analysis_depth`)先前各寫一份 ——
    後者只認 `source` 前綴,去重後保留在一般來源上的 `world_cat`、以及
    「中央社國際」全部數不到,守衛在真有五則料的日子空轉。三條規則與 legacy
    原文逐字相同:`world_cat` 欄位、來源名「世界-<類別>」、來源正是「中央社國際」。
    """
    n = n if isinstance(n, dict) else {}
    wc = str(n.get("world_cat") or "").strip()
    if wc:
        return wc
    src = str(n.get("source") or "")
    if src.startswith(WORLD_SOURCE_PREFIX):
        return src[len(WORLD_SOURCE_PREFIX):]
    return src if src == "中央社國際" else ""


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
            "date_missing": bool(n.get("date_missing")),
            "source": clean(str(n.get("source") or "")),
            # **發布者身分要留下來**(Commit B 的整套獨立性靠它)。
            # 先前只留 `source` —— 而那一欄常常是聚合器別名
            # (`Google:2330`、`類股-金融-台股`),於是 `source_registry`
            # 查不到任何發布者,`independent_sources` 在生產**永遠是 0**,
            # 同集團與通訊社的合併一次都不會發生。
            # 單元測試全綠是因為它們直接餵 `source_name` 給 `clusters()`,
            # 而生產走的是這裡正規化過的清單。
            "source_name": clean(str(n.get("source_name") or "")),
            "source_grade": _grade(n),
            "official": bool(n.get("official")),
            # **世界大事的料要看得出來**(Codex 2026-09-04 P2):legacy 取材段靠
            # `world_cat`(抓取端依來源名「世界-<類別>」打的標)分出世界新聞,
            # 而 packet 先前把它丟了 —— 特化路徑的守衛只能看 `source` 前綴,
            # 去重後保留在一般來源上的 `world_cat` 與「中央社國際」全部數不到。
            "world_cat": clean(str(n.get("world_cat") or "")),
            "coverage_buckets": _coverage.buckets(n),
            "entities": entities_of(n, clean),
            "url": clean(str(n.get("link") or n.get("url") or "")),
        })
        _finance.retain_evidence(items[-1], n, clean=clean)
        # **新聞裡的數字要變成可引用、可核對的事實**(深度加強第二批)。
        # 沒有這一步,「80 億美元訂單」在 registry 裡是 value=None ——
        # 模型抄成 8 億,檢查器只看得到「引用了 n3」。
        # **ID 由我們發,不讓模型組**(2026-08-10 current-head 生產驗收):
        # prompt 只說「`fact:<新聞ID>.<序號>`」,而 packet 給的是一個沒有
        # 編號的清單 —— 模型得自己猜是從 0 還是從 1 起算,實測寫出
        # `fact:nfe44152db8e.1`(那則只有一筆事實,合法的是 `.0`),
        # 引用被判不存在、整份特化分析作廢。這與觀察點代號、事件群代號
        # 同一條規矩:對帳用的鍵就得是我們發的。
        # (變數不叫 `_sid` —— 那是這個函式裡 import 進來的**函式名**,
        #  遮蔽掉的話下一則新聞就炸;實測第二則當場 TypeError。)
        _facts = _nf.facts_for_item(items[-1])
        _item_id = items[-1]["source_item_id"]
        for _k, _f in enumerate(_facts):
            if isinstance(_f, dict):
                _f["evidence_id"] = f"fact:{_item_id}.{_k}"
        items[-1]["numeric_facts"] = _facts
    items.sort(key=lambda x: (_GRADE_RANK[x["source_grade"]],
                             _neg_time(x["published"]), x["source_item_id"]))
    # **同一家來源、幾乎同一個標題 = 同一篇改版重發。** 上游以 ID 去重,
    # 而改版常拿到新 ID —— 同一篇佔兩個名額、在事件群裡灌高 size。
    # 排序後保留第一則(等級高/較新的那則);**跨來源永不去重**
    # (兩家寫一樣的標題是常態,那是分群的工作)。
    seen_fp, deduped, near_dropped = {}, [], 0
    for x in items:
        # **「同一家」要用發布者判,不是用聚合器別名判**(Commit E)。
        # `source` 常是 `Google:2330` 這種查詢代號 —— 同一個查詢帶回
        # 三家不同媒體的同一則新聞時,上一版把它們判成「同一家改版重發」
        # 而砍掉兩則。註解寫著「跨來源永不去重」,而程式做的正好相反。
        # 那也讓 Commit B 的獨立來源數在生產永遠是 1。
        fp = _nf.title_fingerprint(
            _sr.owner_of_item(x) or x.get("source_name") or x["source"],
            x["title"])
        if fp[1] and fp in seen_fp:
            prior = seen_fp[fp]
            prior["coverage_buckets"] = sorted(set(_coverage.buckets(prior)) |
                                               set(_coverage.buckets(x)))
            _finance.retain_evidence(prior, prior, x)
            near_dropped += 1
            continue
        seen_fp[fp] = x
        deduped.append(x)
    items = deduped
    # 第十九輪 P1-3:**先分群、先保障,再截斷。** 先前是排序後直接留前 220,
    # 而必分析清單是在**截斷後**才算的 —— 於是排在第 221 的央行政策公告
    # 既不會進 packet,也不會成為必分析事件,而覆蓋率仍然顯示 100%。
    # 分母一開始就把真正重要的事件排除掉了。
    info = _nc.required_analysis(items)
    forced = _forced_ids(items, info)
    kept, coverage = _coverage.select(items, forced, MAX_NEWS_ITEMS)
    kept.sort(key=lambda x: (_GRADE_RANK[x["source_grade"]],
                             _neg_time(x["published"]), x["source_item_id"]))
    dropped = [x for x in items if x not in kept]
    trunc = {"news_total": len(items), "news_kept": len(kept),
             "news_dropped": len(dropped),
             "news_dropped_by_grade": _count_by_grade(dropped),
             "required_forced_in": len(forced),
             "coverage": coverage,
             "near_duplicates_dropped": near_dropped,
             "summaries_truncated": sum(1 for x in kept if x["summary_truncated"])}
    return kept, trunc, info


#: 每個必分析事件群強制保留幾則。**三則**:分群時選中的代表
#: (資訊最完整的那一則)、定義 `cluster_id` 的那則(最小 ID,
#: 否則群的身分會在截斷後改變)、以及官方那則。
#: 全部保留會讓一個 20 則的群吃掉十分之一的額度。
_FORCED_PER_CLUSTER = 3


def _forced_ids(items: list, info: dict) -> set:
    """必分析事件群**不得被截斷擠掉**的代表。"""
    by_id = {x["source_item_id"]: x for x in items}
    need = set(info.get("required_cluster_ids") or ())
    out: set = set()
    for c in (info.get("clusters") or []):
        if c.get("cluster_id") not in need:
            continue
        members = list(c.get("member_source_ids") or ())
        # **保留的要是分群時真正選中的代表**(第二十一輪 P1-7),
        # 不是最小 ID —— 後者可能是那個「短而模糊」的標題。
        # `cluster_id` 由最小 ID 決定,所以它也要留(群的身分不能變);
        # 官方那則是這個群之所以被列為必分析的原因,同樣要留。
        rep = str(c.get("representative_source_id") or "")
        candidates = ([rep] if rep in members else []) + members[:1] + [
            m for m in members if (by_id.get(m) or {}).get("official")]
        keep, seen_keep = [], set()
        for m in candidates:
            if m not in seen_keep:
                seen_keep.add(m)
                keep.append(m)
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
