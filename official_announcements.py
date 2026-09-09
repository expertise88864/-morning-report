"""Issuer identity from the dedicated TWSE announcement adapter, never news tags.

Only reader evidence/history use these copies. Prediction inputs stay untouched.
The provenance marker denotes an ingestion route, not authenticated source text.
"""
import hashlib
import json
import re

MOPS_URL = 'https://mops.twse.com.tw/mops/#/web/t05st01'
PROVENANCE = 'twse:t187ap04_L'


def issuer(item: dict) -> str:
    code = item.get('announcement_issuer')
    url = str(item.get('link') or item.get('url') or '')
    if (item.get('announcement_provenance') == PROVENANCE
            and item.get('official') is True
            and url in (MOPS_URL, MOPS_URL.split('#')[0])
            and isinstance(code, str) and re.fullmatch(r'\d{4}', code)):
        return code
    return ''


def retain(target: dict, source: dict) -> None:
    code = issuer(source)
    if code:
        target.update(announcement_issuer=code, announcement_provenance=PROVENANCE)


def fingerprint_fields(row: dict) -> dict:
    fields = {k: row[k] for k in (
        'document_id', 'title', 'excerpt', 'published_at', 'content_level')}
    # Older observations remain readable with their original fingerprint.
    for key in ('announcement_issuer', 'announcement_provenance'):
        if key in row:
            fields[key] = row[key]
    return fields


def reader_sources(announcements: list) -> list:
    """Call ONLY on ctx.tw_mops / quotes.TW_MOPS, never arbitrary news."""
    out = []
    for raw in announcements or []:
        if not isinstance(raw, dict) or raw.get('link') != MOPS_URL:
            continue
        code = str(raw.get('code') or '')
        if not re.fullmatch(r'\d{4}', code) or not raw.get('title'):
            continue
        item = {k: raw.get(k, '') for k in ('title', 'summary', 'link', 'published')}
        item.update(official=True, source='MOPS', source_name='MOPS', entities=[code],
                    announcement_issuer=code, announcement_provenance=PROVENANCE)
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        item['source_item_id'] = 'n' + hashlib.sha256(key.encode()).hexdigest()[:15]
        out.append(item)
    return out
