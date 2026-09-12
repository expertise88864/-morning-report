"""Publication chronology, never proof of event evolution or causality."""
import news_memory as memory


def display_day(source: dict) -> str:
    stamp = memory.publication_time(source)
    return stamp.astimezone(memory.TPE).date().isoformat() if stamp else '日期不明'


def relation(current: dict, source: dict) -> str:
    current_time = memory.publication_time(current)
    source_time = memory.publication_time(source)
    if current.get('date_missing') or source.get('date_missing') or not current_time or not source_time:
        return 'unknown'
    current_day = current_time.astimezone(memory.TPE).date()
    source_day = source_time.astimezone(memory.TPE).date()
    if source_day == current_day:
        return 'same_day_reporting'
    return 'earlier_reporting' if source_day < current_day else 'later_reporting'


def annotate(contexts: dict, news: list, sources: dict) -> None:
    """Only annotate retained IDs; immutable archived observations stay unchanged."""
    current = {n.get('source_item_id'): n for n in news}
    for sid, context in contexts.items():
        context['publication_relations'] = {
            eid: relation(current.get(sid, {}), sources[eid])
            for eid in context.get('evidence_ids', []) if eid in sources}


def heading(row: dict, packet: dict, ids: list) -> str:
    """Derive the reader label from source dates, not generated claims/metadata."""
    current = next((n for n in packet.get('news', [])
                    if n.get('source_item_id') == row.get('source_item_id')), {})
    sources = {s['evidence_id']: s for s in packet.get('historical_sources', [])}
    kinds = {relation(current, sources.get(eid, {})) for eid in ids}
    if kinds == {'same_day_reporting'}:
        return '同日相關報導:'
    if kinds == {'earlier_reporting'}:
        return '先前報導脈絡:'
    return '相關報導對照:'


def counts(contexts: dict) -> dict:
    """Count articles by relation; mixed articles may occur in several groups."""
    return {kind: sum(kind in c.get('publication_relations', {}).values()
                      for c in contexts.values()) for kind in
            ('earlier_reporting', 'same_day_reporting', 'later_reporting', 'unknown')}
