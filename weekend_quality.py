"""Offline, dated reading context. Never infer investment stance or causality."""
import datetime as dt
import re
import sys

import news_memory as memory


def load_context(episodes, directory, now, degraded):
    """The caller-owned degradation boundary keeps the Sunday mail available."""
    try:
        return related_news(episodes, directory, now)
    except Exception as exc:
        degraded.append('weekend_podcast_context')
        print(f'::warning::weekend_podcast_context ({type(exc).__name__})', file=sys.stderr)
        return {}


def related_news(episodes, directory, now):
    """Select source titles by explicit company name, not by price or stance.

    These are reading links, not proposition-level corroboration. No portfolio,
    history reconstruction, model request or persistent state is involved.
    """
    archive = memory.load(directory, now.isoformat(), days=7)
    day = now.astimezone(memory.TPE).date()
    if not archive and not any((directory / f'{day - dt.timedelta(days=i):%Y-%m-%d}.json.gz').is_file()
                               for i in range(8)):
        raise FileNotFoundError('recent news archive unavailable')
    names = {str(t.get('name') or '').strip()
             for ep in episodes for t in (ep.get('digest') or {}).get('tickers', [])}
    result = {}
    for name in names:
        if len(name) < 2:
            continue
        rows, seen = [], set()
        for row in reversed(archive):
            stamp = memory.timestamp(row.get('published_at'))
            title = str(row.get('title') or '')
            url = memory.source_url(row.get('url'))
            if (stamp is None or not dt.timedelta(0) <= now - stamp <= dt.timedelta(days=7)
                    or not url or url in seen):
                continue
            # Latin names require word boundaries (AMD must not match an acronym suffix).
            pattern = re.escape(name)
            if name.isascii():
                pattern = r'(?<!\w)' + pattern + r'(?!\w)'
            if not re.search(pattern, title, re.I):
                continue
            seen.add(url)
            rows.append({'title': title, 'url': url, 'published_at': stamp.astimezone(memory.TPE).isoformat()})
        result[name] = sorted(rows, key=lambda r: r['published_at'], reverse=True)[:2]
    return result


def award_key(title):
    """Narrow exact event signature; preserve other organisations/medal counts.

    Only the observed Changhua hospital invention-expo duplicate is covered.
    Negated/corrected/historical coverage must remain distinct.
    """
    if not re.search(r'彰基|彰化基督教醫院', title) or not re.search(r'創博會|創新技術博覽會', title):
        return None
    if re.search(r'未|不|否認|更正|去年|前年|歷年|回顧|取消|撤', title):
        return None
    normalized = title.translate(str.maketrans('一二兩三四五六七八九', '1223456789'))
    medals = re.search(r'(\d+)金\s*(\d+)銀\s*(\d+)銅', normalized)
    if not medals:
        return None
    years = tuple(re.findall(r'\d{3,4}年', normalized))
    return ('彰基創博會', years, medals.groups())
