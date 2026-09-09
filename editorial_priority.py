"""Explainable reader ordering, separate from investment and top-event authority.

Version 1 is a fixed editorial heuristic, not a learned or calibrated model.
Rank concrete developments ahead of previews/promotional roundups, then prefer
new source detail over near-identical historical text. Existing event rank breaks
ties. No stance/forecast weights, required evidence sets or archive writes change.
"""
import re

import content_overlap as overlap
import event_score

VERSION = 2
_DEVELOPMENT = re.compile(
    r'簽署|得標|停產|擴產|量產|召回|裁定|起訴|制裁|禁令|下修財測|上修財測|'
    r'財報|營收|升息|降息|地震|颱風|停火|開戰|併購|收購|'
    r'\b(?:earnings|recall|sanctions?|ceasefire|merger|production|rate cut|rate hike)\b', re.I)
_PREVIEW = re.compile(r'一次看|懶人包|直播|倒數|預告|優惠|促銷|\b(?:preview|livestream|sale)\b', re.I)


def required_representatives(cards: list, packet: dict) -> list:
    reserved = []
    for topic in (packet.get('research') or {}).get('deep_topics', [])[:3]:
        members = topic.get('member_source_ids') or [topic.get('source_item_id')]
        card = next((c for c in cards if c.get('source_item_id') in members), None)
        if card is not None and card not in reserved:
            reserved.append(card)
    return reserved


def order(cards: list, packet: dict) -> list:
    events = packet.get('top_events') or {}
    ids = [r.get('cluster_id') for r in events.get('ranked', [])] or events.get('top_cluster_ids', [])
    ranks = {cid: i for i, cid in enumerate(ids)}
    membership = {sid: c.get('cluster_id') for c in
                  (packet.get('news_clusters') or {}).get('clusters', [])
                  for sid in c.get('member_source_ids', [])}
    sources = {n.get('source_item_id'): n for n in packet.get('news') or []}
    # Only previously validated context IDs can affect novelty; no invented past.
    import news_research_context as context
    valid = context.history_registry(packet)
    history = {n['evidence_id']: n for n in packet.get('historical_sources') or []
               if n.get('evidence_id') in valid}
    contexts = (packet.get('research') or {}).get('contexts') or {}

    def key(card):
        sid = card.get('source_item_id')
        source = sources.get(sid) or {}
        title = str(source.get('title') or '')
        body = title + ' ' + str(source.get('summary') or '')
        development = bool(_DEVELOPMENT.search(title))
        tier = -1 if event_score.is_price_move(title) else (
            1 if development else -1 if _PREVIEW.search(title) else 0)
        repeated = any(overlap.duplicate(body, history[eid]['title'] + ' ' + history[eid]['excerpt'])
                       for eid in (contexts.get(sid) or {}).get('evidence_ids', []) if eid in history)
        from analysis_render_depth import news_subject
        from reader_editorial import recurring_revenue
        repeated = repeated or recurring_revenue(source, [history[eid] for eid in
            (contexts.get(sid) or {}).get('evidence_ids', []) if eid in history],
            news_subject(card, packet).get('name', ''))
        return (-tier, repeated, ranks.get(membership.get(sid), 999))
    return sorted((c for c in cards if isinstance(c, dict)), key=key)


def distinct(cards: list, packet: dict) -> list:
    """Do not suppress a different analysis just because its cluster matches."""
    kept = []
    sources = {n.get('source_item_id'): n for n in packet.get('news') or []}
    reserved = required_representatives(cards, packet)
    for card in reserved + [c for c in cards if c not in reserved]:
        source = sources.get(card.get('source_item_id')) or {}
        text = str(source.get('title') or '') + ' ' + str(source.get('summary') or '')
        meaning = {k: v for k, v in card.items() if k != 'source_item_id'}
        if card not in reserved and any(overlap.duplicate(text, old_text) and meaning == {
                k: v for k, v in old.items() if k != 'source_item_id'} for old, old_text in kept):
            continue
        kept.append((card, text))
    return [c for c in cards if any(c is old for old, _ in kept)]
