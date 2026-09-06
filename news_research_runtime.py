"""Optional bounded research outside rendering and investment calculations.

Callbacks keep provider HTTP/circuit breakers and atomic state accounting in
their existing owners. Research failures are visible, never fatal to delivery.
"""
from __future__ import annotations

import datetime as dt
import re
import time

import event_score
import news_memory as memory
import news_normalize
import news_research_context as context

MAX_QUERIES = 5
MAX_RESULTS_PER_QUERY = 4
SEARCH_SECONDS = 75


def observe(recorder, stage: str, news: list) -> None:
    recorder.data.setdefault("news", {}).setdefault("research_funnel", {})[stage] = context.snapshot(news)


def _warn(recorder, key: str, exc=None) -> None:
    recorder.degraded.append(key)
    print(f"::warning::{key}" + (f" ({type(exc).__name__})" if exc else ""), flush=True)


def _anchor(item: dict) -> str:
    title = str(item.get("title") or "") + " " + str(item.get("summary") or item.get("excerpt") or "")
    for ent in list(dict.fromkeys(news_normalize.entities_of(item) + memory.subjects(item))):
        if len(ent) < 2:
            continue
        pattern = re.escape(ent)
        if ent.isascii():
            pattern = r"(?<![A-Za-z0-9])" + pattern + r"(?![A-Za-z0-9])"
        if re.search(pattern, title, re.I):
            return ent
    return ""


def search_plan(news: list, archive: list, as_of: str, *, sanitize) -> list:
    """Three current themes + two quiet, still searchable historical subjects.

    Quiet means no recent observation, NOT that a real-world case is resolved.
    Rotation is date-based so one old subject cannot occupy every daily slot.
    """
    normalized, _, info = news_normalize.normalize_news(news, sanitize)
    rank = event_score.rank(info["clusters"], normalized)
    by_id = {n["source_item_id"]: n for n in normalized}
    selected = [by_id[r["representative_source_id"]] for r in rank["ranked"][:3]]
    now = memory.timestamp(as_of)
    if now is None:
        return []
    latest = {}
    for row in archive:
        anchor = _anchor(row)
        pub = memory.timestamp(row.get("published_at"))
        seen = memory.timestamp(row.get("observed_at"))
        if anchor and pub and seen and seen <= now and pub <= now:
            if anchor not in latest or pub > memory.timestamp(latest[anchor]["published_at"]):
                latest[anchor] = row
    quiet = sorted([r for r in latest.values()
                    if 7 <= (now - memory.timestamp(r["published_at"])).days <= 60],
                   key=lambda r: r["evidence_id"])
    if quiet:
        offset = now.date().toordinal() % len(quiet)
        selected += (quiet[offset:] + quiet[:offset])[:2]
    plan, seen_anchors = [], set()
    for item in selected:
        anchor = _anchor(item)
        if not anchor or anchor in seen_anchors:
            continue
        seen_anchors.add(anchor)
        plan.append({"anchor": anchor,
                     "query": f'"{anchor}" (進度 OR 公告 OR 澄清 OR 延後 OR 下修)',
                     "basis": "current_theme" if item.get("source_item_id") else "quiet_followup"})
    return plan[:MAX_QUERIES]


def enrich(ctx, directory, *, sanitize, atomic_write, fetch_feed, make_url,
           entry_time, entry_source, time_left, reserve: float) -> None:
    """Ingestion + limited extra search. Additional sources never enter scores."""
    recorder, quotes = ctx.recorder, ctx.quotes
    now = dt.datetime.now(memory.TPE)
    as_of = now.isoformat()
    diag = recorder.data.setdefault("news", {}).setdefault("research", {})
    observe(recorder, "analysis_input", ctx.news or [])
    unreadable = []
    def report_unreadable(path, exc):
        unreadable.append(path.name)
        _warn(recorder, "news_memory", exc)
    diag["unreadable_partitions"] = unreadable
    try:
        archive = memory.load(directory, as_of, on_unreadable=report_unreadable)
    except Exception as exc:  # One recovery boundary; load itself raises.
        quotes["NEWS_MEMORY"] = []
        diag["memory_status"] = "unreadable_no_write"
        _warn(recorder, "news_memory", exc)
        return
    quotes["NEWS_MEMORY"] = archive
    diag.update(memory_status="partial_history" if unreadable else "available" if archive else "cold_start",
                historical_observations=len(archive), queries_attempted=0, queries_failed=0)
    extra = []
    try:
        plan = search_plan(ctx.news or [], archive, as_of, sanitize=sanitize)
        diag["queries_planned"] = len(plan)
        deadline = time.monotonic() + SEARCH_SECONDS
        for query in plan:
            # Soft launch budget; leave room for all three HTTP attempts plus
            # connect/read timeouts and backoff. No new call after this boundary.
            if time.monotonic() + 35 > deadline or time_left() < reserve + 35:
                diag["stopped_for_budget"] = True
                _warn(recorder, "news_research_budget")
                break
            diag["queries_attempted"] += 1
            try:
                feed = fetch_feed(make_url(query["query"], when="7d"), timeout=4)
                if feed.get("bozo"):
                    raise ValueError("malformed research feed")
                for entry in (feed.get("entries") or [])[:20]:
                    pub = entry_time(entry)
                    if pub is None or not now - dt.timedelta(days=7) <= pub <= now:
                        continue
                    title, summary = str(entry.get("title") or ""), str(entry.get("summary") or "")
                    candidate = {"title": title, "summary": summary, "entities": [query["anchor"]]}
                    if _anchor(candidate) != query["anchor"]:
                        continue
                    source, source_url = entry_source(entry)
                    candidate.update(link=entry.get("link") or "", published=pub.isoformat(),
                                     source="研究補查", source_name=source, source_url=source_url)
                    if not memory.source_url(candidate["link"]):
                        continue
                    # Up to four per query, not the first four possibly irrelevant hits.
                    candidate["research_query_index"] = diag["queries_attempted"]
                    extra.append(candidate)
                    if sum(n["research_query_index"] == diag["queries_attempted"] for n in extra) >= MAX_RESULTS_PER_QUERY:
                        break
            except Exception as exc:  # Feed helper raises; empty valid feed is not failure.
                diag["queries_failed"] += 1
                _warn(recorder, "news_research_fetch", exc)
        diag["search_seconds"] = round(time.monotonic() - (deadline - SEARCH_SECONDS), 2)
    except Exception as exc:
        _warn(recorder, "news_research_plan", exc)
    # Today/recent discoveries are fresh source evidence; older articles remain
    # background only. Do not feed the 7-day search window to prediction inputs.
    quotes["NEWS_RESEARCH_SOURCES"] = [n for n in extra
                                     if memory.timestamp(n["published"]) >= now - dt.timedelta(hours=30)]
    try:
        as_of = dt.datetime.now(memory.TPE).isoformat()
        rows, skipped = memory.observations((ctx.news or []) + extra, as_of, sanitize=sanitize)
        known = {r["evidence_id"] for r in archive}
        added = memory.save(directory, [r for r in rows if r["evidence_id"] not in known],
                            as_of, atomic_write=atomic_write)
        diag.update(archived_new=added, rejected_observations=skipped,
                    supplementary_articles=len(extra), current_supplementary=len(quotes["NEWS_RESEARCH_SOURCES"]))
        # Newly found older reports were learned today: retain provenance, don't
        # pretend they were in yesterday's memory. Dedicated context can use them
        # as newly retrieved background, never a historical observation then.
        quotes["NEWS_RESEARCH_BACKGROUND"] = [r for r in rows if r["evidence_id"] not in known
                                              and memory.timestamp(r["published_at"]).astimezone(memory.TPE).date() < now.date()]
    except Exception as exc:
        _warn(recorder, "news_memory", exc)
        diag["memory_status"] = "write_failed"


def legacy(quotes, news, *, sanitize) -> str:
    if not news and not quotes.get("NEWS_RESEARCH_SOURCES"):
        return ""
    try:
        return "\n" + context.legacy_block((news or []) + (quotes.get("NEWS_RESEARCH_SOURCES") or []),
                                    (quotes.get("NEWS_MEMORY") or []) + (quotes.get("NEWS_RESEARCH_BACKGROUND") or []),
                                    dt.datetime.now(memory.TPE).isoformat(), sanitize=sanitize)
    except Exception as exc:
        print(f"::warning::news_research_legacy ({type(exc).__name__})", flush=True)
        return ""
