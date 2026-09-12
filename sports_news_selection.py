"""Offline feed selection before the display cap; no result inference."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Mapping

from news_display_quality import unique
from sports_quality import news_allowed


def stale_scorecard(title: str, today: dt.date) -> bool:
    """Only reject dated score-card titles, never general historical discussion."""
    if not re.search(r"\d+\s*[:：]\s*\d+", title):
        return False
    match = re.search(r"[（(](\d{1,2})/(\d{1,2})[）)]\s*(?:[|｜]|$)", title)
    if not match:
        return False
    dates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            dates.append(dt.date(year, int(match[1]), int(match[2])))
        except ValueError:
            continue
    if not dates:
        return False
    event = min(dates, key=lambda date: abs((date - today).days))
    return (today - event).days > 7


def select(entries: Iterable[Mapping[str, Any]], now: dt.datetime,
           limit: int = 3) -> tuple[list[dict[str, str]], dict[str, int]]:
    if now.tzinfo is None:
        raise ValueError("sports selection requires timezone-aware report time")
    cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(hours=30)
    report = {"input": 0, "excluded": 0, "undated": 0, "duplicates": 0}
    candidates = []
    for entry in entries:
        report["input"] += 1
        title = str(entry.get("title") or "").strip()
        if (not title or not news_allowed(entry) or stale_scorecard(title, now.date())
                or re.match(r"^\s*[\[【]推薦[\]】]\s*(?:網球|MLB|NBA|中職|棒球)", title, re.I)):
            report["excluded"] += 1
            continue
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        try:
            published = dt.datetime(stamp[0], stamp[1], stamp[2], stamp[3], stamp[4],
                                    stamp[5], tzinfo=dt.timezone.utc) if stamp else None
        except (ValueError, TypeError, OverflowError, IndexError, KeyError):
            published = None
        if published is not None and (published < cutoff or published > now + dt.timedelta(minutes=5)):
            report["excluded"] += 1
            continue
        if published is None:
            report["undated"] += 1
        candidates.append({"title": title, "link": str(entry.get("link") or "")})
    distinct = unique(candidates)
    report["duplicates"] = len(candidates) - len(distinct)
    chosen = distinct[:max(0, limit)]
    report["selected"] = len(chosen)
    return chosen, report
