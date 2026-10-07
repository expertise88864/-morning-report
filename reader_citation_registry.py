"""Bound public source-title hashes; a destination alone proves no wording."""
from hashlib import sha256
from itertools import islice
from typing import Any, Iterable
from urllib.parse import quote


MAX_SOURCE_ROWS = 600


def url_variants(url: str) -> tuple[str, ...]:
    """Use only the same two percent-encoding forms as the HTML renderer."""
    return tuple(dict.fromkeys((url, quote(url, safe=':/?=&%'),
                                quote(url, safe=':/?=&%#@+;,$!-_~'))))


def title_hashes(title: object) -> tuple[str, ...]:
    """Accept literal fetched titles and the renderer's bracket escaping only."""
    if not isinstance(title, str) or not title.strip() or len(title) > 4096:
        return ()
    labels = (title, title.replace('[', '（').replace(']', '）'))
    return tuple(dict.fromkeys(sha256(label.encode('utf-8')).hexdigest() for label in labels))


def source_bindings(rows: Iterable[object]) -> list[tuple[str, str]]:
    """Only existing Python source rows, not model-provided link registries."""
    result: list[tuple[str, str]] = []
    for row in islice(rows, MAX_SOURCE_ROWS):
        if not isinstance(row, dict):
            continue
        url = row.get('url') or row.get('link')
        if not isinstance(url, str) or len(url) > 4096:
            continue
        result.extend((value, digest) for value in url_variants(url)
                      for digest in title_hashes(row.get('title')))
    return list(dict.fromkeys(result))


def analysis_bindings(quotes: dict[str, Any]) -> list[tuple[str, str]]:
    """Read only Python-built packet/NEWS_LINK_INDEX title metadata."""
    result: list[tuple[str, str]] = list(quotes.get('_ANALYSIS_PACKET_TITLES') or [])
    for row in (quotes.get('NEWS_LINK_INDEX') or [])[:MAX_SOURCE_ROWS]:
        if isinstance(row, dict) and isinstance(row.get('u'), str):
            result.extend((url, digest) for url in url_variants(row['u'])
                          for digest in (row.get('title_hashes') or ()))
    return result


def matches(url: str, label: str, bindings: Iterable[tuple[str, str]]) -> bool:
    """Missing or mismatched metadata fails closed, without registering prose."""
    hashes = title_hashes(label)
    return bool(hashes and (url, hashes[0]) in bindings)
