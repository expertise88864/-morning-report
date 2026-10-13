"""Exact-date, display-only pulse history; the caller owns atomic persistence."""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path
from urllib.parse import urlsplit


_TPE = dt.timezone(dt.timedelta(hours=8))
_KEEP_DAYS = 35


def _prob(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 100):
        raise ValueError("probability must be finite and within 0..100")
    return value


def _key(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("history identity must be a nonempty trimmed string")
    return value


def _market(value):
    value = _key(value)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError("source_url must be an absolute HTTPS market URL")
    return value


def _day(now):
    if not isinstance(now, dt.datetime) or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(_TPE).date()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load(path, today):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(text, object_pairs_hook=_pairs)
    if (not isinstance(data, dict) or set(data) != {"version", "days"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["days"], dict)):
        raise ValueError("invalid pulse history schema")
    for date, markets in data["days"].items():
        parsed = dt.date.fromisoformat(date)
        if parsed.isoformat() != date or parsed > today or not isinstance(markets, dict):
            raise ValueError("invalid or future snapshot date")
        for url, outcomes in markets.items():
            _market(url)
            if not isinstance(outcomes, dict) or not outcomes:
                raise ValueError("invalid snapshot outcomes")
            for key, value in outcomes.items():
                _key(key)
                _prob(value)
    return data["days"]


def _rows(rows):
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    result = copy.deepcopy(rows)
    seen = set()
    for row in result:
        if not isinstance(row, dict):
            raise ValueError("invalid pulse row")
        url = _market(row.get("source_url"))
        if url in seen:
            raise ValueError("duplicate market")
        seen.add(url)
        outcomes = row.get("outcomes")
        if not isinstance(outcomes, list) or not outcomes:
            raise ValueError("nonempty structured outcomes required")
        limit = row.get("display_limit", len(outcomes))
        if type(limit) is not int or limit < 1:
            raise ValueError("display_limit must be a positive integer")
        keys = set()
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                raise ValueError("invalid outcome")
            name = _key(outcome.get("name"))
            key = _key(outcome.get("hist_key", name))
            if key in keys:
                raise ValueError("duplicate outcome identity")
            keys.add(key)
            _prob(outcome.get("prob_raw", outcome.get("prob")))
    return result


def _number(value):
    text = format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _detail(row, baseline, missing):
    parts = []
    for outcome in row["outcomes"][:row.get("display_limit")]:
        key = outcome.get("hist_key", outcome["name"])
        value = Decimal(str(outcome.get("prob_raw", outcome.get("prob"))))
        if key not in baseline:
            change = missing
        else:
            delta = value - Decimal(str(baseline[key]))
            if not delta:
                change = "0pp"
            else:
                magnitude = "<0.01" if abs(delta) < Decimal("0.01") else _number(abs(delta))
                change = ("↑" if delta > 0 else "↓") + magnitude + "pp"
        parts.append(f"{outcome['name']} {_number(value)}%({change})")
    return "・".join(parts)


def _render(rows, days, today):
    yesterday = days.get((today - dt.timedelta(days=1)).isoformat(), {})
    monday = days.get((today - dt.timedelta(days=today.weekday())).isoformat(), {})
    for row in rows:
        url = row["source_url"]
        row["detail"] = _detail(row, yesterday.get(url, {}), "昨日無快照")
        row.pop("weekly_detail", None)
        if today.weekday() == 6:
            row["weekly_detail"] = _detail(row, monday.get(url, {}), "週一無快照")
    return rows


def prepare(rows, now, path, atomic_write):
    """Return copied rows and atomically write JSON text via callback(path, text).

    Keep today plus 34 prior Taipei dates. Same-day retries merge available
    outcomes, never substitute an older date for a missing comparison baseline.
    Invalid input, corrupt history and write errors propagate to the caller.
    """
    today = _day(now)
    result = _rows(rows)
    days = _load(path, today)
    _render(result, days, today)
    cutoff = today - dt.timedelta(days=_KEEP_DAYS - 1)
    days = {date: markets for date, markets in days.items() if date >= cutoff.isoformat()}
    if result:
        snapshot = days.setdefault(today.isoformat(), {})
        for row in result:
            prices = snapshot.setdefault(row["source_url"], {})
            prices.update({o.get("hist_key", o["name"]): o.get("prob_raw", o.get("prob"))
                           for o in row["outcomes"]})
    atomic_write(path, json.dumps({"version": 1, "days": days}, ensure_ascii=False, allow_nan=False))
    return result


def collect(fetch, now, path, atomic_write, warn):
    """Fetch once; preserve valid current prices on history failure without retrying writes.

    Fetch errors and malformed current rows propagate. History failures are
    observable through warn, and fallback comparisons use no stored baseline.
    """
    rows = fetch()
    today = _day(now)
    valid = _rows(rows)
    try:
        return prepare(valid, now, path, atomic_write)
    except (OSError, ValueError, TypeError) as exc:
        warn(f"Polymarket pulse history unavailable: {type(exc).__name__}")
        return _render(valid, {}, today)
