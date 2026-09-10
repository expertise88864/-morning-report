"""PubMed selection only; no clinical interpretation or extra API calls."""
import re
import unicodedata


def articles(ids: list, records: dict, journal: str, limit: int) -> list:
    out, seen = [], set()
    def types_of(pid):
        value = (records.get(pid) or {}).get('pubtype') or []
        return {str(t).casefold() for t in (value if isinstance(value, list) else [value])}
    for pid in sorted(ids, key=lambda p: bool(types_of(p).intersection({'editorial', 'letter'}))):
        if len(out) >= max(0, limit):
            break
        item = records.get(pid) or {}
        title = str(item.get('title') or '').strip().rstrip('.')
        types = types_of(pid)
        if not title or types.intersection({'comment', 'published erratum'}):
            continue
        if re.search(r'^(?:(?:comments?|commentary|corrections?)(?:\s*:|\s+(?:on|to)\b)|'
                     r'(?:reply|erratum|response to)\b)|'
                     r'(?:[.:—–-]\s*)(?:reply|authors?[’\x27]?\s+reply|author response)$', title, re.I):
            continue
        key = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', title)).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({'journal': journal, 'pmid': pid, 'title': title})
    return out
