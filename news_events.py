"""結構化新聞事件的純規則層(A5-B5,自 morning_report.py 抽出)。

原則:函式本體自 morning_report.py 逐字搬遷、一字不改;morning_report 以同名
re-export 維持既有測試與呼叫端;驗證以 tools/refactor_audit.py verify-move 為準。
"""
from num_utils import _safe_number
from typing import Optional

from news_rules import (
    NEWS_NEGATIVE_TERMS,
    NEWS_POSITIVE_TERMS,
    _matches_any,
)
import datetime as dt


def _news_event_direction(text: str) -> int:
    """用明確事件詞判斷消息方向；同時有多空詞或沒有方向時不加分。"""
    positive = bool(_matches_any(text, NEWS_POSITIVE_TERMS))
    negative = bool(_matches_any(text, NEWS_NEGATIVE_TERMS))
    if positive == negative:
        return 0
    return 1 if positive else -1

def _event_type(text: str) -> str:
    """Map noisy headlines to a small, learnable event taxonomy."""
    lower = (text or "").lower()
    rules = (
        ("guidance_raise", ("raises guidance", "raise guidance", "上修財測", "調高財測")),
        ("guidance_cut", ("cuts guidance", "cut guidance", "下修財測", "調降財測")),
        ("orders", ("order", "訂單", "接單", "合約", "contract")),
        ("earnings", ("earnings", "eps", "財報", "獲利", "盈餘")),
        ("revenue_growth", ("revenue", "營收", "sales growth")),
        ("export_controls", ("export control", "出口管制", "制裁", "sanction")),
        ("litigation", ("lawsuit", "litigation", "訴訟", "裁罰")),
        ("geopolitical", ("war", "missile", "attack", "戰爭", "飛彈", "攻擊")),
    )
    for event_type, tokens in rules:
        if any(token in lower for token in tokens):
            return event_type
    return "general"

def _parse_news_time(value, now: Optional[dt.datetime] = None) -> dt.datetime:
    """Parse RSS and ISO dates; missing timestamps are treated as fresh but explicit."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = None
        raw = str(value or "").strip()
        if raw:
            try:
                parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                try:
                    from email.utils import parsedate_to_datetime
                    parsed = parsedate_to_datetime(raw)
                except (TypeError, ValueError):
                    parsed = None
    parsed = parsed or now
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)

def _freshness_weight(age_hours: float) -> float:
    """Fresh events matter most; old duplicates fade quickly."""
    if age_hours <= 12:
        return 1.0
    if age_hours <= 24:
        return 0.75
    if age_hours <= 48:
        return 0.45
    return 0.20

def _event_cluster_key(event: dict) -> tuple:
    import re as _re
    title = _re.sub(r"\W+", "", str(event.get("title") or "").lower())[:48]
    if event.get("event_type") != "general":
        title = ""
    return (
        str(event.get("entity") or ""),
        str(event.get("event_type") or "general"),
        int(_safe_number(event.get("direction"))),
        title,
    )

def _event_surprise_score(event: dict) -> float:
    """Estimate how much genuinely new information an event carries."""
    explicit = event.get("surprise_score")
    if explicit is not None:
        return round(max(0.1, min(1.0, _safe_number(explicit, 0.5))), 3)
    text = f"{event.get('title', '')} {event.get('summary', '')}".lower()
    if any(token in text for token in (
            "unexpected", "surprise", "beats estimates", "misses estimates",
            "優於預期", "低於預期", "意外", "突發", "緊急")):
        return 0.95
    if any(token in text for token in (
            "as expected", "in line with", "符合預期", "市場預期", "早已預期")):
        return 0.25
    return {
        "guidance_raise": 0.90, "guidance_cut": 0.90, "orders": 0.70,
        "earnings": 0.60, "revenue_growth": 0.50, "export_controls": 0.85,
        "litigation": 0.75, "geopolitical": 0.90, "general": 0.35,
    }.get(str(event.get("event_type")), 0.35)

def _event_lifecycle(event: dict) -> str:
    """Classify event progression so repeated coverage does not repeatedly add score."""
    explicit = str(event.get("lifecycle") or event.get("status") or "").lower()
    text = f"{explicit} {event.get('title', '')} {event.get('summary', '')}".lower()
    if any(token in text for token in (
            "withdrawn", "withdraw", "cancelled", "canceled", "撤回", "取消", "暫緩")):
        return "withdrawn"
    if any(token in text for token in (
            "implemented", "effective", "takes effect", "上路", "生效", "實施")):
        return "implemented"
    if any(token in text for token in (
            "confirmed", "announced", "approved", "公告", "核定", "通過", "證實")):
        return "confirmed"
    if any(token in text for token in (
            "rumor", "reportedly", "may", "considering", "傳聞", "擬", "可能", "研議")):
        return "rumor"
    return "confirmed" if event.get("source_grade") == "A" else "rumor"

def _event_timeline_key(event: dict) -> tuple[str, str]:
    """Use a stable lineage key across rumor, confirmation and implementation coverage."""
    entity = str(event.get("entity") or "").strip()
    event_type = str(event.get("event_type") or "general").strip() or "general"
    if not entity or event_type == "general":
        import hashlib
        cluster = "|".join(str(part) for part in _event_cluster_key(event))
        if not cluster.strip("|"):
            cluster = str(event.get("title") or event.get("summary") or "")
        digest = hashlib.sha1(cluster.encode("utf-8")).hexdigest()[:10]
        return entity or f"cluster:{digest}", event_type
    return entity, event_type


def apply_event_timeline(model_history: list[dict],
                         events: list[dict]) -> list[dict]:
    """Annotate incremental lifecycle transitions and suppress repeated event scoring."""
    previous: dict[tuple[str, str], str] = {}
    for record in sorted(model_history or [], key=lambda item: item.get("session_date", "")):
        for event in record.get("structured_events") or []:
            previous[_event_timeline_key(event)] = str(
                event.get("lifecycle") or _event_lifecycle(event))
    order = {"rumor": 1, "confirmed": 2, "implemented": 3, "withdrawn": 4}
    base_weight = {"rumor": 0.35, "confirmed": 1.0, "implemented": 0.55, "withdrawn": 1.0}
    transitions = {("rumor", "confirmed"): 0.65, ("confirmed", "implemented"): 0.45}
    output = []
    for raw in events or []:
        event = dict(raw)
        key = _event_timeline_key(event)
        status = _event_lifecycle(event)
        prior = previous.get(key)
        is_incremental = prior != status and (
            prior is None or status == "withdrawn"
            or order.get(status, 0) > order.get(prior, 0))
        event["lifecycle"] = status
        event["previous_lifecycle"] = prior
        event["timeline_key"] = "|".join(key)
        event["is_incremental"] = is_incremental
        event["lifecycle_weight"] = (
            transitions.get((prior, status), base_weight.get(status, 0.0))
            if is_incremental else 0.0
        )
        previous[key] = status if is_incremental or prior is None else prior
        output.append(event)
    return output

def _event_study_dedupe_key(row: dict, evidence: dict) -> tuple:
    event_type = str(evidence.get("event_type") or "")
    direction = int(_safe_number(evidence.get("direction")))
    code = str(row.get("code") or "")
    event_id = str(evidence.get("event_id") or "").strip()
    if event_id:
        return ("event_id", event_id, code, event_type, direction)
    timeline_key = str(evidence.get("timeline_key") or "").strip()
    if timeline_key:
        return ("timeline", timeline_key, code, event_type, direction)
    return (
        "fallback",
        str(row.get("session_date") or ""),
        code,
        event_type,
        direction,
        str(evidence.get("scope_company") or ""),
        str(evidence.get("scope_industry") or ""),
        str(evidence.get("scope_supply_chain") or ""),
        str(evidence.get("lifecycle") or ""),
        str(evidence.get("relation") or ""),
    )

def _shrunk_event_impact(event_study: dict[tuple, dict],
                         code: str,
                         industry: str,
                         supply_chain: str,
                         event_type: str,
                         direction: int) -> tuple[float, int, str]:
    """Shrink sparse company studies toward industry, supply-chain and global priors."""
    levels = [
        ("company", code, 10.0),
        ("industry", industry, 18.0),
        ("supply_chain", supply_chain, 18.0),
        ("global", "", 30.0),
    ]
    weighted, total_weight, samples, used = 0.0, 0.0, 0, []
    for scope, scope_id, prior_strength in levels:
        if scope != "global" and not scope_id:
            continue
        stats = event_study.get((scope, scope_id, event_type, direction)) or {}
        n = int(stats.get("samples", 0))
        if not n:
            continue
        weight = n / (n + prior_strength)
        weighted += _safe_number(stats.get("avg_excess_pct")) * weight
        total_weight += weight
        samples += n
        used.append(scope)
    if not total_weight:
        return 0.0, 0, "conservative_fallback"
    impact = max(-3.0, min(3.0, weighted / total_weight))
    return impact, samples, "hierarchical_event_study:" + "+".join(used)

_LLM_EVENT_TYPES = {"guidance_raise", "guidance_cut", "orders", "earnings",
                    "revenue_growth", "export_controls", "litigation", "geopolitical", "general"}

def _validate_llm_events(events: list) -> tuple[list, int]:
    """驗證 LLM 抽取事件 schema:event_type 屬允許集合、direction ∈ {-1,0,1}、entity 為字串或缺。
    回 (合格清單, 丟棄數)。不合格項丟棄(寧缺勿濫,避免髒事件污染下游計分/去重)。"""
    valid, dropped = [], 0
    for ev in events or []:
        if not isinstance(ev, dict):
            dropped += 1
            continue
        try:
            direction = int(ev.get("direction"))
        except (TypeError, ValueError):
            direction = None
        entity = ev.get("entity")
        if (str(ev.get("event_type") or "") in _LLM_EVENT_TYPES
                and direction in (-1, 0, 1)
                and isinstance(entity, (str, type(None)))):
            valid.append(ev)
        else:
            dropped += 1
    return valid, dropped
