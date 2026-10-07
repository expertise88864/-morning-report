"""Offline target-session regression against the published 2026 TWSE calendar."""

import datetime as dt

from session_calendar import (
    _infer_target_session_date,
    _next_tw_weekday,
    evaluate_breakout_forecasts,
)


def test_moon_festival_teacher_day_long_weekend_targets_tuesday():
    for report_day in ("2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28"):
        assert _infer_target_session_date(report_day) == "2026-09-29"
    assert _infer_target_session_date("2026-09-29") == "2026-09-29"


def test_other_verified_2026_closures_are_not_predicted_as_open():
    assert _next_tw_weekday(dt.date(2026, 2, 12)) == dt.date(2026, 2, 23)
    assert _next_tw_weekday(dt.date(2026, 10, 9)) == dt.date(2026, 10, 12)
    assert _next_tw_weekday(dt.date(2026, 10, 26)) == dt.date(2026, 10, 27)


def test_future_target_fallback_does_not_count_known_closures_as_sessions():
    """A live session list cannot yet contain a future target trading date."""
    history = [{
        "date": "2026-09-24", "target_session_date": "2026-09-24",
        "breakout_candidates": [{
            "code": "2330", "close": 100,
            "price_forecast": {
                "3d": {"expected_price": 110},
                "5d": {"expected_price": 120},
            },
        }],
    }]
    result = evaluate_breakout_forecasts(
        history, [{"code": "2330", "close": 105}], "2026-10-01",
        sessions=["2026-09-23", "2026-09-24"],
    )
    assert result[3]["samples"] == 1
    assert result[5]["samples"] == 0
