"""Conservative point-in-time checks for model-only historical context."""
from __future__ import annotations

from datetime import date, datetime
import re


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def day(value) -> date | None:
    text = str(value or "")
    if not _DATE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def timestamp(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def generated_after_asof(value, as_of: datetime | None, cutoff: date) -> bool:
    if not value:
        return False
    generated = timestamp(value)
    if generated is None or generated.date() >= cutoff:
        return True  # Unknown/later generation is not point-in-time evidence.
    if as_of is None:
        return False
    try:
        return generated > as_of
    except TypeError:
        return True  # Mixed naive/aware timestamps cannot be compared safely.
