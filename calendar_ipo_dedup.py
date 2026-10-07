"""Keep duplicate public-subscription rows from repeating in one email."""
from __future__ import annotations


def unique_rows(rows: list[dict]) -> list[dict]:
    """Drop exact repeated offerings, preserving source order and distinct terms."""
    seen: set[tuple] = set()
    result: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Start and lottery rate are not displayed; differing hidden metadata
        # must not create two visually identical lines in the reader's email.
        key = tuple((name, str(row.get(name) or "")) for name in (
            "code", "name", "end", "draw", "price", "market",
            "spread", "profit"))
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result
