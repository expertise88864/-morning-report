"""Bounded source retrieval and research questions; no investment authority.

STORM inspires perspective/gap questions; Open Deep Research inspires bounded
research and writing separation. GraphRAG inspires theme-level weekly synthesis.
No vendor runtime, new model provider, or unbounded agent loop is introduced.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

import news_memory as memory
import news_coverage
import news_normalize
import source_registry

MAX_HISTORY_CHARS = 36_000
MAX_DEEP_TOPICS = 3
RESEARCH_RULES = """跨週研究規則：
所有重要新聞先對照 research.contexts 與 historical_sources；沒有匹配歷史就不要編前情。
historical_sources 是帶日期的原始報導摘錄，不是本報觀點，也不保證報導內容為真。
歷史引用只能支持「當時報導了什麼」；不能單獨證明今天仍成立、今天的價格或方向。
history: ID 僅能放在 top_news_analysis[].historical_context.evidence_ids，其他引用欄位只用當期證據。
historical_context.evolution 寫一至兩句「先前→本次新增或推翻」，evidence_ids 照抄匹配的歷史 ID。
只有標題時不得補出未載明的機制；沒有更正證據，不可宣稱先前說法已被否認。
research.deep_topics 是 Python 選出的最多三個深入主題；在原有新聞段落深入，不另增重複章節。
逐題檢視 perspectives/questions：原始承諾與實際進度、公司/客戶/供應商/監管者角度、反證。
用既有 mechanism_steps 區分 fact/inference/unknown；傳導每一步說明成立條件與時間差。
confirmation_signal 與 invalidation_signal 必須可觀察，不能把「市場持續關注」當驗證。
回答不了就具體揭露缺少的證據，不增加推測性步驟來湊深度，不自創機率或改 Python 分數。
研究計畫、取材偏好、來源涵蓋統計只供內部使用，不得在晨報透露。
"""


def snapshot(news: list) -> dict:
    """Counts are diagnostics, never the number of independently verified facts."""
    items = [n for n in news if isinstance(n, dict)]
    independence = source_registry.independence(items)
    return {"articles": len(items), "fulltext": sum(bool(n.get("fulltext")) for n in items),
            "summary_only": sum(bool(n.get("summary")) and not n.get("fulltext") for n in items),
            "title_only": sum(not n.get("summary") and not n.get("fulltext") for n in items),
            "undated": sum(not memory.publication_time(n)
                           or bool(n.get("date_missing")) for n in items),
            "known_source_groups": independence["count"],
            "aggregator_only": independence["aggregator_only"],
            "unverified_publishers": independence["unverified"],
            "buckets": news_coverage.counts(items)}


def build(packet: dict, archive: list) -> dict:
    """Attach historical sources for every selected article within a total cap."""
    news = packet.get("news") or []
    contexts, historical = memory.retrieve(news, archive, packet.get("as_of") or "")
    by_history = {r["evidence_id"]: r for r in historical}
    by_news = {n["source_item_id"]: n for n in news}
    cluster_data = packet.get("news_clusters") or {}
    clusters = {c["cluster_id"]: c for c in cluster_data.get("clusters") or []}
    top_ids = (packet.get("top_events") or {}).get("top_cluster_ids") or []
    deep = []
    for cid in top_ids[:MAX_DEEP_TOPICS]:
        cluster = clusters.get(cid) or {}
        sid = cluster.get("representative_source_id")
        if sid not in by_news:
            continue
        deep.append({"cluster_id": cid, "source_item_id": sid,
                     "member_source_ids": cluster.get("member_source_ids") or [sid],
                     "questions": ["原始承諾與目前執行階段有何差距？",
                                   "哪些來源支持、哪些證據可能推翻這條傳導？",
                                   "下一個可觀察里程碑是什麼，日期是否有來源？"],
                     "perspectives": ["公司與執行者", "客戶與供應商", "監管與反方證據"],
                     "content_level": "fulltext" if by_news[sid].get("fulltext") else
                                      "summary" if by_news[sid].get("summary") else "title_only"})
    # First allocate one source per article, then add deeper context. Top themes
    # are first within each round; a long dossier cannot starve every other item.
    order = list(dict.fromkeys([d["source_item_id"] for d in deep] + list(contexts)))
    kept, used_chars = {}, 0
    for step in range(6):
        for sid in order:
            ids = (contexts.get(sid) or {}).get("evidence_ids") or []
            if step >= len(ids) or ids[step] in kept:
                continue
            row = by_history[ids[step]]
            size = len(json.dumps(row, ensure_ascii=False))
            if used_chars + size <= MAX_HISTORY_CHARS:
                kept[ids[step]] = row
                used_chars += size
    for context in contexts.values():
        ids = context["evidence_ids"]
        context["evidence_ids"] = [eid for eid in ids if eid in kept]
        context["omitted_for_budget"] = len(ids) - len(context["evidence_ids"])
    packet["historical_sources"] = list(kept.values())
    packet["research"] = {"version": 1, "contexts": contexts, "deep_topics": deep,
                          "history_chars": used_chars, "history_char_limit": MAX_HISTORY_CHARS,
                          "archive_observations": len(archive),
                          "selected_sources": snapshot(news)}
    return packet


def history_registry(packet: dict) -> dict:
    """Historical source IDs cannot act as current-direction inference evidence."""
    now = memory.timestamp(packet.get("as_of"))
    out = {}
    for row in packet.get("historical_sources") or []:
        seen, pub = memory.timestamp(row.get("observed_at")), memory.timestamp(row.get("published_at"))
        if now is None or seen is None or pub is None or seen > now or pub > now:
            continue
        out[row["evidence_id"]] = {
            "value": None, "unit": "", "as_of": row["published_at"],
            "as_of_precision": "source", "observed_session": "", "session": "",
            "observed_at": row["observed_at"], "source": row.get("source") or "",
            "quote": row["title"] + " — " + row["excerpt"], "url": row["url"],
            "quality": row["content_level"], "usable_for_inference": False,
            "why_unusable": "歷史報導僅供有日期的前情，不是今天的方向證據"}
    return out


def validate(obj: dict, packet: dict) -> list:
    """Fail closed on fake/cross-story history, not on absence of history."""
    import analysis_schema
    contexts = (packet.get("research") or {}).get("contexts") or {}
    registry = history_registry(packet)
    problems = []
    fields = analysis_schema.evidence_id_fields()
    def check_scope(node, path=()):
        if isinstance(node, list):
            for index, child in enumerate(node):
                check_scope(child, (*path, index))
        elif isinstance(node, dict):
            for field, child in node.items():
                current = (*path, field)
                historical = (len(current) == 4 and current[0] == "top_news_analysis"
                              and isinstance(current[1], int)
                              and current[2:] == ("historical_context", "evidence_ids"))
                if (field in fields and isinstance(child, list) and not historical
                        and any(str(eid).startswith("history:") for eid in child)):
                    problems.append(f"{current}: 歷史 ID 僅限 historical_context，不可支持當期推論")
                check_scope(child, current)
    check_scope(obj)
    for index, row in enumerate(obj.get("top_news_analysis") or []):
        if not isinstance(row, dict):
            continue  # Existing schema validation diagnoses the row itself.
        block = row.get("historical_context") or {}
        if not isinstance(block, dict):
            problems.append(f"top_news_analysis[{index}] historical_context 不是物件")
            continue
        ids = block.get("evidence_ids") or []
        allowed = set((contexts.get(row.get("source_item_id")) or {}).get("evidence_ids") or [])
        if block.get("evolution") and not ids:
            problems.append(f"top_news_analysis[{index}] 跨日分析沒有歷史來源引用")
        if any(eid not in allowed or eid not in registry for eid in ids):
            problems.append(f"top_news_analysis[{index}] 引用不存在、未匹配或不可用的歷史來源")
    return problems


def advisories(obj: dict, packet: dict) -> list:
    if not isinstance(packet, dict):
        return []
    research = packet.get("research") or {}
    contexts = research.get("contexts") or {}
    rows = {r.get("source_item_id"): r for r in obj.get("top_news_analysis") or []}
    out = []
    for sid, row in rows.items():
        if ((contexts.get(sid) or {}).get("evidence_ids") and
                not (row.get("historical_context") or {}).get("evolution")):
            out.append(f"{sid} 有可追溯的跨日報導，請補 historical_context 的前情與本次增量並引用")
    for topic in research.get("deep_topics") or []:
        row = next((rows[s] for s in topic["member_source_ids"] if s in rows), None)
        if row is None:
            out.append(f"深入主題 {topic['cluster_id']} 尚未分析；有證據才展開，不得編造補量")
        elif not all(str(row.get(k) or "").strip() for k in
                     ("confirmation_signal", "invalidation_signal", "why_this_magnitude")):
            out.append(f"深入主題 {topic['cluster_id']} 缺少可驗證條件或量級限制")
    return out


def metrics(obj: dict, packet: dict) -> dict:
    """Structural evidence measures, explicitly NOT semantic truth scores."""
    research = packet.get("research") or {}
    contexts = research.get("contexts") or {}
    rows = [r for r in obj.get("top_news_analysis") or [] if r.get("why_it_matters")]
    with_history = [r for r in rows if (contexts.get(r.get("source_item_id")) or {}).get("evidence_ids")]
    valid_ids = history_registry(packet)
    grounded = 0
    for row in with_history:
        block = row.get("historical_context") or {}
        ids = block.get("evidence_ids") or []
        allowed = set(contexts[row["source_item_id"]]["evidence_ids"])
        grounded += bool(block.get("evolution") and ids and all(i in allowed and i in valid_ids for i in ids))
    rendered_ids = {r["source_item_id"] for r in rows}
    return {"analyzed_articles": len(rows), "with_matched_history": len(with_history),
            "with_valid_history_citations": grounded,
            "deep_topics_expected": len(research.get("deep_topics") or []),
            "deep_topics_analyzed": sum(bool(rendered_ids.intersection(t["member_source_ids"]))
                                        for t in research.get("deep_topics") or []),
            "semantic_truth_evaluated": False,
            "selected_source_coverage": research.get("selected_sources") or {},
            "analyzed_source_coverage": snapshot([n for n in packet.get("news") or []
                                                  if n.get("source_item_id") in rendered_ids]),
            "history_budget_omissions": sum(c.get("omitted_for_budget", 0) for c in contexts.values())}


def legacy_block(news: list, archive: list, as_of: str, *, sanitize) -> str:
    """Legacy gets the same provenance rules, in its own non-nested fence."""
    import news_clusters
    import event_score
    normalized, _, cluster_info = news_normalize.normalize_news(news, sanitize)
    packet = {"news": normalized, "news_clusters": cluster_info,
              "as_of": as_of}
    packet["top_events"] = event_score.rank(cluster_info.get("clusters") or news_clusters.clusters(normalized), normalized)
    build(packet, archive)
    import evidence_packet
    data = evidence_packet.sanitize_tree({k: packet[k] for k in ("research", "historical_sources")}, sanitize)
    return (RESEARCH_RULES + "此路徑輸出 Markdown：將前情與來源融入原新聞段落，不输出 JSON 欄位名。\n"
            + "<UNTRUSTED_SOURCE_DATA>\n" + json.dumps(data, ensure_ascii=False) + "\n</UNTRUSTED_SOURCE_DATA>\n")


def history_prose(row: dict, packet: dict) -> str:
    """Compact prose with real source links, including a factual fallback."""
    if not isinstance(packet, dict):
        return ""
    contexts = (packet.get("research") or {}).get("contexts") or {}
    allowed = set((contexts.get(row.get("source_item_id")) or {}).get("evidence_ids") or [])
    sources = {r["evidence_id"]: r for r in packet.get("historical_sources") or []}
    valid = history_registry(packet)
    block = row.get("historical_context") or {}
    if not isinstance(block, dict):
        return ""
    ids = block.get("evidence_ids") or []
    evolution = str(block.get("evolution") or "").strip()
    if evolution:
        if not ids or any(i not in allowed or i not in valid for i in ids):
            return ""  # Direct rendering must not bypass the validation boundary.
    else:
        ids = sorted(allowed.intersection(valid), key=lambda i: sources[i]["published_at"])[:1]
        if not ids:
            return ""
        original = sources[ids[0]]
        title = re.sub(r"[\[\]<>*_`#]", "", original["title"])[:70]
        evolution = f"{original['published_at'][:10]} 曾報導「{title}」；以上為當時報導，非今日新進展。"
    links = [f"[{sources[i]['published_at'][:10]} 原始報導]({quote(sources[i]['url'], safe=':/?=&%')})"
             for i in ids[:2]]
    return "前情與變化:" + evolution.rstrip("。") + "。" + " ".join(links)
