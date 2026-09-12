"""Publication dates for RSS/ISO episodes; never infer a recording date."""
from __future__ import annotations

import datetime as dt
import re
from email.utils import parsedate_to_datetime

TPE = dt.timezone(dt.timedelta(hours=8))


def published_at(raw) -> dt.datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        stamp = dt.datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=TPE)
    except ValueError:
        try:
            stamp = parsedate_to_datetime(raw.strip())
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return None
    try:
        return stamp.astimezone(TPE)
    except (ValueError, OverflowError):
        return None


def report_day(as_of=None) -> dt.date | None:
    if as_of is None:
        return dt.datetime.now(TPE).date()
    if isinstance(as_of, dt.datetime):
        return as_of.astimezone(TPE).date() if as_of.tzinfo else as_of.date()
    if isinstance(as_of, dt.date):
        return as_of
    if isinstance(as_of, str) and as_of[10:11] == 'T':
        stamp = published_at(as_of)
        return stamp.date() if stamp else None
    try:
        # Production report labels are YYYY-MM-DD (Day).
        if not isinstance(as_of, str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}(?: \((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\))?', as_of):
            return None
        return dt.date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return None


def age_tag(episode: dict, as_of=None) -> str:
    stamp = published_at(episode.get('published'))
    if stamp is None:
        return ''
    label = f" ・{stamp:%m/%d} 發布"
    day = report_day(as_of)
    if day is None:
        return label
    days = (day - stamp.date()).days
    if days < 0:
        return label + '（發布日期晚於本報，時效待確認）'
    if days >= 7:
        return label + f'（約 {days} 天前，內容可能已非當前盤勢）'
    return label
