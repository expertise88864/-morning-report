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
    import editorial_priority
    from reader_editorial import is_macro
    ordered = importance_order(cards, packet)
    distinct = editorial_priority.distinct(ordered, packet)
    selected = []
    for tech in (True, False):
        group = [c for c in distinct if not is_macro(c, packet) and article_is_tech(c, packet) == tech]
        reserve = editorial_priority.required_representatives(group, packet)
        if not tech:
            preferred = finance.balanced([items.get(c.get("source_item_id"), {}) for c in group], 1)
            reserve += [c for c in group if c not in reserve and any(items.get(c.get("source_item_id")) is n for n in preferred)]
        selected.extend((reserve + [c for c in group if c not in reserve])[:4 if tech else 6])
    selected.extend(c for c in distinct if is_macro(c, packet))
    return selected, [c for c in ordered if c not in selected]


def importance_order(cards: list, packet: dict) -> list:
    """Reader ordering does not change authoritative required-event coverage."""
    import editorial_priority
    return editorial_priority.order(cards, packet)
