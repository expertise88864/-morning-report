"""Oversized history input is bounded without changing full local history."""
from copy import deepcopy
from datetime import date, timedelta
import json

import payload_budget
from payload_history_context import MAX_HISTORY_CHARS, slice_history


def _row(day: str, *, generated_at: str | None = None, filler: int = 0) -> dict:
    return {
        "date": day,
        "generated_at": generated_at or f"{day}T07:00:00+08:00",
        "stance_label_py": "偏多",
        "stance_score_py": 0.3,
        "pred_0050": 100,
        "fair_00662": 120,
        "actual_open_0050": 101,
        "actual_open_00662": 119,
        "stance_components_py": {"breadth": 0.2},
        "breakout_candidates": [{
            "code": "2330", "name": "台積電", "attention_rank": 1,
            "ranking_score": 0.7, "close": 900,
            "price_forecast": {"training_rows": "private or bulky"},
        }],
        "tdcc_snapshot": "t" * filler,
        "critical_news": ["n" * filler],
    }


def _large_history(days: int = 15, filler: int = 8000) -> list[dict]:
    start = date(2026, 9, 1)
    return [_row((start + timedelta(days=i)).isoformat(), filler=filler)
            for i in range(days)]


def test_large_history_is_point_in_time_bounded_and_original_unchanged():
    rows = _large_history()
    rows += [_row("2026-09-10", filler=2000),
             _row("2026-09-16", filler=2000),
             _row("bad-date", filler=2000),
             _row("2026-09-08", generated_at="2026-09-25T07:00:00+08:00",
                  filler=2000)]
    packet = {"target_session_date": "2026-09-17",
              "as_of": "2026-09-17T06:00:00+08:00",
              "market": {"HISTORY": rows, "QQQ": 1.0},
              "required_disclosures": {"gap:existing": "保留"}}
    original = deepcopy(packet)
    manifest = {}
    out = slice_history(packet, manifest)
    history = out["market"]["HISTORY"]

    assert packet == original
    assert len(history) == 10
    assert [r["date"] for r in history] == [
        (date(2026, 9, 7) + timedelta(days=i)).isoformat()
        for i in range(10)]
    assert out["market"]["QQQ"] == 1.0
    assert out["required_disclosures"] == original["required_disclosures"]
    assert "history_input_scope" not in out
    assert all("critical_news" in r and "tdcc_snapshot" not in r
               for r in history)
    assert all((r["pred_0050"], r["actual_open_0050"],
                r["fair_00662"], r["actual_open_00662"])
               == (100, 101, 120, 119) for r in history)
    assert all(r["breakout_candidates"][0] == {
        "code": "2330", "name": "台積電", "attention_rank": 1,
        "ranking_score": 0.7, "close": 900,
    } for r in history)
    report = manifest["llm"]["history_context"]
    assert report["status"] == "applied"
    assert report["chars_saved"] > 100_000
    assert report["excluded_invalid_or_future"] == 1
    assert report["excluded_future_generated"] == 1
    assert report["excluded_duplicate_dates"] == 1


def test_small_safe_history_uses_same_allowlist_but_invalid_cutoff_omits_history():
    small = {"target_session_date": "2026-09-17",
             "market": {"HISTORY": [_row("2026-09-16")]}}
    small["market"]["HISTORY"][0]["stance_label_llm"] = "舊模型文字"
    manifest = {}
    projected = slice_history(small, manifest)
    assert projected is not small
    assert small["market"]["HISTORY"][0]["stance_label_llm"] == "舊模型文字"
    prior = projected["market"]["HISTORY"][0]
    assert prior["pred_0050"] == 100
    assert prior["actual_open_0050"] == 101
    assert prior["critical_news"]
    assert "stance_label_llm" not in prior
    assert "tdcc_snapshot" not in prior
    assert "price_forecast" not in prior["breakout_candidates"][0]
    assert manifest["llm"]["history_context"]["status"] == "applied"
    large = {"target_session_date": "invalid",
             "market": {"HISTORY": _large_history()}}
    manifest = {}
    assert "HISTORY" not in slice_history(large, manifest)["market"]
    assert "HISTORY" in large["market"]
    assert manifest["llm"]["history_context"]["status"] == "omitted_invalid_cutoff"


def test_small_history_cannot_bypass_point_in_time_filter_on_rerun():
    packet = {"target_session_date": "2026-09-17",
              "as_of": "2026-09-17T08:00:00+08:00",
              "market": {"HISTORY": [
                  _row("2026-09-16"), _row("2026-09-17"), _row("2026-09-18")]}}
    manifest = {}
    out = slice_history(packet, manifest)
    assert [row["date"] for row in out["market"]["HISTORY"]] == ["2026-09-16"]
    assert len(packet["market"]["HISTORY"]) == 3
    assert manifest["llm"]["history_context"]["status"] == "applied"


def test_all_future_history_is_omitted_even_when_under_the_size_limit():
    packet = {"target_session_date": "2026-09-17",
              "market": {"HISTORY": [_row("2026-09-17"), _row("2026-09-18")],
                         "QQQ": 1.0}}
    manifest = {}
    out = slice_history(packet, manifest)
    assert "HISTORY" not in out["market"]
    assert out["market"]["QQQ"] == 1.0
    assert len(packet["market"]["HISTORY"]) == 2
    assert manifest["llm"]["history_context"]["status"] == "omitted_no_prior_session"


def test_projection_drops_oldest_whole_sessions_to_obey_hard_limit():
    rows = _large_history(filler=20_000)
    packet = {"target_session_date": "2026-09-17",
              "as_of": "2026-09-17T06:00:00+08:00",
              "market": {"HISTORY": rows}}
    manifest = {}
    out = slice_history(packet, manifest)
    selected = out["market"]["HISTORY"]
    assert 1 < len(selected) < 10
    assert selected[-1]["date"] == "2026-09-15"
    assert len(json.dumps(selected, ensure_ascii=False, separators=(",", ":"))) <= MAX_HISTORY_CHARS
    assert manifest["llm"]["history_context"]["chars_after"] <= MAX_HISTORY_CHARS
    assert packet["market"]["HISTORY"] is rows


def test_single_oversize_session_omits_history_from_model_copy():
    packet = {"target_session_date": "2026-09-03",
              "market": {"HISTORY": _large_history(days=2, filler=150_000)}}
    manifest = {}
    assert "HISTORY" not in slice_history(packet, manifest)["market"]
    assert "HISTORY" in packet["market"]
    report = manifest["llm"]["history_context"]
    assert report["status"] == "omitted_oversize_projection"
    assert report["projected_chars"] > report["limit"] == MAX_HISTORY_CHARS


def test_no_safe_prior_session_is_not_sent_to_the_model():
    packet = {"target_session_date": "2026-09-01",
              "market": {"HISTORY": _large_history()}}
    manifest = {}
    assert "HISTORY" not in slice_history(packet, manifest)["market"]
    assert manifest["llm"]["history_context"]["status"] == "omitted_no_prior_session"


def test_later_generated_revision_cannot_overwrite_earlier_valid_day():
    rows = _large_history()
    rows.append(_row("2026-09-12", generated_at="2026-09-19T07:00:00+08:00",
                     filler=9000))
    packet = {"target_session_date": "2026-09-17",
              "as_of": "2026-09-17T06:00:00+08:00",
              "market": {"HISTORY": rows}}
    out = slice_history(packet)
    assert "2026-09-12" in [r["date"] for r in out["market"]["HISTORY"]]
    day_12 = next(r for r in out["market"]["HISTORY"]
                  if r["date"] == "2026-09-12")
    assert day_12["generated_at"] == "2026-09-12T07:00:00+08:00"


def test_future_target_cannot_include_reports_after_asof():
    packet = {"target_session_date": "2026-09-30",
              "as_of": "2026-09-12T06:00:00+08:00",
              "market": {"HISTORY": _large_history()}}
    manifest = {}
    out = slice_history(packet, manifest)
    assert out["market"]["HISTORY"][-1]["date"] == "2026-09-11"
    assert manifest["llm"]["history_context"]["exclusive_cutoff"] == "2026-09-12"


def test_weekend_report_targeting_today_is_not_prior_session_context():
    rows = _large_history(days=18)
    weekend = _row("2026-09-19", filler=8000)
    weekend["target_session_date"] = "2026-09-21"
    rows.append(weekend)
    packet = {"target_session_date": "2026-09-21",
              "as_of": "2026-09-21T06:00:00+08:00",
              "market": {"HISTORY": rows}}
    manifest = {}
    out = slice_history(packet, manifest)
    selected = out["market"]["HISTORY"]
    assert len(selected) == 10
    assert selected[-1]["date"] == "2026-09-18"
    assert all(row["date"] != "2026-09-19" for row in selected)
    assert manifest["llm"]["history_context"]["excluded_invalid_or_future"] == 1


def test_two_generated_reports_for_one_target_session_are_deduplicated():
    rows = _large_history(days=18)
    rows[-2]["target_session_date"] = "2026-09-18"
    rows[-1]["target_session_date"] = "2026-09-18"
    packet = {"target_session_date": "2026-09-21",
              "as_of": "2026-09-21T06:00:00+08:00",
              "market": {"HISTORY": rows}}
    manifest = {}
    out = slice_history(packet, manifest)
    selected = out["market"]["HISTORY"]
    assert sum(row.get("target_session_date") == "2026-09-18"
               for row in selected) == 1
    assert next(row for row in selected if row.get("target_session_date") == "2026-09-18")["date"] == "2026-09-18"
    assert manifest["llm"]["history_context"]["excluded_duplicate_dates"] == 1


def test_budget_application_keeps_news_and_structured_events_when_history_shrinks():
    rows = _large_history(filler=20_000)
    packet = {
        "target_session_date": "2026-09-17",
        "as_of": "2026-09-17T06:00:00+08:00",
        "market": {"HISTORY": rows,
                   "STRUCTURED_NEWS_EVENTS": [{"event_id": "e1", "text": "s" * 300_000}]},
        "news": [{"source_item_id": "n1", "summary": "新聞" * 500}],
        "tw_universe": [{"code": "2330", "name": "台積電", "evidence": "u" * 20_000}],
    }
    original = deepcopy(packet)
    manifest = {}
    out = payload_budget.apply(packet, manifest)
    assert packet == original
    assert 1 <= len(out["market"]["HISTORY"]) <= 10
    assert out["market"]["HISTORY"][-1]["date"] == "2026-09-15"
    assert out["market"]["STRUCTURED_NEWS_EVENTS"] == (
        packet["market"]["STRUCTURED_NEWS_EVENTS"])
    assert out["news"] == packet["news"]
    assert out["tw_universe"] == packet["tw_universe"]
    assert manifest["llm"]["payload_budget"]["trimmed"] == []
