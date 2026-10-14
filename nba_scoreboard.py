"""Bounded ESPN schedule recovery and status-aware, escaped game summaries."""
from __future__ import annotations

import datetime as dt
import html
import sys
from zoneinfo import ZoneInfo

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
TPE = ZoneInfo("Asia/Taipei")
ET = ZoneInfo("America/New_York")


def week_events(get, now: dt.datetime, days: int = 7, url: str = URL, back: int = 0) -> list[dict]:
    """NBA (and since 2026-10 MLB) reject date ranges with HTTP 400; at most eight daily requests."""
    start = (now - dt.timedelta(days=back)).astimezone(ET).date()
    end = (now + dt.timedelta(days=days)).astimezone(ET).date()
    events, seen = [], set()
    for offset in range(min((end - start).days + 1, 8)):
        response = get(url, params={"dates": (start + dt.timedelta(days=offset)).strftime("%Y%m%d")}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("events"), list):
            raise ValueError("NBA schedule missing events")
        for event in payload["events"]:
            key = event.get("id") or (event.get("date"), event.get("shortName"))
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    return events


def result_row(event: dict, fallback_date: str) -> dict | None:
    """Filter lookback results without letting a scheduled game stop the search."""
    comp = (event.get("competitions") or [{}])[0]
    status = (event.get("status") or comp.get("status") or {}).get("type") or {}
    if status.get("name") in {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_DELAYED", "STATUS_SUSPENDED"}:
        return None
    if not status.get("completed"):   # 進行中的比分不是賽果(2026-10-14 晚寄的信列出「第 2 節」)
        return None
    return game_row(event, fallback_date)


def game_row(event: dict, fallback_date: str) -> dict | None:
    """Never present scheduled 0:0 as a result; event timestamps are Taipei-local."""
    comp = (event.get("competitions") or [{}])[0]
    teams = comp.get("competitors") or []
    if len(teams) != 2:
        return None
    status = (event.get("status") or comp.get("status") or {}).get("type") or {}
    state = status.get("state")
    away = next((t for t in teams if t.get("homeAway") == "away"), teams[0])
    home = next((t for t in teams if t.get("homeAway") == "home"), teams[1])
    def name(team):
        value = html.escape(str((team.get("team") or {}).get("abbreviation") or "?"))
        return f"<b>{value}</b>" if status.get("completed") and team.get("winner") else value
    text = f"{name(away)} vs {name(home)}"
    if state in {"in", "post"} or status.get("completed"):
        text = f"{name(away)} {html.escape(str(away.get('score', '-')))}:{html.escape(str(home.get('score', '-')))} {name(home)}"
    label = "已完賽" if status.get("completed") else {"in": "進行中", "pre": "未開賽"}.get(state, "狀態未確認")
    status_name = str(status.get("name") or "")
    if status_name in {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_DELAYED", "STATUS_SUSPENDED"}:
        label = {"STATUS_POSTPONED": "延期", "STATUS_CANCELED": "取消", "STATUS_DELAYED": "延遲", "STATUS_SUSPENDED": "暫停"}[status_name]
        text = f"{name(away)} vs {name(home)}"
    clock = (event.get("status") or comp.get("status") or {}).get("displayClock")
    period = (event.get("status") or comp.get("status") or {}).get("period")
    if state == "in" and period:
        label += f"・第 {period} 節" + (f" {clock}" if clock else "")
    phase = {1: "季前賽", 2: "例行賽", 3: "季後賽"}.get((event.get("season") or {}).get("type"), "")
    note = ((comp.get("notes") or [{}])[0].get("headline") or "")
    try:
        timestamp = dt.datetime.fromisoformat(str(event.get("date") or "").replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("missing timezone")
        date = timestamp.astimezone(TPE).strftime("%m/%d %H:%M")
    except ValueError:
        date = fallback_date
        print("::warning::[sports] NBA event missing reliable start time", file=sys.stderr)
    return {"text": text, "date": date, "series": (comp.get("series") or {}).get("summary", ""),
            "note": "・".join(str(x) for x in (phase, label, note) if x)}
