"""Display-only Fed markets, using the caller's bounded Polymarket transport."""
import datetime as dt


HIKE_TOTAL_SLUG = "how-many-fed-rate-hikes-in-2026-20260623190717369"
HIKE_TOTAL_ZH = {f"{n} ({25 * n} bps)": f"累計{n}碼" for n in range(5)}
HIKE_TOTAL_ZH["5+ (125+ bps)"] = "累計5碼以上"


def decision_rows(now, *, search, outright, annotate, format_line, is_future,
                  months, outcomes, warn):
    """Nearest three future meetings, including year to disambiguate January."""
    rows = []
    try:
        candidates = [event for event in search("Fed decision", limit=20)
                      if isinstance(event, dict) and not event.get("closed")
                      and str(event.get("title", "")).startswith("Fed Decision in")
                      and is_future(event, now.astimezone(dt.timezone.utc))]
        candidates.sort(key=lambda event: str(event.get("endDate")))
    except Exception as exc:
        warn(f"Fed decisions: {exc}")
        return rows
    seen = set()
    for event in candidates:
        month = str(event.get("title", "")).removeprefix("Fed Decision in").strip(" ?")
        slug = str(event.get("slug") or "")
        year = str(event.get("endDate") or "")[:4]
        if month not in months or not slug or not year.isdigit() or (year, month) in seen:
            continue
        seen.add((year, month))
        if len(seen) > 3:
            break
        try:
            options = outright(slug, outcomes, top=5, min_prob=0.0)
            if options:
                label = f"Fed {year}年{months[month]}決議"
                annotate(f"pulse|fed-decision|{slug}", options, now)
                rows.append({"label": label, "detail": format_line(options),
                             "source_url": "https://polymarket.com/event/" + slug})
        except Exception as exc:
            warn(f"Fed {year} {month}: {exc}")
    return rows


def total_hikes_row(now, *, events, outright, annotate, format_line, is_future, warn):
    """Verified 2026 contract measures 25bp units, not the count of meetings."""
    # The calendar-year contract closes at US midnight, not Taipei midnight.
    if now.astimezone(dt.timezone.utc) >= dt.datetime(2027, 1, 1, 4, 59, tzinfo=dt.timezone.utc):
        return []
    try:
        found = events({"slug": HIKE_TOTAL_SLUG})
        if not found or found[0].get("closed") or not is_future(found[0], now.astimezone(dt.timezone.utc)):
            return []
        options = outright(HIKE_TOTAL_SLUG, HIKE_TOTAL_ZH, top=6, min_prob=0.0)
        if not options:
            return []
        annotate(f"pulse|{HIKE_TOTAL_SLUG}", options, now)
        return [{"label": "2026 全年累計升息幅度（1碼=25基點）", "detail": format_line(options),
                 "source_url": "https://polymarket.com/event/" + HIKE_TOTAL_SLUG}]
    except Exception as exc:
        warn(f"Fed annual hikes: {exc}")
        return []
