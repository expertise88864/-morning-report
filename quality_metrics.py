# -*- coding: utf-8 -*-
"""**這些指標量的是「有沒有真的做到」,不是「有沒有填欄位」**(第十九輪 P2-3)。

## 為什麼要另開一個模組

既有的 `analysis_metrics` 與 `analysis_stages.depth_metrics` 量的多半是
**存在性**:有幾則、有沒有非空、ID 合不合法。外審點出七種 false green,
它們的共同形狀是:

    dashboard 顯示 coverage / grounding / depth 接近 100%,
    而信裡仍然只有「台積電偏多、情緒改善」。

最危險的四種:

  * **駁回也算 covered** —— 必分析事件寫一句「影響有限」就進了分子;
  * **同一個實體覆蓋一整天** —— 「台積電」出現一次,所有提到台積電的
    新聞都算談過了;
  * **合法但不相關的證據算 grounded** —— 同向訊號引用一則航運新聞;
  * **`asset_id="市場"` 算逐標的分析** —— renderer 還會把它排得很漂亮。

## 這裡刻意**不**做的事

**不合成總分。** 分數會被當成「品質」,而各項退步就分不出是哪一項。
**不擋任何東西** —— 有已知誤判的指標當門檻比沒有指標更糟(這個 repo
栽過)。它們的用途是十配對的判讀,以及「下一版有沒有真的變好」。
"""
from __future__ import annotations

from typing import Optional

QUALITY_METRICS_VERSION = 1


def _news(obj) -> list:
    return [n for n in ((obj or {}).get("top_news_analysis") or [])
            if isinstance(n, dict)]


def _rate(hit: int, total: int):
    return round(hit / total, 3) if total else None


def required_event_coverage(obj, packet) -> dict:
    """**駁回不算覆蓋。**

    先前 `_coverage_problems` 接受「分析了」或「說明為什麼不談」——
    那是**合格**的判準,對的。但指標若把兩者都算進分子,
    「今天六件必分析事件全部駁回」與「六件全部分析」會得到同一個數字。
    """
    import news_clusters as _nc
    info = (packet or {}).get("news_clusters") or {}
    need = list(info.get("required_cluster_ids") or ())
    groups = info.get("clusters") or []
    analysed = {_nc.cluster_of(groups, str(n.get("source_item_id") or ""))
                for n in _news(obj)}
    dismissed = {str((d or {}).get("cluster_id") or "")
                 for d in ((obj or {}).get("dismissed_events") or [])
                 if isinstance(d, dict)}
    hit = [c for c in need if c in analysed]
    return {
        "required": len(need),
        "analysed": len(hit),
        "dismissed": len([c for c in need if c not in analysed and c in dismissed]),
        # **分子只有真的分析過的** —— 駁回另外報一格。
        "true_coverage_rate": _rate(len(hit), len(need)),
        "official_required": len([c for c in groups
                                  if c.get("cluster_id") in need
                                  and c.get("official")]),
    }


def event_fingerprint_coverage(text: str, packet) -> dict:
    """**以事件群為單位**,而不是逐則新聞。

    既有的 `evidence_coverage` 用「實體或標題前八字出現在文字裡」判定,
    於是「台積電」被提到一次,當天所有提到台積電的新聞都算覆蓋 ——
    一個實體撐起整份覆蓋率。改成一群只算一次,而且要**這一群自己的**
    標題指紋命中。
    """
    body = str(text or "")
    info = (packet or {}).get("news_clusters") or {}
    groups = info.get("clusters") or []
    by_id = {str(n.get("source_item_id")): n
             for n in ((packet or {}).get("news") or []) if isinstance(n, dict)}
    if not groups:
        return {"clusters": 0, "covered": 0, "rate": None}
    covered = 0
    for c in groups:
        for sid in (c.get("member_source_ids") or ()):
            title = str((by_id.get(str(sid)) or {}).get("title") or "")
            if title and title[:8] in body:
                covered += 1
                break
    return {"clusters": len(groups), "covered": covered,
            "rate": _rate(covered, len(groups))}


def alignment_grounding(obj, packet) -> dict:
    """同向訊號的證據**有沒有綁在那一筆上**(不是「有沒有 ID」)。"""
    import analysis_stages as _ast
    import tension_refs as _tr
    need = _tr.required_alignment_ids((packet or {}).get("signal_tensions"))
    rows = [r for r in (((obj or {}).get("cross_market_synthesis") or {})
                        .get("alignment_readings") or []) if isinstance(r, dict)]
    bound = [r for r in rows
             if str(r.get("alignment_id") or "") in need
             and _ast.both_sides_cited(
                 {"tension_id": r.get("alignment_id"),
                  "evidence_ids": r.get("evidence_ids")}, packet)]
    return {"required": len(need), "read": len(rows), "grounded": len(bound),
            "side_grounded_rate": _rate(len(bound), len(need))}


def asset_breakdown_quality(obj) -> dict:
    """逐標的分析裡有多少是**真的標的**。"""
    import analysis_validate as _av
    rows = [(n, a) for n in _news(obj)
            for a in (n.get("affected_assets") or []) if isinstance(a, dict)]
    ids = [str(a.get("asset_id") or "").strip() for _, a in rows]
    generic = [x for x in ids if x in _av._GENERIC_ASSETS]
    dup = 0
    for n in _news(obj):
        got = [str(a.get("asset_id") or "") for a in (n.get("affected_assets") or [])
               if isinstance(a, dict)]
        dup += len(got) - len(set(got))
    with_second = sum(1 for _, a in rows
                      if str(a.get("second_order_effect") or "").strip())
    return {"assets": len(rows), "generic": len(generic),
            "generic_rate": _rate(len(generic), len(rows)),
            "duplicate": dup,
            "second_order_rate": _rate(with_second, len(rows))}


def ordered_chain_completion(obj) -> dict:
    """**順序對的完整鏈**佔高重要性事件的多少。"""
    import analysis_stages as _ast
    hi = [n for n in _news(obj) if n.get("materiality") == "high"]
    ok = [n for n in hi if _ast._ordered_chain(n)]
    broken = [n for n in hi if _ast._stage_order_broken(n)]
    return {"high_materiality": len(hi), "ordered_complete": len(ok),
            "out_of_order": len(broken),
            "ordered_completion_rate": _rate(len(ok), len(hi))}


def claim_graph_saturation(obj) -> dict:
    """**一條主張填滿全信**是另一種 false green。

    四個段落都回指同一條 claim 時,claim graph 的「覆蓋率」是 100%,
    而實際上整封信只有一個根據。**這是觀測,不是門檻** —— 某些日子
    確實由單一驅動主導,把它做成硬性失敗會逼出湊數的主張。
    """
    o = obj or {}
    sections = {"executive_summary": list(o.get("executive_summary_claim_ids") or [])}
    for sec in ("stance", "priced_in", "portfolio_implications"):
        node = o.get(sec)
        if isinstance(node, dict):
            sections[sec] = [str(x) for x in (node.get("claim_ids") or [])]
    used = [c for ids in sections.values() for c in ids]
    claims = [c for c in (o.get("claim_audit") or []) if isinstance(c, dict)]
    top = max((used.count(c) for c in set(used)), default=0)
    return {"claims": len(claims), "sections_mapped": len(sections),
            "distinct_claims_used": len(set(used)),
            # 1.0 = 每一段都靠同一條主張
            "saturation_rate": _rate(top, len(sections))}


def deepen_preservation(before, after) -> dict:
    """加深有沒有**弄丟已經成立的東西**(第十九輪 P1-11 的可量測版)。"""
    import analysis_depth as _ad
    ib, ia = _ad._identity(before), _ad._identity(after)
    lost = {name: sorted(ib[name] - ia[name])[:5] for name in ib
            if ib[name] - ia[name]}
    total = sum(len(v) for v in ib.values())
    kept = total - sum(len(ib[n] - ia[n]) for n in ib)
    return {"tracked": total, "preserved": kept,
            "preservation_rate": _rate(kept, total), "lost": lost}


def quality_metrics(obj: Optional[dict], packet: Optional[dict],
                    text: str = "") -> dict:
    """全部集合起來。**刻意不合成總分。**"""
    return {
        "schema_version": QUALITY_METRICS_VERSION,
        "required_event_coverage": required_event_coverage(obj, packet),
        "event_fingerprint_coverage": event_fingerprint_coverage(text, packet),
        "alignment_grounding": alignment_grounding(obj, packet),
        "asset_breakdown": asset_breakdown_quality(obj),
        "ordered_chain": ordered_chain_completion(obj),
        "claim_graph": claim_graph_saturation(obj),
    }
