"""Bounded, attributed opinions for comparison, never market-fact evidence."""
from __future__ import annotations

import hashlib
import json

from podcast_dates import published_at

MAX_CONTEXT_CHARS = 24_000
MAX_EPISODES = 14


def project(episodes: list, *, as_of: str, sanitize) -> dict:
    """Project public digest fields only; do not mutate or re-date stored episodes."""
    output = {'version': 1, 'episodes': [], 'omitted_episodes': 0,
              'future_episodes': 0, 'invalid_episodes': 0, 'duplicate_episodes': 0,
              'clock_known': False, 'input_valid': isinstance(episodes, list),
              'usable_as_market_fact': False}
    clock = published_at(as_of)
    output['clock_known'] = clock is not None
    if not isinstance(episodes, list):
        return output
    # Without a trustworthy report clock we cannot exclude future information.
    if clock is None:
        output['omitted_episodes'] = len(episodes)
        return output
    seen = set()
    for episode in episodes:
        if not isinstance(episode, dict) or not isinstance(episode.get('digest'), dict):
            output['invalid_episodes'] += 1
            continue
        stamp = published_at(episode.get('published'))
        if clock and stamp and stamp > clock:
            output['future_episodes'] += 1
            continue
        digest = episode['digest']
        truncated = False
        def text(value, limit):
            nonlocal truncated
            cleaned = sanitize(value) if isinstance(value, str) else ''
            truncated |= len(cleaned) > limit
            return cleaned[:limit]
        show, title = text(episode.get('show'), 80), text(episode.get('title'), 160)
        identity = json.dumps([show, episode.get('guid') or title,
                               episode.get('published')], ensure_ascii=False, default=str)
        oid = 'opinion:' + hashlib.sha256(identity.encode()).hexdigest()[:20]
        if oid in seen:
            output['duplicate_episodes'] += 1
            continue
        points = digest.get('summary_points')
        points = points if isinstance(points, list) else []
        tickers = digest.get('tickers')
        tickers = tickers if isinstance(tickers, list) else []
        row = {'opinion_id': oid, 'show': show, 'title': title,
               'published_at': stamp.isoformat() if stamp else '',
               'date_known': stamp is not None,
               'summary_points': [text(p, 240) for p in points[:3] if isinstance(p, str)],
               'tickers': [{k: text(t.get(k), 160 if k == 'reason' else 40)
                            for k in ('name', 'code', 'market', 'direction', 'reason')}
                           for t in tickers[:5] if isinstance(t, dict)],
               'market_view': text(digest.get('market_view'), 240)}
        if not show or not title or not (row['summary_points'] or row['tickers'] or row['market_view']):
            output['invalid_episodes'] += 1
            continue
        seen.add(oid)
        row['excerpt_limited'] = truncated or len(points) > 3 or len(tickers) > 5
        output['episodes'].append(row)
        if len(output['episodes']) > MAX_EPISODES:
            output['episodes'].pop()
            output['omitted_episodes'] += 1
    # Count omissions before rechecking: counter digit growth also consumes budget.
    while output['episodes'] and len(json.dumps(output, ensure_ascii=False)) > MAX_CONTEXT_CHARS:
        output['episodes'].pop()
        output['omitted_episodes'] += 1
    return output
