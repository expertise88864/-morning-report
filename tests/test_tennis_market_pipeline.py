"""Offline regression for the actual producer -> renderer identity wiring."""
import datetime as dt
import html

import pytest

import morning_report as mr
from render_utils import _render_sports_html


@pytest.fixture
def fetched(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline fixture attempted network access")

    monkeypatch.setattr(mr.requests, "get", no_network)
    monkeypatch.setattr(mr.requests, "post", no_network)
    monkeypatch.setattr(mr, "_poly_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(mr, "_poly_annotate_deltas", lambda key, rows, now: rows)

    def outright(slug, *args, **kwargs):
        if slug == "2026-womens-us-open-winner-tennis":
            return [{"name": "Offline woman", "prob": 100}]
        if slug == "2026-mens-us-open-winner-tennis":
            return [{"name": "Offline man", "prob": 58}]
        return []

    monkeypatch.setattr(mr, "_poly_outright", outright)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [{"shortName": "US Open",
                "status": {"type": {"state": "post"}},
                "groupings": [{"grouping": {"slug": "womens-singles"},
                    "competitions": [{"id": "offline-final",
                        "date": "2026-09-12T20:00:00Z",
                        "round": {"displayName": "Final"},
                        "status": {"type": {"completed": True}},
                        "competitors": [
                            {"athlete": {"shortName": "Offline woman"}, "winner": True},
                            {"athlete": {"shortName": "Offline opponent"}, "winner": False}
                        ]}]}]}]}

    monkeypatch.setattr(mr, "_http_get", lambda *args, **kwargs: Response())
    now = dt.datetime(2026, 9, 13, 7, tzinfo=mr.TPE)
    return {"tennis": mr.fetch_tennis_digest(now),
            "poly": mr.fetch_polymarket_sports(now)}


def test_quote_producer_preserves_selected_event_identity(fetched):
    for key, gender in (("tennis_m", "mens"), ("tennis_w", "womens")):
        assert fetched["poly"][key][0].get("event_slug") == (
            f"2026-{gender}-us-open-winner-tennis")


def test_score_producer_preserves_full_source_date(fetched):
    result = fetched["tennis"]["results"][0]
    assert result["date"] == "09/13"  # existing reader-facing Taipei date retained
    assert result.get("played_at") == "2026-09-12T20:00:00Z"


def test_finished_womens_market_is_not_a_prediction_but_mens_remains(fetched):
    rendered = _render_sports_html(fetched, html)
    assert "WTA 冠軍:" in rendered
    assert "Offline woman" in rendered
    assert "男:" in rendered and "58%" in rendered
    assert "女:" not in rendered
    assert "100%" not in rendered
