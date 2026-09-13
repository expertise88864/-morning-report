"""Match a tennis future to a sourced final, never to a rounded market price.

Pure display helpers: no network, persistence, prediction, or implicit current year.
Unknown identity deliberately leaves the market alone.
"""
from __future__ import annotations

import datetime as dt
import re


def result_dates(timestamp: object) -> dict[str, str]:
    """Keep source time for identity and an unambiguous Taipei display date."""
    source = timestamp if isinstance(timestamp, str) else ""
    display = ""
    try:
        instant = dt.datetime.fromisoformat(source.replace("Z", "+00:00"))
        if instant.tzinfo is not None:
            display = instant.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%m/%d")
    except (ValueError, OverflowError):
        pass
    return {"played_at": source, "date": display}


def market_has_final(rows: object, results: object) -> bool:
    """Whether every quote names the same US Open edition with a known final.

    Producers must preserve ``event_slug`` on quotes and the full source timestamp
    as ``played_at`` on results. Old month/day-only results cannot establish year.
    """
    if not isinstance(rows, list) or not rows or not isinstance(results, list):
        return False
    slugs = {row.get("event_slug") for row in rows
             if isinstance(row, dict) and isinstance(row.get("event_slug"), str)}
    if len(slugs) != 1 or any(not isinstance(row, dict)
                             or not isinstance(row.get("event_slug"), str)
                             or row.get("event_slug") not in slugs for row in rows):
        return False
    slug = next(iter(slugs))
    match = re.fullmatch(r"(20\d{2})-(mens|womens)-us-open-winner-tennis", slug)
    if not match:
        return False
    year = int(match[1])
    tour = "ATP" if match[2] == "mens" else "WTA"
    for result in results:
        if not isinstance(result, dict) or result.get("tour") != tour:
            continue
        if result.get("round") != "Final" or not all(
                isinstance(result.get(key), str) and result[key].strip()
                for key in ("winner", "loser")):
            continue
        event = result.get("event_key") or result.get("event")
        if not isinstance(event, str):
            continue
        # Exact names, not substring matching: junior/doubles/qualifying events
        # must not finish the main singles championship.
        name = re.sub(r"\s+", " ", event.strip()).casefold()
        if name not in {"us open", "u.s. open", f"{year} us open", f"us open {year}"}:
            continue
        try:
            played = dt.datetime.fromisoformat(str(result.get("played_at", ""))
                                              .replace("Z", "+00:00"))
            if played.tzinfo is None:
                continue
            played_year = played.astimezone(dt.timezone(dt.timedelta(hours=8))).year
        except (ValueError, OverflowError):
            continue
        if played_year == year:
            return True
    return False
