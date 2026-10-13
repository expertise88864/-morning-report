import datetime as dt
import json

import morning_report as mr
from polymarket_pulse_history import prepare


def test_rank_and_threshold_changes_keep_actual_baselines(monkeypatch, tmp_path):
    probabilities = [50, 30, 12, 8, 1]
    def events(params):
        return [{"markets": [{"id": str(i), "groupItemTitle": chr(65 + i),
                              "outcomes": ["Yes", "No"],
                              "outcomePrices": json.dumps([p / 100, 1 - p / 100])}
                             for i, p in enumerate(probabilities)]}]
    monkeypatch.setattr(mr, "_poly_events", events)
    path = tmp_path / "history.json"
    monday = dt.datetime(2026, 10, 12, 6, tzinfo=mr.TPE)
    def snapshot(now):
        complete = []
        shown = mr._poly_outright("example", top=3, min_prob=.03, capture=complete)
        assert len(complete) == 5
        return prepare([{"source_url": "https://polymarket.com/event/example",
                         "outcomes": complete, "display_limit": len(shown)}],
                       now, path, mr._atomic_write_text)[0]
    assert "D " not in snapshot(monday)["detail"]
    snapshot(monday + dt.timedelta(days=5))
    probabilities[:] = [50, 30, 12, 13, 1]
    sunday = snapshot(monday + dt.timedelta(days=6))
    assert "D 13%(↑5pp)" in sunday["detail"]
    assert "D 13%(↑5pp)" in sunday["weekly_detail"]
    probabilities[:] = [50, 30, 12, 13, 15]
    assert "E 15%(↑14pp)" in snapshot(monday + dt.timedelta(days=7))["detail"]
