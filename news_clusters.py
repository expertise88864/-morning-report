# -*- coding: utf-8 -*-
"""**同一件事被四家媒體報導,不是四件事**(第十八輪 P1-3 與縱向重複計權)。

## 兩個一起解決的問題

**(a) 重複計權。** 分析單位先前是「一則新聞」。台積電法說會若有官方公告、
Reuters、Bloomberg、台灣媒體轉述,就會產生四個分析單位、四條因果鏈 ——
`news_analyzed` 從 4 變成 8 看起來變深了,實際只是同一條鏈改寫四次。
更糟的是模型可能把同一個底層驅動在立場裡**重複加權**。

**(b) 必分析清單。** 新聞超過上限時,截斷鍵是(來源等級, 發布時間, ID)——
**沒有重要性**。於是一批較新的 A 級低影響新聞可以擠掉真正重要的 B 級事件,
而驗證器只擋得住「一則都沒分析」。模型分析一則次要新聞就通過,
而 materiality 是它自己標的 —— **自評的重要性不能當覆蓋率的分母**。

## 這裡刻意只用**看得出來的**判準

分群靠**實體交集 + 標題詞重疊**,不靠語意相似度:相似度模型會把
「台積電法說會」與「台積電董事會」歸成一群,而那是兩件事。
必分析的判準是**官方來源**與**群集規模**(幾家媒體同時報) ——
兩者都是資料本身說得出來的,不需要任何人先判斷「這重不重要」。

模型仍然可以主張某一則不重要,但**要留下理由**(見 `analysis_validate`)——
靜默略過與判斷不重要,在信裡長得一模一樣。
"""
from __future__ import annotations

import re
from typing import Optional

#: 標題詞重疊到這個比例才算同一件事。**本模組自訂,無 repo 出處。**
#:
#: 實測(不是推理出來的):
#:     同語言、同事件            0.69 / 0.90
#:     跨語言、同事件            0.33   ← 會漏併
#:     不同事件、同主體          0.18
#: 訂 0.5 分得開前兩類。**跨語言會漏併是刻意接受的** ——
#: 漏併只是退回今天的行為(同一件事出現兩個分析單位),而誤併會讓一個
#: 真的事件被藏在另一個底下。兩種錯誤的代價不對稱,門檻就該偏向安全那側。
TITLE_OVERLAP = 0.5

#: **不同來源**達到這個數才算「多家同時報」。
#: 先前用的是文章數 —— 於是同一家媒體的三篇改寫稿會被當成「三家同時報
#: 的重大事件」。改寫稿不是獨立證據,它連二手都算不上。
CLUSTER_IS_MAJOR = 3

#: 必分析清單的上限。**不是「至少分析幾則」,是「這幾則不能不談」** ——
#: 把門檻訂高只會逼出湊數的段落。
MAX_REQUIRED = 6

#: 切詞用。中文沒有空格,用二元組近似;英文與數字照原樣。
_WORD = re.compile(r"[A-Za-z0-9]+")
_CJK = re.compile(r"[一-鿿]")

#: 太常見的詞不具鑑別力,兩則不相干的新聞也會共用。
_STOP = {"公司", "表示", "指出", "今日", "昨日", "台灣", "market", "the", "for"}


def _tokens(title: str) -> set:
    t = str(title or "")
    out = {w.lower() for w in _WORD.findall(t) if len(w) > 1}
    cjk = "".join(c if _CJK.match(c) else " " for c in t)
    for run in cjk.split():
        out |= {run[i:i + 2] for i in range(len(run) - 1)}
    return {w for w in out if w not in _STOP}


def _same_event(a: dict, b: dict) -> bool:
    """**實體要有交集,而且標題要講同一件事。** 少了任何一半都會誤併。"""
    ea, eb = set(a.get("entities") or []), set(b.get("entities") or [])
    if not (ea & eb):
        return False
    ta, tb = _tokens(a.get("title")), _tokens(b.get("title"))
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= TITLE_OVERLAP


def clusters(news: Optional[list]) -> list:
    """把新聞分成事件群。**確定性**:順序只由 `source_item_id` 決定。

    回 `[{cluster_id, member_source_ids, sources, official, size}]`。
    `cluster_id` 用群裡最小的 `source_item_id` —— 穩定、可引用,
    而且不需要另外發明一套編號(發明的編號會在明天指到別的東西)。
    """
    items = sorted([n for n in (news or []) if isinstance(n, dict)
                    and n.get("source_item_id")],
                   key=lambda n: str(n["source_item_id"]))
    groups: list = []
    for n in items:
        for g in groups:
            # 第二十輪 P1-2:**與代表比,不是與任何成員比。** single-link
            # 會被橋接串起來:A~B、B~C 而 A≁C,三則被併成一群 ——
            # 兩件不同的事被壓成一條因果鏈,其中一件還會因為「同一群
            # 只能分析一次」被驗證器強迫省略。與代表(群裡最小 ID 那則,
            # 也就是定義 cluster_id 的那則)比對:橋最多自成一群,
            # 誤併變漏併 —— 兩種錯誤的代價不對稱,要偏向安全那側。
            if _same_event(n, g[0]):
                g.append(n)
                break
        else:
            groups.append([n])
    out = []
    for g in groups:
        ids = sorted(str(m["source_item_id"]) for m in g)
        srcs = sorted({str(m.get("source") or "") for m in g} - {""})
        out.append({
            "cluster_id": f"cluster:{ids[0]}",
            "member_source_ids": ids,
            "sources": srcs,
            # **官方公告與 A 級媒體不是同一件事。** 先前 `official` 把兩者
            # 混成一格 —— 於是 Reuters 的一則報導與主管機關公告在必分析
            # 清單裡有同樣的份量,而「官方來源被漏掉」正是這類報告
            # 最實質的失誤,判準本身不能先把它糊掉。
            "official": any(m.get("official") for m in g),
            "has_grade_a": any(m.get("source_grade") == "A" for m in g),
            "size": len(g),
            "unique_sources": len(srcs),
            # **單一來源與多方證實是兩種可信度**(借自事件聚合系統的
            # corroboration 概念)。模型分析單一來源的事件時要明講
            # 「未經其他媒體證實」—— 而它得先知道哪些是。
            "corroboration": ("official" if any(m.get("official") for m in g)
                              else "multi_source" if len(srcs) >= 2
                              else "single_source"),
        })
    return sorted(out, key=lambda c: c["cluster_id"])


def required_analysis(news: Optional[list]) -> dict:
    """**分母不是模型自己給的。**

    `required_cluster_ids`:官方來源,或多家媒體同時報導的事件群。
    兩個判準都是資料本身說得出來的 —— 不需要先判斷「這重不重要」,
    而那正是先前把覆蓋率交給模型自評的問題。
    """
    cs = clusters(news)
    ranked = sorted(
        [c for c in cs
         if c["official"] or c["unique_sources"] >= CLUSTER_IS_MAJOR],
        # 官方優先,其次**不同來源數**;同分用 ID 決勝(確定性)。
        key=lambda c: (not c["official"], -c["unique_sources"], c["cluster_id"]))
    need = [c["cluster_id"] for c in ranked[:MAX_REQUIRED]]
    return {
        "clusters": cs,
        "required_cluster_ids": need,
        "coverage_basis": ("官方公告,或三個**不同來源**同時報導的事件群;"
                           "改寫稿不算獨立來源,A 級媒體也不等於官方;"
                           "不採用模型自評的重要性"),
        "dropped_from_required": max(0, len(ranked) - MAX_REQUIRED),
    }


def cluster_of(clusters_: Optional[list], source_item_id: str) -> str:
    """這則新聞屬於哪一群(找不到回空字串)。"""
    sid = str(source_item_id or "")
    for c in (clusters_ or []):
        if isinstance(c, dict) and sid in (c.get("member_source_ids") or []):
            return str(c.get("cluster_id") or "")
    return ""
