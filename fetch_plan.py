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


def _continuing(cluster: dict, by_id: dict, timeline) -> int:
    """這個事件群接得上 timeline 的第幾天(判準共用
    `event_identity.match_days` —— 主體相交**且動作相同**)。"""
    if not timeline:
        return 0
    members = [str(m) for m in (cluster.get("member_source_ids") or [])]
    ents = {str(e) for m in members
            for e in (by_id.get(m, {}).get("entities") or [])}
    titles = " ".join(str(by_id.get(m, {}).get("title") or "") for m in members)
    import event_identity as _eid
    return _eid.match_days(timeline, ents, titles)


def _rank(cluster: dict, by_id: dict, days: int = 0) -> tuple:
    """事件群的抓取優先序。
    **官方 → 獨立群組數 → 延燒中 → 群內最高重要性 → ID。**

    延燒中(昨天以前已在追蹤)排在同獨立度的新事件之前,理由是縱向的:
    延續事件在信裡要寫**增量**(昨天 vs 今天),增量需要全文的細節;
    只有 RSS 兩行摘要時,模型只能把背景再講一次 —— 那正是「延燒第 N 天」
    機制要消掉的重複。放在獨立度**之後**:一個三家證實的新事件仍然
    比一條單來源的延燒尾巴重要。
    """
    members = [by_id.get(m) for m in (cluster.get("member_source_ids") or [])]
    best = min([_imp(m) for m in members if m], default=9)
    return (not cluster.get("official"),
            -int(cluster.get("independent_sources") or 0),
            0 if days >= 2 else 1,
            best, str(cluster.get("cluster_id") or ""))


def _fetchable(item) -> bool:
    """已經有全文、或根本沒有可抓的連結時,這一格不該佔預算。"""
    if not isinstance(item, dict) or item.get("fulltext"):
        return False
    link = str(item.get("link") or item.get("source_url") or "")
    return link.startswith("http")


def plan(news: Optional[list], clusters: Optional[list],
         budget: int = 26, timeline: Optional[list] = None) -> dict:
    """回 `{targets, per_cluster, uncovered_clusters, budget, basis}`。

    **純函式**:不抓網路、不改輸入。`targets` 是有序的 `source_item_id`
    清單,呼叫端照順序抓到預算用完(或時間用完)為止 —— 順序本身就是
    優先序,所以中途停下來也是**從最重要的事件開始有全文**。

    `timeline` 是**事件記錄清單**(昨天為止的 timeline;見
    `timeline_records`)—— 給了就把延燒中的事件排在同獨立度的新事件
    之前(理由見 `_rank`)。折成 `{主體: 天數}` 會讓同主體的新事件
    誤標成延燒(外審補審 F4)。
    """
    by_id = {str(n.get("source_item_id")): n for n in (news or [])
             if isinstance(n, dict) and n.get("source_item_id")}
    cont = {str(c.get("cluster_id") or ""): _continuing(c, by_id, timeline or [])
            for c in (clusters or []) if isinstance(c, dict)}
    ranked = sorted([c for c in (clusters or []) if isinstance(c, dict)],
                    key=lambda c: _rank(c, by_id,
                                        cont.get(str(c.get("cluster_id") or ""), 0)))
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
        # **「分不出事件群」與「今天根本沒有新聞」是兩件事**
        # (第一輪外審 F3):前者是接線壞了,後者是上游斷料。
        # 沒有這個數字的話,兩者在 manifest 裡長得一模一樣。
        "available_news": len([n for n in (news or []) if isinstance(n, dict)]),
        # **不做靜默的上限**:排得到卻沒預算的事件群要說出來。
        "uncovered_clusters": uncovered,
        "budget": int(budget),
        # 延燒優先真的有沒有生效,manifest 要看得出來 —— 0 可能是
        # 「今天沒有延燒事件」也可能是「timeline 沒接上」,分不開的話
        # 接線斷了不會有人發現(2026-08-06 兩階段抓取整段 no-op 的教訓)。
        "continuing_boosted": sorted(k for k, d in cont.items() if d >= 2),
        "timeline_events": len(timeline or []),
        "basis": ("逐事件群分配:官方 → 獨立群組數 → 延燒中 → 群內最高"
                  "重要性;先每群一篇再補第二篇(涵蓋的事件數優先於"
                  "單一事件深度)"),
    }


def timeline_records(path) -> list:
    """昨天為止的事件 timeline → **記錄清單**(讀不到回空清單)。

    外審補審 F4:先前這裡回 `{主體: max(天數)}` —— 同一個主體的兩個
    活躍事件(荷姆茲第 7 天、制裁第 2 天)被壓成一格,於是制裁案第一天
    就拿到 7 天與全文優先權。**動作是身分的一部分,不能在載入時就丟掉。**

    讀的是 state 檔的原始形狀而不是渲染後的清單 —— 抓取發生在今天的
    timeline 更新**之前**,昨天的 state 正是「哪些事在延燒」的最新事實。
    **讀不到就不加權,不是不抓**:降級方向是退回今天以前的排序。
    """
    try:
        import json
        import pathlib
        state = json.loads(
            pathlib.Path(str(path)).read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return [dict(v, key=k) for k, v in state.items() if isinstance(v, dict)]


def plan_for_run(news: Optional[list], recorder=None, budget: int = 26,
                 timeline_file=None) -> list:
    """生產用的一行入口:**補 ID** → 分群 → 排計畫 → 記進 recorder → 回 `targets`。

    `recorder` 是 `ManifestRecorder`(相位不得直接碰 `_RUN_MANIFEST`,
    見 `test_main_decomposition` 的棘輪)。沒給就只回計畫,不記錄。

    **補 ID 是這裡的責任,不是呼叫端的**(第二十四輪 P1-1)。分群與本計畫都以
    `source_item_id` 索引,而它原本要到 EvidencePacket 的 `normalize_news()`
    才產生 —— 生產接線在那之前就跑完了,於是 2026-08-06 的 manifest 是
    `available news = 563`、`clusters = 0`、`targets = 0`:兩階段抓取整段 no-op。

    修法刻意放在入口而不是要求呼叫端「記得先補」—— 忘記正是這個缺陷的成因。
    `assign_source_item_ids()` 就地寫入且冪等,所以呼叫端同一個 list 拿去
    `fetch_news_fulltext()` 也看得到 ID,而後面的 `normalize_news()` 不會改號。
    """
    import news_clusters as _nc
    import news_ids as _nids
    news = _nids.assign_source_item_ids(news)
    out = plan(news, _nc.clusters(news), budget=budget,
               timeline=timeline_records(timeline_file) if timeline_file else [])
    rec = getattr(recorder, "record_fulltext_plan", None)
    if callable(rec):
        rec(out)
    return out["targets"]
