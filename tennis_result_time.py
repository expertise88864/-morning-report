"""Reject source-labelled tennis results that are after the report cutoff."""

import datetime as dt


def eligible_result_timestamp(comp: dict, event: dict, now: dt.datetime) -> str:
    """Return a timestamp only when its timezone-aware start is not in the future.

    A provider's completed flag alone is insufficient: the 2026-09-26
    morning email showed a result dated 2026-09-27. Unknown dates are omitted
    rather than silently interpreted in the runner's local timezone.
    """
    stamp = str(comp.get("date") or event.get("date") or "")
    try:
        start = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if start.tzinfo is not None and start <= now:
            return stamp
    except ValueError:
        pass
    return ""
