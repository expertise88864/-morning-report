"""Select distinct podcast perspectives against the actual report copy.

Projection only: preserve episode IDs, attribution, tickers and stored digests.
An all-overlap episode keeps one original point rather than vanishing silently.
"""
from __future__ import annotations

import copy
import content_overlap as overlap


def for_report(quotes: dict, text: str, safe_block) -> list:
    episodes = quotes.get('PODCAST_DIGEST') or []
    quotes['PODCAST_CONTENT_AUDIT'] = {}
    return safe_block('Podcast 跨內容比對', project, episodes, text,
                      diagnostics=quotes['PODCAST_CONTENT_AUDIT']) or episodes


def project(episodes: list, report_text: str, *, diagnostics: dict | None = None) -> list:
    references = {overlap.signature(s) for s in overlap.sentences(report_text)}
    output = copy.deepcopy(episodes)
    stats = {'version': 1, 'input_points': 0, 'duplicate_points': 0,
             'output_points': 0, 'all_overlap_episodes': 0}
    for episode in output:
        digest = episode.get('digest') or {}
        points = [p for p in digest.get('summary_points') or [] if isinstance(p, str) and p.strip()]
        stats['input_points'] += len(points)
        unique, repeated = [], []
        seen = set(references)
        for point in points:
            if overlap.covered_keys(point, seen):
                repeated.append(point)
            else:
                unique.append(point)
                seen.update(overlap.signature(s) for s in overlap.sentences(point))
        selected = unique[:5]
        if not selected and repeated:
            selected = repeated[:1]
            stats['all_overlap_episodes'] += 1
        digest['summary_points'] = selected
        episode['digest'] = digest
        references.update(overlap.signature(s) for p in selected for s in overlap.sentences(p))
        stats['duplicate_points'] += len(repeated)
        stats['output_points'] += len(selected)
    if diagnostics is not None:
        diagnostics.update(stats)
    return output
