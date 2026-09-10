"""Bounded historical source diversity without changing event identity."""
import datetime as dt

import content_overlap as overlap
from source_text import visible_summary


def select(hits: list, limit: int) -> list:
    """Preserve origin/latest plus distinct intervening weeks and publishers."""
    def identity(row):
        return (row.get('document_id'), row['published_at'], row['title'])

    # A pre-upgrade truncated anchor can contain no visible excerpt. Prefer
    # its readable twin, but never collapse two nonempty factual revisions.
    readable = {identity(r) for r in hits
                if r.get('document_id') and visible_summary(r['excerpt']).strip()}
    unique, seen = [], set()
    for row in hits:
        excerpt = visible_summary(row['excerpt']).strip()
        if not excerpt and identity(row) in readable:
            continue
        text = row['title'] + ' ' + excerpt
        key = overlap.signature(text)
        if key and key in seen:
            continue
        unique.append(row)
        seen.add(key)
    if len(unique) <= limit:
        return unique
    chosen = [unique[0]]
    if limit > 1:
        chosen.append(unique[-1])

    def week(row):
        return dt.date.fromisoformat(row['published_at'][:10]).isocalendar()[:2]
    while len(chosen) < limit:
        weeks = {week(r) for r in chosen}
        groups = {r.get('source_group') for r in chosen if r.get('source_group')}
        rest = [r for r in unique if r not in chosen]
        chosen.append(max(rest, key=lambda r: (week(r) not in weeks,
                      bool(r.get('source_group')) and r.get('source_group') not in groups,
                      r['published_at'], r['evidence_id'])))
    return sorted(chosen, key=lambda r: (r['published_at'], r['evidence_id']))
