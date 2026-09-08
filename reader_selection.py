"""Read-only editorial selection; no model calls, authority changes or state writes."""
import re


def article_is_tech(card: dict, packet: dict) -> bool:
    from analysis_render_depth import news_subject, is_tech
    from industry_class import is_tech_headline
    import finance_editorial as finance

    item = next((n for n in packet.get("news", []) if isinstance(n, dict)
                 and n.get("source_item_id") == card.get("source_item_id")), {})
    subject = news_subject(card, packet)
    title = str(item.get("title") or "")
    if not finance.groups(item) and re.search(r'iPhone|蘋果.*發表會', title, re.I):
        return True
    if is_tech(subject):
        return True
    # A known non-tech issuer outranks incidental AI vocabulary.
    if finance.groups(item) or subject.get("industry") or subject.get("name") in ("COST", "TMUS"):
        return False
    return is_tech_headline(title) or bool(re.search(
        r"台積|美光|聯亞|廠務工程|矽光子|OpenAI|Anthropic|DeepSeek|Claude|Gemini|ChatGPT", title, re.I))


def select_cards(cards: list, packet: dict) -> tuple[list, list]:
    """Four tech / six other cards, ranked by Python event importance."""
    import finance_editorial as finance
    items = {n.get("source_item_id"): n for n in packet.get("news", []) if isinstance(n, dict)}
    ordered = importance_order(cards, packet)
    selected = []
    for tech in (True, False):
        group = [c for c in ordered if article_is_tech(c, packet) == tech]
        if tech:
            reserve = []
        else:
            preferred = finance.balanced([items.get(c.get("source_item_id"), {}) for c in group], 1)
            reserve = [c for c in group if any(items.get(c.get("source_item_id")) is n for n in preferred)]
        selected.extend((reserve + [c for c in group if c not in reserve])[:4 if tech else 6])
    return selected, [c for c in ordered if c not in selected]


def importance_order(cards: list, packet: dict) -> list:
    """Use existing Python event ranking, preserving source order for unranked items."""
    events = packet.get('top_events') or {}
    order = [r.get('cluster_id') for r in events.get('ranked', [])] or events.get('top_cluster_ids', [])
    ranks = {cid: i for i, cid in enumerate(order)}
    membership = {sid: str(c.get("cluster_id") or "") for c in
                  (packet.get("news_clusters") or {}).get("clusters", [])
                  for sid in c.get("member_source_ids", [])}
    return sorted((c for c in cards if isinstance(c, dict)), key=lambda c:
                     ranks.get(membership.get(c.get("source_item_id")), 999))
