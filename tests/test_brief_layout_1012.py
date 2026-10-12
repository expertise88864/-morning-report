"""Offline regressions for the October 12 reader requests."""
import datetime as dt
import html

import pytest

import morning_report as mr
import nba_scoreboard as nba
import polymarket_local_elections as elections
from reader_prose import clean_text

NOW = dt.datetime(2026, 10, 12, 8, tzinfo=nba.TPE)


def event(state="pre", **changes):
    return {"id": "1", "date": "2026-10-12T01:00Z", "shortName": "CHI @ DEN",
            "season": {"type": 1}, "status": {"type": {"state": state, "completed": state == "post"},
            "period": 2, "displayClock": "03:12"}, "competitions": [{"competitors": [
                {"homeAway": "away", "score": "0", "team": {"abbreviation": "CHI"}},
                {"homeAway": "home", "score": "0", "team": {"abbreviation": "DEN"}}]}], **changes}


def test_reader_language_keeps_analysis_and_urls():
    url = "https://example.com/Python模型"
    assert clean_text(f"Python 模型估計2530。把表合起來讀：法人淨賣超。[來源]({url})") == (
        f"模型估計2530。法人淨賣超。[來源]({url})")


@pytest.mark.parametrize("state,label", [("pre", "未開賽"), ("in", "進行中"), ("post", "已完賽")])
def test_nba_status_and_taipei_time(state, label):
    row = nba.game_row(event(state), "10/11")
    assert row["date"] == "10/12 09:00"
    assert label in row["note"] and "季前賽" in row["note"]
    assert ("0:0" in row["text"]) is (state != "pre")
    rendered = mr._render_sports_html({"nba": [row]}, html)
    assert "NBA 最新賽況" in rendered and "NBA 冠軍賽" not in rendered
    if state == "in":
        assert "第 2 節 03:12" in rendered


def test_nba_escapes_source_names_and_scores():
    game = event("in")
    game["competitions"][0]["competitors"][0].update(score="<img>", team={"abbreviation": "<script>"})
    row = nba.game_row(game, "10/11")
    assert "<script>" not in row["text"] and "&lt;img&gt;" in row["text"]


def test_nba_daily_schedule_bounded_deduplicated_and_future_only(monkeypatch):
    dates = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"events": [event(), event(id="past", date="2026-10-11T01:00Z"),
                    event(id="late", date="2026-10-20T01:00Z"), event("post", id="done")]}
    def get(url, params, timeout):
        assert url == nba.URL and timeout <= 10
        dates.append(params["dates"])
        assert "-" not in params["dates"]
        return Response()
    monkeypatch.setattr(mr, "_http_get", get)
    monkeypatch.delenv("NBA_FAVORITE_TEAMS", raising=False)
    rows = mr.fetch_nba_week_fixtures(NOW)
    assert len(dates) == 8 and dates[0] == "20261011" and dates[-1] == "20261018"
    assert len(rows) == 1 and rows[0]["when"] == "10/12 09:00"


def test_nba_http_failure_is_not_an_empty_schedule():
    def get(*args, **kwargs):
        raise TimeoutError("offline")
    with pytest.raises(TimeoutError):
        nba.week_events(get, NOW)


def test_election_top_three_chinese_names_without_quality_noise():
    def market(name, p):
        return {"active": True, "closed": False, "groupItemTitle": name,
                "outcomes": ["Yes", "No"], "outcomePrices": [p, 1-p]}
    data = {"title": "Kaohsiung Mayor Election Winner", "slug": elections.MARKETS[-1][2],
            "active": True, "closed": False, "description": "2026 election",
            "endDate": "2026-11-29T00:00Z", "markets": [market("Hsu Chih-chieh", .02),
            market("Ko Chih-en", .3), market("Lai Jui-lung", .7), market("Other Person", .001)]}
    rows = elections.fetch_rows(lambda _: [data], lambda _: [], NOW)
    assert rows[0]["detail"] == "賴瑞隆 70.0%・柯志恩 30.0%・許智傑 2.0%"


def test_us_quotes_and_tw_forecasts_share_one_section(monkeypatch):
    from bs4 import BeautifulSoup
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    quotes = {x: {"ticker": x, "close": 100, "change_pct": 1} for x in ("QQQ", "TSM", "SPY")}
    output = mr.render_html(quotes, {"error": "missing"}, {"error": "missing"},
                            "## 我的明確立場\nPython 模型中性。把表合起來讀：維持觀察。", "2026-10-12", "每日報")
    soup = BeautifulSoup(output, "html.parser")
    assert "一、美股收盤行情" not in output
    heading = next(h for h in soup.find_all("h2") if "美股收盤行情" in h.get_text())
    tables = heading.find_all_next("table", limit=2)
    assert all(symbol in tables[0].get_text() for symbol in ("QQQ", "TSM", "SPY"))
    assert all(symbol in tables[1].get_text() for symbol in ("2330", "0050", "00662"))
    assert "Python 模型" not in soup.get_text() and "把表合起來讀" not in soup.get_text()
