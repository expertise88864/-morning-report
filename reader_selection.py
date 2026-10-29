"""Read-only editorial selection; no model calls, authority changes or state writes."""
import re

#: 前台每類最多幾則(使用者 2026-10-19:與備援相當;原 4 科技 / 6 其他)。
FRONT_LIMIT = 10
#: 使用者 2026-10-29:「太多科技類股新聞」—— 科技最多 6 則,其他類股仍 10 則。
FRONT_LIMIT_TECH, FRONT_LIMIT_OTHER = 6, FRONT_LIMIT


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
    # Rivalry targets are not headline actors, but still supply an industry signal.
    if subject.get("rival_named"):
        return bool(subject.get("rival_tech"))
    from card_curation import tech_by_assets   # 標題的「AI」不代表科技新聞(10-27 DHL 運價)
    if (by_assets := tech_by_assets(card, packet)) is not None:
        return by_assets
    return is_tech_headline(title) or bool(re.search(
        r"台積|美光|聯亞|廠務工程|矽光子|OpenAI|Anthropic|DeepSeek|Claude|Gemini|ChatGPT", title, re.I))


def select_cards(cards: list, packet: dict) -> tuple[list, list]:
    """Ten tech / ten other cards (user 2026-10-19: match the fallback), ranked by Python importance."""
    import finance_editorial as finance
    items = {n.get("source_item_id"): n for n in packet.get("news", []) if isinstance(n, dict)}
    import editorial_priority
    from reader_editorial import is_macro
    import card_curation as cc
    ordered = importance_order(cards, packet)
    distinct = editorial_priority.distinct(ordered, packet)
    reps = editorial_priority.required_representatives(distinct, packet)
    capped, spill = cc.cap_themes([c for c in distinct if not is_macro(c, packet)], packet, reps)  # 同主線先上兩張
    capped = cc.first_per_company(capped, packet, reps)   # 10-28:同一家公司一張,其餘「同公司還有」
    spill = [c for c in distinct if any(c is s for v in spill.values() for s in v)]
    selected = []
    for tech in (True, False):
        group = [c for c in capped if article_is_tech(c, packet) == tech]
        group = group if tech else cc.sector_balanced(group, packet)   # 九段:不同產業先上版
        reserve = editorial_priority.required_representatives(group, packet)
        if not tech:
            preferred = finance.balanced([items.get(c.get("source_item_id"), {}) for c in group], 1)
            reserve += [c for c in group if c not in reserve and any(items.get(c.get("source_item_id")) is n for n in preferred)]
            reserve += cc.taiwan_economy(group, packet, reserve)   # 10-29:至少一則台灣經濟與產業
        limit = FRONT_LIMIT_TECH if tech else FRONT_LIMIT_OTHER
        chosen = (reserve + [c for c in group if c not in reserve])[:limit]
        taken = {cc.company_key(c, packet) for c in selected + chosen} - {""}
        selected.extend(cc.backfill(chosen, [c for c in spill if article_is_tech(c, packet) == tech],
                                    packet, limit, taken))   # 10-28:有空位就補回同主線的卡
    selected.extend(c for c in distinct if is_macro(c, packet))
    return selected, [c for c in ordered if c not in selected]


def importance_order(cards: list, packet: dict) -> list:
    """Reader ordering does not change authoritative required-event coverage."""
    import editorial_priority
    return editorial_priority.order(cards, packet)
