# -*- coding: utf-8 -*-
"""**全文預算要花在事件上,不是花在文章上**(重構規格 Commit B:兩階段抓取)。

## 問題

上一版的全文抓取是「掃 critical、抓十篇;掃 high、抓十六篇」——
判準是**每則新聞自己的重要性**,而同一個事件會有四家媒體各報一則。
於是十篇 critical 全文可能是**兩個事件**:台積電法說四篇、Fed 決議三篇、
其餘三篇是那兩件事的追蹤稿。真正只有一家報的第三個事件一篇全文都沒有,
而它在信裡就只有 RSS 的兩行摘要。

**這是重複計權在抓取層的版本** —— 分群已經在分析層解掉一次了。

## 做法(兩階段)

    第一階段:RSS 帶回標題/摘要/來源(已經發生,不用改)
      → 分群、算獨立性(`news_clusters`)
    第二階段:**逐事件群**分配全文預算 —— 每群先抓代表,有剩才加抓

排序完全確定性:官方 → 獨立群組數 → 群內最高重要性 → cluster_id。

## 沒有靜默的上限

`plan()` 一定回報 `uncovered_clusters`(排得到但預算不夠的事件群)。
「抓了十篇」與「十篇涵蓋了幾個事件、漏了哪幾個」是兩件事,而只講前者
會讀起來像涵蓋完整。
"""
from __future__ import annotations

from typing import Optional

#: 重要性由高到低。**排序用,不是門檻** —— 門檻是預算。
_IMPORTANCE = {"critical": 0, "high": 1, "medium": 2, "low": 3}

#: 一個事件群最多抓幾篇。第二篇的用途是**互相對照**(同一事件的兩個
#: 獨立群組寫的不一樣時,那個差異本身就是資訊);第三篇的邊際極低。
MAX_PER_CLUSTER = 2


def _imp(item) -> int:
    return _IMPORTANCE.get(str((item or {}).get("importance") or ""), 9)


def _rank(cluster: dict, by_id: dict) -> tuple:
    """事件群的抓取優先序。**官方 → 獨立群組數 → 群內最高重要性 → ID。**"""
    members = [by_id.get(m) for m in (cluster.get("member_source_ids") or [])]
    best = min([_imp(m) for m in members if m], default=9)
    return (not cluster.get("official"),
            -int(cluster.get("independent_sources") or 0),
            best, str(cluster.get("cluster_id") or ""))


def _fetchable(item) -> bool:
    """已經有全文、或根本沒有可抓的連結時,這一格不該佔預算。"""
    if not isinstance(item, dict) or item.get("fulltext"):
        return False
    link = str(item.get("link") or item.get("source_url") or "")
    return link.startswith("http")


def plan(news: Optional[list], clusters: Optional[list],
         budget: int = 26) -> dict:
    """回 `{targets, per_cluster, uncovered_clusters, budget, basis}`。

    **純函式**:不抓網路、不改輸入。`targets` 是有序的 `source_item_id`
    清單,呼叫端照順序抓到預算用完(或時間用完)為止 —— 順序本身就是
    優先序,所以中途停下來也是**從最重要的事件開始有全文**。
    """
    by_id = {str(n.get("source_item_id")): n for n in (news or [])
             if isinstance(n, dict) and n.get("source_item_id")}
    ranked = sorted([c for c in (clusters or []) if isinstance(c, dict)],
                    key=lambda c: _rank(c, by_id))
    targets: list = []
    per_cluster: list = []
    for c in ranked:
        members = [str(m) for m in (c.get("member_source_ids") or [])]
        rep = str(c.get("representative_source_id") or "")
        # 代表先(它是分群時資訊量最高的那則),其餘依重要性、ID 決勝
        order = ([rep] if rep in members else []) + sorted(
            [m for m in members if m != rep],
            key=lambda m: (_imp(by_id.get(m)), m))
        picked = [m for m in order if _fetchable(by_id.get(m))][:MAX_PER_CLUSTER]
        per_cluster.append({"cluster_id": str(c.get("cluster_id") or ""),
                            "picked": picked, "size": len(members)})
        targets.extend(picked)
    # **先每群一篇,再回頭補第二篇** —— 預算不夠時,涵蓋的事件數優先於
    # 單一事件的深度。上一版正好相反(同一事件抓到第四篇,別的事件掛零)。
    first = [p["picked"][0] for p in per_cluster if p["picked"]]
    second = [m for p in per_cluster for m in p["picked"][1:]]
    ordered = first[:budget] + second[:max(0, budget - len(first))]
    covered = {m for m in ordered}
    uncovered = [p["cluster_id"] for p in per_cluster
                 if p["picked"] and not (set(p["picked"]) & covered)]
    return {
        "targets": ordered,
        "per_cluster": per_cluster,
        # **不做靜默的上限**:排得到卻沒預算的事件群要說出來。
        "uncovered_clusters": uncovered,
        "budget": int(budget),
        "basis": ("逐事件群分配:官方 → 獨立群組數 → 群內最高重要性;"
                  "先每群一篇再補第二篇(涵蓋的事件數優先於單一事件深度)"),
    }


def plan_for_run(news: Optional[list], recorder=None, budget: int = 26) -> list:
    """生產用的一行入口:分群 → 排計畫 → 記進 recorder → 回 `targets`。

    `recorder` 是 `ManifestRecorder`(相位不得直接碰 `_RUN_MANIFEST`,
    見 `test_main_decomposition` 的棘輪)。沒給就只回計畫,不記錄。
    """
    import news_clusters as _nc
    out = plan(news, _nc.clusters(news), budget=budget)
    rec = getattr(recorder, "record_fulltext_plan", None)
    if callable(rec):
        rec(out)
    return out["targets"]
