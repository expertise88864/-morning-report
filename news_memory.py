"""Dated source observations, not a memory of generated conclusions.

Borrow temporal provenance from Graphiti without a graph service. Daily gzip
partitions are append-only observations (including revisions), atomically
published by the caller. Retrieval is point-in-time; no historical backdating,
automatic deletion, model scores, or generated summaries are stored here.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import event_identity as identity
import news_normalize
import source_registry
import entity_alias

SCHEMA = 1
LOOKBACK_DAYS = 90
EXCERPT_CHARS = 1200
MAX_PARTITION_BYTES = 32_000_000
TPE = dt.timezone(dt.timedelta(hours=8))


def subjects(item: dict) -> list:
    """Research-only literal names; never write inferred issuer tags to news.

    Subsidiaries/projects remain distinct names, not their holding company's
    issuer identity. Existing declared aliases handle stock/name equivalence.
    """
    base = news_normalize.entities_of(item)
    if not base:
        import finance_editorial
        text = str(item.get("title") or "").replace("臺", "台")
        aliases = [alias for group in entity_alias.ALIAS_GROUPS for alias in group]
        aliases += [alias for group in finance_editorial.ALIASES.values() for alias in group]
        for alias in aliases:
            name = str(alias).replace("臺", "台")
            if len(name) < 2:
                continue
            pattern = re.escape(name)
            if name.isascii():
                pattern = r"(?<![A-Za-z0-9])" + pattern + r"(?![A-Za-z0-9])"
            if re.search(pattern, text, re.I):
                base.append(name)
    return sorted({entity_alias.canonical(str(e)).replace("臺", "台") for e in base})[:16]


def timestamp(value) -> dt.datetime | None:
    """Unknown dates stay unknown; date-only values use end-of-day in Taipei."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return dt.datetime.combine(dt.date.fromisoformat(text), dt.time.max, TPE)
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=TPE)
    except (ValueError, TypeError, OverflowError):
        return None


def publication_time(item: dict) -> dt.datetime | None:
    """Respect the feed adapter's canonical time, including updated-only feeds."""
    return timestamp(item.get("published_dt") or item.get("published") or item.get("published_at"))


def source_url(value) -> str:
    """HTTP links without credentials; no network access or DNS safety claim."""
    try:
        p = urlsplit(str(value or "").strip())
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return ""
        return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
    except ValueError:
        return ""


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False,
                                    sort_keys=True).encode("utf-8")).hexdigest()[:24]


def observations(news, observed_at: str, *, sanitize) -> tuple[list, dict]:
    """Allowlist raw source fields; never serialize arbitrary quotes/portfolio."""
    now = timestamp(observed_at)
    if now is None or sanitize is None:
        raise ValueError("dated observations require time and sanitizer")
    rows, skipped = {}, {"undated": 0, "future": 0, "no_source": 0}
    for n in news or []:
        if not isinstance(n, dict):
            continue
        published = publication_time(n)
        if published is None or n.get("date_missing"):
            skipped["undated"] += 1
            continue
        if published > now:
            skipped["future"] += 1
            continue
        url = source_url(n.get("link") or n.get("url"))
        title = sanitize(str(n.get("title") or ""))[:300]
        if not url or not title:
            skipped["no_source"] += 1
            continue
        body = str(n.get("fulltext") or n.get("summary") or "")
        excerpt = sanitize(body)[:EXCERPT_CHARS]
        row = {
            "document_id": _hash(url), "url": url, "title": title,
            "excerpt": excerpt, "excerpt_truncated": len(sanitize(body)) > EXCERPT_CHARS,
            "content_level": ("fulltext_excerpt" if n.get("fulltext") else
                              "summary" if excerpt else "title_only"),
            "published_at": published.isoformat(), "observed_at": now.isoformat(),
            "event_at": "",  # Publication time is NOT event occurrence time.
            "source": sanitize(str(n.get("source_name") or n.get("source") or ""))[:100],
            "source_group": source_registry.owner_of_item(n),
            "official": bool(n.get("official")),
            "entities": [sanitize(e) for e in subjects(n)],
        }
        # Content revisions receive a new ID; first-observed time is not hashed.
        rid = "history:" + _hash({k: row[k] for k in (
            "document_id", "title", "excerpt", "published_at", "content_level")})
        row["evidence_id"] = rid
        rows.setdefault(rid, row)
    return list(rows.values()), skipped


def _validate(row) -> None:
    if (not isinstance(row, dict) or
            not re.fullmatch(r"history:[0-9a-f]{24}", str(row.get("evidence_id") or "")) or
            not source_url(row.get("url")) or
            not all(isinstance(row.get(k), str) for k in (
                "title", "excerpt", "source", "source_group", "event_at",
                "published_at", "observed_at", "document_id")) or
            not row["title"] or len(row["title"]) > 300 or len(row["excerpt"]) > EXCERPT_CHARS or
            row.get("content_level") not in ("title_only", "summary", "fulltext_excerpt") or
            not isinstance(row.get("official"), bool) or
            not isinstance(row.get("excerpt_truncated"), bool) or row["event_at"] != "" or
            not isinstance(row.get("entities"), list) or
            not all(isinstance(e, str) for e in row["entities"])):
        raise ValueError("invalid source observation")
    expected = "history:" + _hash({k: row[k] for k in (
        "document_id", "title", "excerpt", "published_at", "content_level")})
    if row["document_id"] != _hash(source_url(row["url"])) or row["evidence_id"] != expected:
        raise ValueError("source observation fingerprint mismatch")
    pub, seen = timestamp(row.get("published_at")), timestamp(row.get("observed_at"))
    if pub is None or seen is None or pub > seen:
        raise ValueError("invalid observation chronology")


def read_partition(path: Path) -> list:
    """Corruption propagates: callers may degrade, never reconstruct over it."""
    with gzip.open(path, "rb") as stream:
        data = stream.read(MAX_PARTITION_BYTES + 1)
    if len(data) > MAX_PARTITION_BYTES:
        raise ValueError("news memory partition exceeds read bound")
    obj = json.loads(data)
    if not isinstance(obj, dict) or obj.get("schema") != SCHEMA or not isinstance(obj.get("rows"), list):
        raise ValueError("unsupported news memory partition")
    for row in obj["rows"]:
        _validate(row)
        if path.name != f"{timestamp(row['observed_at']).astimezone(TPE):%Y-%m-%d}.json.gz":
            raise ValueError("source observation in wrong partition")
    if len({r["evidence_id"] for r in obj["rows"]}) != len(obj["rows"]):
        raise ValueError("duplicate source observation")
    return obj["rows"]


def load(directory: Path, as_of: str, days: int = LOOKBACK_DAYS, *, on_unreadable=None) -> list:
    """Point-in-time read, strict by default. Optional caller handles older gaps.

    Today's target always raises on corruption; it must never be reconstructed.
    Older partitions are independent: opt-in callers report exclusions visibly.
    """
    now = timestamp(as_of)
    if now is None:
        raise ValueError("invalid memory as_of")
    day = now.astimezone(TPE).date()
    rows = {}
    for offset in range(max(0, min(days, LOOKBACK_DAYS)) + 1):
        p = directory / f"{day - dt.timedelta(days=offset):%Y-%m-%d}.json.gz"
        if not p.exists():
            continue
        try:
            partition = read_partition(p)
        except Exception as exc:
            if offset == 0 or on_unreadable is None:
                raise
            on_unreadable(p, exc)
            continue
        for row in partition:
            seen = timestamp(row["observed_at"])
            if seen is not None and seen <= now:
                previous = rows.get(row["evidence_id"])
                if previous is None or seen < timestamp(previous["observed_at"]):
                    rows[row["evidence_id"]] = row
    return sorted(rows.values(), key=lambda r: (r["published_at"], r["evidence_id"]))


def save(directory: Path, rows: list, as_of: str, *, atomic_write) -> int:
    """Append to this ingestion day's partition; same-day retries are idempotent."""
    now = timestamp(as_of)
    if now is None:
        raise ValueError("invalid memory write date")
    p = directory / f"{now.astimezone(TPE):%Y-%m-%d}.json.gz"
    existing = read_partition(p) if p.exists() else []
    merged = {r["evidence_id"]: r for r in existing}
    for row in rows:
        _validate(row)
        if timestamp(row["observed_at"]).astimezone(TPE).date() != now.astimezone(TPE).date():
            raise ValueError("observation written into wrong day")
        merged.setdefault(row["evidence_id"], row)
    if len(merged) == len(existing):
        return 0
    payload = json.dumps({"schema": SCHEMA, "rows": sorted(merged.values(),
                         key=lambda r: r["evidence_id"])}, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_PARTITION_BYTES:
        raise ValueError("news memory partition exceeds write bound")
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write(p, gzip.compress(payload, mtime=0))
    return len(merged) - len(existing)


def related(a: dict, b: dict) -> bool:
    """Conservative retrieval, not a mutation of authoritative event identity."""
    ea = set(identity.canonical_subjects(subjects(a)))
    eb = set(identity.canonical_subjects(subjects(b)))
    if not ea.intersection(eb):
        return False
    left = identity.view_identity(a.get("title"), ea, a.get("summary") or a.get("excerpt") or "")
    right = identity.view_identity(b.get("title"), eb, b.get("summary") or b.get("excerpt") or "")
    if left["action"] != right["action"] or left["object"] != right["object"]:
        return False
    # Different quarterly releases are not continuations of one release.
    def periods(t):
        return set(re.findall(r"(?:20\d{2}\s*[Qq][1-4]|[Qq][1-4]|第[一二三四1234]季)", t))
    pa, pb = periods(str(a.get("title") or "")), periods(str(b.get("title") or ""))
    if pa and pb and pa != pb:
        return False
    return identity.incident_match(left["incident_tokens"], right["incident_tokens"]) == identity.MATCH


def retrieve(news: list, archive: list, as_of: str, limit: int = 6) -> tuple[dict, list]:
    """Per-current-item origin + recent changes; archive caps never become facts."""
    now = timestamp(as_of)
    if now is None:
        return {}, []
    limit = max(1, min(6, limit))
    cutoff = now.astimezone(TPE).date()
    index: dict = {}
    for row in archive:
        seen, pub = timestamp(row.get("observed_at")), timestamp(row.get("published_at"))
        if seen is None or pub is None or seen > now or pub > now or pub.astimezone(TPE).date() >= cutoff:
            continue
        for entity in identity.canonical_subjects(subjects(row)):
            index.setdefault(entity, []).append(row)
    contexts, used = {}, {}
    for item in news:
        sid = str(item.get("source_item_id") or "")
        if not sid:
            continue
        candidates = {r["evidence_id"]: r for ent in identity.canonical_subjects(subjects(item))
                      for r in index.get(ent, [])}
        url = source_url(item.get("url") or item.get("link"))
        hits = [r for r in candidates.values() if related(item, r) and
                not (r["url"] == url and r["title"] == item.get("title"))]
        hits.sort(key=lambda r: (timestamp(r["published_at"]), timestamp(r["observed_at"]), r["evidence_id"]))
        selected = hits if len(hits) <= limit else hits[:1] + (hits[-(limit - 1):] if limit > 1 else [])
        contexts[sid] = {"evidence_ids": [r["evidence_id"] for r in selected],
                         "available_observations": len(hits), "omitted_observations": len(hits) - len(selected),
                         "status": "historical_sources" if selected else "no_matched_history"}
        used.update({r["evidence_id"]: r for r in selected})
    return contexts, list(used.values())
