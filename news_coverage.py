"""Deterministic editorial coverage reserves, not investment scores.

Registered feed provenance and dated financial topic mentions are used; neither
implies an issuer or independent confirmation. Required events always win.
"""
from collections import Counter
import finance_editorial as _finance

SECTORS = ("金融", "航運", "生技", "汽車", "傳產", "營建", "房市政策",
           "中彰投建設", "重電", "觀光", "能源")
WORLD = ("國際大事", "災難極端", "科學太空", "AI大事", "中央社國際")
BUCKETS = _finance.GROUPS + ("tech:ai-models",) + tuple("sector:" + s for s in SECTORS) + tuple("world:" + s for s in WORLD)
RESERVE_PER_BUCKET = 3
REGIONAL_SOURCES = frozenset({"類股-房市-中彰投", "類股-建商-中彰投", "類股-建設-中彰投"})


def buckets(item: dict) -> list[str]:
    """Finite allowlist; provenance survives upstream duplicate replacement."""
    old = item.get("coverage_buckets")
    out = {b for b in (old if isinstance(old, list) else ())
           if isinstance(b, str) and b in BUCKETS and b not in _finance.GROUPS}
    source = str(item.get("source") or "")
    if source == "AI模型新聞":
        out.add("tech:ai-models")
    if source in REGIONAL_SOURCES:
        out.add("sector:中彰投建設")
    for sector in SECTORS:
        prefix = "類股-" + sector
        if source == prefix or source.startswith(prefix + "-"):
            out.add("sector:" + sector)
    world = str(item.get("world_cat") or "")
    if not world and source.startswith("世界-"):
        world = source[3:]
    if source == "中央社國際":
        world = source
    if world in WORLD:
        out.add("world:" + world)
    # Topic matches never become company_label/entities or investment scores.
    financial = _finance.groups(item)
    if financial:
        out.update(financial)
        out.add("sector:金融")
    return sorted(out)


def counts(items: list[dict]) -> dict[str, int]:
    tally = Counter(b for item in items for b in buckets(item))
    return {b: tally[b] for b in BUCKETS if tally[b]}


def select(items: list[dict], forced: set, limit: int) -> tuple[list[dict], dict]:
    """Input is quality/time/ID ordered. Round-robin reserves avoid starvation.

    Keep the fixed input budget (except pre-existing required-event overflow).
    Counts describe available *articles*, not verified events or independent
    publishers. Never manufacture a story to meet an editorial quota.
    """
    kept = [x for x in items if x["source_item_id"] in forced]
    seen = {x["source_item_id"] for x in kept}
    by_bucket = {b: [x for x in items if b in buckets(x)] for b in BUCKETS}
    tally = Counter(b for x in kept for b in buckets(x))
    for floor in range(1, RESERVE_PER_BUCKET + 1):
        for bucket in BUCKETS:
            if bucket in _finance.GROUPS and floor > 2:
                continue
            if len(kept) >= limit or tally[bucket] >= floor:
                continue
            candidate = next((x for x in by_bucket[bucket]
                              if x["source_item_id"] not in seen), None)
            if candidate is not None:
                kept.append(candidate)
                seen.add(candidate["source_item_id"])
                tally.update(buckets(candidate))
    for item in items:
        if len(kept) >= limit:
            break
        if item["source_item_id"] not in seen:
            kept.append(item)
            seen.add(item["source_item_id"])
    available, selected = counts(items), counts(kept)
    return kept, {"reserve_per_bucket": RESERVE_PER_BUCKET,
                  "available_articles": available, "selected_articles": selected,
                  "uncovered_buckets": [b for b in available if b not in selected]}
