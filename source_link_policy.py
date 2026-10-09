"""Only collected source addresses may become labelled Markdown anchors."""
from __future__ import annotations


def collected_urls(quotes: dict) -> set[str]:
    """Read source-owned fields, never generated analysis or arbitrary prose."""
    urls = set()
    for key, fields in (
        ("NEWS_LINK_INDEX", ("u",)),
        ("NEWS_MEMORY", ("url",)),
        ("NEWS_RESEARCH_BACKGROUND", ("url",)),
        ("NEWS_RESEARCH_SOURCES", ("url", "link")),
        ("TW_MOPS", ("url", "link")),
    ):
        rows = quotes.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                urls.update(row[field] for field in fields
                            if isinstance(row.get(field), str) and row[field])
    return urls
