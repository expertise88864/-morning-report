"""Non-authoritative prior prose, separate from validated views and watch state."""
from __future__ import annotations

import datetime as dt
import json
import re

import analysis_origin as origin
import analysis_recap as recap

ALLOWED_ORIGINS = {origin.LEGACY_PRIMARY, origin.LEGACY_AFTER_LUNA_FAILURE}
MAX_ITEMS = 6
MAX_CHARS = 800


def extract(text: str, news: list, date: str, analysis_origin: str) -> dict:
    """Keep complete, linked public news paragraphs; never infer a claim or entity."""
    if analysis_origin not in ALLOWED_ORIGINS:
        return {}
    dt.date.fromisoformat(date)
    urls = {str(n.get('link') or n.get('url') or '') for n in news if isinstance(n, dict)}
    urls = {url for url in urls if url.startswith(('https://', 'http://'))}
    items = []
    for section in re.split(r'(?m)^(?=#{1,3} )', text):
        heading, _, body = section.partition('\n')
        if heading.lstrip('# ').strip() not in {'八、科技板塊脈動', '九、其他類股資訊'}:
            continue
        for paragraph in re.split(r'\n\s*\n', body):
            paragraph = paragraph.strip()
            if not paragraph or len(paragraph) > MAX_CHARS:
                continue  # Do not truncate a qualifying caveat or opposite argument.
            links = re.findall(r'\[[^\]\n]*\]\((https?://[^\s)]+)\)', paragraph)
            if not links or any(link not in urls for link in links):
                continue
            if re.search(r'持倉|持有股數|我的部位|你的部位|組合曝險|PORTFOLIO', paragraph, re.I):
                continue
            item = {'statement': paragraph, 'source_urls': list(dict.fromkeys(links))}
            if item not in items:
                items.append(item)
    return {'date': date, 'analysis_origin': analysis_origin,
            'status': 'unvalidated_model_opinion', 'items': items[:MAX_ITEMS]}


def save(path, record: dict, atomic_write) -> str:
    """Update only the legacy field; unreadable history is never reconstructed."""
    if not record.get('items'):
        return recap.NOTHING
    prior = recap.load(path)
    if prior.get('unreadable'):
        raise ValueError('existing recap unreadable; preserve history')
    updated = dict(prior, legacy_report=record)
    atomic_write(path, json.dumps(updated, ensure_ascii=False, indent=1))
    return recap.SAVED


def for_prompt(record: dict, previous_session: str, target_session: str) -> dict:
    """No pv IDs: this is attributed model prose, not eligible factual evidence."""
    if (not isinstance(record, dict) or not isinstance(record.get('analysis_origin'), str)
            or record['analysis_origin'] not in ALLOWED_ORIGINS):
        return {}
    try:
        previous, target = dt.date.fromisoformat(previous_session), dt.date.fromisoformat(target_session)
    except (ValueError, TypeError):
        return {}
    if previous >= target or record.get('date') != previous_session:
        return {}
    if record.get('status') != 'unvalidated_model_opinion':
        return {}
    items = record.get('items')
    if not isinstance(items, list) or not items:
        return {}
    return {'date': previous_session, 'analysis_origin': record['analysis_origin'],
            'status': 'unvalidated_model_opinion',
            'items': [{'statement': item['statement'], 'source_urls': item['source_urls']}
                      for item in items[:MAX_ITEMS]
                      if isinstance(item, dict) and isinstance(item.get('statement'), str)
                      and 0 < len(item['statement']) <= MAX_CHARS
                      and isinstance(item.get('source_urls'), list)
                      and 0 < len(item['source_urls']) <= 12
                      and all(isinstance(url, str) and len(url) <= MAX_CHARS
                              and url.startswith(('https://', 'http://'))
                              for url in item['source_urls'])]}
