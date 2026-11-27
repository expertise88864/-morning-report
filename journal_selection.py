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
        if not title or types.intersection({'comment', 'published erratum', 'retraction notice', 'retraction of publication',
                                            'expression of concern', 'interview'}) \
                or re.search(r'^(?:retraction(?: and replacement)?(?: of)?\s*:|retraction and replacement of\b|retraction note\b'
                              r'|notice of retraction\b|expression of concern\b)|\ban interview with\b', title, re.I):
            continue
        if re.search(r'^(?:(?:comments?|commentary|corrections?|corrigendum)$|'
                     r'(?:comments?|commentary|corrections?|corrigendum)(?:\s*:|\s+(?:on|to)\b)|'
                     r'(?:reply|erratum|response to)\b)|'
                     r'(?:[.:—–-]\s*)(?:reply|authors?[’\x27]?\s+reply|author response)$', title, re.I):
            continue
        if not types and re.search(r'^(?:comments?|commentary|corrections?|corrigendum)\s+(?:in|re|regarding|for)\b', title, re.I):
            continue
        key = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', title)).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({'journal': journal, 'pmid': pid, 'title': title})
    return out


def clean_zh(text: str) -> str:
    """拿掉譯者的「語義待摘要確認」提示,只留原文術語(11-10);提示單獨在括號裡時連括號一起拿掉,
    不留「Smoke（）」這種空括號(11-22 實信)。"""
    text = re.sub(r"[，；,;]?\s*語義待摘要確認", "", str(text or ""))
    return re.sub(r"[（(]\s*[）)]", "", text).strip()
