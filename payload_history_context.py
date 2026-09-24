"""Bounded, point-in-time historical context for the analysis *input* only.

Full history remains in state for Python; only the oversized LLM copy changes.
"""
from __future__ import annotations

from datetime import date
import json

from payload_history_dates import day as _day
from payload_history_dates import generated_after_asof as _generated_after_asof
from payload_history_dates import timestamp as _timestamp


MAX_HISTORY_CHARS = 100_000
RECENT_SESSIONS = 10

# Allowlist observations and Python results, never model pipelines or future
# private state fields, in the prompt.
_SCALAR_FIELDS = (
    "date", "target_session_date", "generated_at", "stance_label_py",
    "stance_score_py", "stance_coverage_py", "earnings_proximity",
    "qqq_pct", "sox_pct", "spy_pct", "tsm_pct", "vix",
    "usdtwd", "usdtwd_stale", "night_txf_pct", "taifex_foreign_oi",
    "taifex_chip_source_date", "taifex_top10_net",
    "taifex_spec_top10_net", "txo_pc_oi_ratio", "txo_pcr_source_date",
    "taifex_top10_concentration_pct", "foreign_top10_total",
    "pred_taiex", "pred_0050", "weighted_final_2330", "fair_00662",
    "last_0050", "momentum_5d_pct_2330", "model1_2330", "model2_2330", "model3_2330",
    "model4_2330", "actual_open_taiex", "actual_open_2330",
    "actual_open_0050", "actual_open_00662",
)
_CANDIDATE_FIELDS = ("code", "name", "attention_rank", "ranking_score", "close")
def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          default=str))


def _projection(record: dict) -> dict:
    out = {key: record[key] for key in _SCALAR_FIELDS
           if key in record and not isinstance(record[key], (dict, list))}
    components = record.get("stance_components_py")
    if isinstance(components, dict):
        out["stance_components_py"] = {
            key: value for key, value in components.items()
            if isinstance(key, str) and isinstance(value, (int, float, str, bool))}
    critical_news = record.get("critical_news")
    if isinstance(critical_news, list):
        out["critical_news"] = [title for title in critical_news if isinstance(title, str)]
    missing = record.get("stance_missing_py")
    if isinstance(missing, list):
        out["stance_missing_py"] = [name for name in missing if isinstance(name, str)]
    candidates = record.get("breakout_candidates")
    if isinstance(candidates, list):
        out["breakout_candidates"] = [
            {key: row[key] for key in _CANDIDATE_FIELDS if key in row}
            for row in candidates if isinstance(row, dict)]
    return out


def slice_history(packet: dict, manifest: dict | None = None, *,
                  max_chars: int = MAX_HISTORY_CHARS,
                  recent_sessions: int = RECENT_SESSIONS) -> dict:
    """Project large history without mutating the original packet or state.

    Keep the latest distinct sessions strictly before the target session.
    Without a valid cutoff, omit history from the model-only copy rather than
    risk including future records. Scope and exclusions are
    recorded in the manifest.  A bounded history window is not a missing
    current-day source or required data gap.
    """
    market = packet.get("market") if isinstance(packet, dict) else None
    history = market.get("HISTORY") if isinstance(market, dict) else None
    if not isinstance(history, list):
        return packet
    before = _size(history)

    def without_history(status: str, **details) -> dict:
        out = dict(packet)
        out["market"] = {key: value for key, value in market.items()
                         if key != "HISTORY"}
        if manifest is not None:
            manifest.setdefault("llm", {})["history_context"] = {
                "status": status, "chars_before": before, **details}
        return out

    cutoff = _day(packet.get("target_session_date"))
    if cutoff is None:
        cutoff = _day(str(packet.get("as_of") or "")[:10])
    if cutoff is None:
        return without_history("omitted_invalid_cutoff")
    as_of = _timestamp(packet.get("as_of"))
    safe_cutoff = min(cutoff, as_of.date()) if as_of is not None else cutoff
    by_day: dict[date, dict] = {}
    invalid_or_future = 0
    future_generated = 0
    for row in history:
        if isinstance(row, dict):
            report_day = _day(row.get("date"))
            session = _day(row.get("target_session_date")) or report_day
        else:
            report_day = session = None
        if (report_day is None or report_day >= safe_cutoff
                or session is None or session >= safe_cutoff):
            invalid_or_future += 1
            continue
        if _generated_after_asof(row.get("generated_at"), as_of, safe_cutoff):
            future_generated += 1
            continue
        by_day[session] = row  # Later revisions for one session supersede earlier ones.
    if not by_day:
        return without_history(
            "omitted_no_prior_session",
            excluded_invalid_or_future=invalid_or_future,
            excluded_future_generated=future_generated)
    selected = sorted(by_day)[-max(1, recent_sessions):]
    projected = [_projection(by_day[day]) for day in selected]
    while len(projected) > 1 and _size(projected) > max_chars:
        selected, projected = selected[1:], projected[1:]
    after = _size(projected)
    if after > max_chars:
        return without_history("omitted_oversize_projection",
                               projected_chars=after, limit=max_chars)
    out = dict(packet)
    out["market"] = dict(market, HISTORY=projected)
    report = {"status": "applied", "chars_before": before, "chars_after": after,
              "chars_saved": before - after, "source_rows": len(history),
              "evidence_role": "historical_context_not_current_fact",
              "exclusive_cutoff": safe_cutoff.isoformat(),
              "included_sessions": len(projected), "first_date": selected[0].isoformat(),
              "last_date": selected[-1].isoformat(),
              "excluded_invalid_or_future": invalid_or_future,
              "excluded_future_generated": future_generated,
              "excluded_duplicate_dates": len(history) - invalid_or_future
              - future_generated - len(by_day)}
    if manifest is not None:
        manifest.setdefault("llm", {})["history_context"] = report
    return out
