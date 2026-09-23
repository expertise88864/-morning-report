"""Reject stale local traffic events that a fresh feed has re-indexed."""

import datetime as dt
import re


def event_date_relevant(label: str, title: str, today: dt.date) -> bool:
    """An explicit event date older than 30 days is not current traffic news."""
    if label != "交通異動":
        return True
    match = re.search(r"(?<!\d)(\d{1,2})\s*[月/]\s*(\d{1,2})\s*日?", title)
    if not match:
        return True
    try:
        days = [dt.date(year, int(match[1]), int(match[2]))
                for year in (today.year - 1, today.year, today.year + 1)]
    except ValueError:
        return True  # Bad headline date alone is not enough to suppress the item.
    if any(-30 <= (today - event).days <= 30 for event in days):
        return True
    # With no year in the title, only explicit future wording can disambiguate
    # a distant notice from an old event re-indexed by the feed.
    future_notice = re.search(r"將|預計|預定|訂於|即將|(?:日|\d)起", title)
    return bool(future_notice and any(-180 <= (today - event).days < -30
                                      for event in days))
