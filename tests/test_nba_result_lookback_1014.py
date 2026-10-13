"""Offline regressions for result lookback versus future NBA schedules."""
import pytest

import morning_report as mr
import nba_scoreboard as nba
from test_brief_layout_1012 import NOW, event


def test_results_skip_pre_day_and_find_previous_completed_game(monkeypatch):
    calls = []
    finished = event("post", id="finished", date="2026-10-10T01:00Z")
    teams = finished["competitions"][0]["competitors"]
    teams[0]["score"], teams[1]["score"] = "101", "99"
    scheduled = event()

    class Response:
        def __init__(self, events):
            self.events = events

        def raise_for_status(self):
            pass

        def json(self):
            return {"events": self.events}

    def blocked(*args, **kwargs):
        raise mr.requests.exceptions.ConnectionError("offline fixture")

    def get(url, params=None, timeout=None, **kwargs):
        if url != nba.URL:
            return blocked()
        day = params["dates"]
        calls.append((day, timeout))
        if timeout == 15:
            assert day in {"20261011", "20261010"}
            return Response([scheduled] if day == "20261011" else [finished])
        assert timeout == 10
        return Response([scheduled])

    monkeypatch.setattr(mr, "_http_get", get)
    monkeypatch.setattr(mr.requests, "get", blocked)
    monkeypatch.setattr(mr.requests, "post", blocked)
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout", blocked)
    monkeypatch.delenv("NBA_FAVORITE_TEAMS", raising=False)
    digest = mr.fetch_sports_digest(NOW)
    assert [day for day, timeout in calls if timeout == 15] == ["20261011", "20261010"]
    assert len(digest["nba"]) == 1
    assert "101:99" in digest["nba"][0]["text"]
    assert "0:0" not in digest["nba"][0]["text"]
    assert "已完賽" in digest["nba"][0]["note"]
    assert len([1 for _, timeout in calls if timeout == 10]) == 8
    assert len(digest["nba_fixtures"]) == 1
    assert digest["nba_fixtures"][0]["when"] == "10/12 09:00"


@pytest.mark.parametrize("state,completed,accepted", [
    ("pre", False, False), ("in", False, True), ("post", False, True),
    (None, True, True), (None, False, False),
])
def test_result_status_eligibility_and_score_display(state, completed, accepted):
    game = event(state)
    game["status"]["type"]["completed"] = completed
    row = nba.result_row(game, "10/11")
    assert (row is not None) is accepted
    if accepted:
        assert "0:0" in row["text"]


def test_result_status_precedence_and_competition_fallback():
    game = event("pre")
    game["competitions"][0]["status"] = {"type": {"state": "post", "completed": True}}
    assert nba.result_row(game, "10/11") is None
    del game["status"]
    assert nba.result_row(game, "10/11") is not None


@pytest.mark.parametrize("name", ["STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_DELAYED", "STATUS_SUSPENDED"])
def test_exception_status_does_not_become_result(name):
    game = event("post")
    game["status"]["type"]["name"] = name
    assert nba.result_row(game, "10/11") is None
