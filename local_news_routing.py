"""Route dated local feed candidates before category caps and global dedup."""
import re

from news_display_quality import relevant


def destination(label: str, title: str) -> str:
    """Narrow positive topic signals; keep specialist hospital/school coverage."""
    if label not in {"彰化重點追蹤", "建設", "房市", "建商動態", "產業/科技", "選情"}:
        return label
    if re.search(r"競總|造勢|後援會|民調|選情|選戰|競選|選舉", title):
        return "選情"
    if label in {"建設", "產業/科技"} and re.search(r"推案|預售屋|房價|房市|住宅|建案", title):
        if not re.search(r"晶圓廠|半導體廠|廠房|產線", title):
            return "房市"
    return label


def select(candidates: list, queries: list, per_label: int, *, is_dup, seen_entry) -> dict:
    """Keep configured display order/caps; do not let a full bucket claim duplicates."""
    limits = {row[0]: row[2] if len(row) > 2 else per_label for row in queries}
    buckets: dict = {label: [] for label in limits}
    for label, item in candidates:
        title = item["title"]
        target = destination(label, title)
        if target not in buckets or not relevant(target, title):
            continue
        buckets[target].append(item)
    out, seen = {}, []
    for label, items in buckets.items():
        kept = []
        for item in items:
            if len(kept) >= limits[label]:
                break
            if is_dup(item["title"], seen):
                continue
            kept.append(item)
            seen.append(seen_entry(item["title"]))
        if kept:
            out[label] = kept
    return out
