"""Market completion must join event identity, tour, year and a final result."""
import copy

import pytest

from tennis_market_context import market_has_final, result_dates


def quotes():
    return [{"event_slug": "2026-womens-us-open-winner-tennis",
             "name": "Offline player", "prob": 100}]


def final():
    return {"event_key": "US Open", "tour": "WTA", "round": "Final",
            "winner": "Offline player", "loser": "Offline opponent",
            "played_at": "2026-09-12T20:00:00Z", "date": "09/13"}


def test_same_edition_final_matches_without_mutating_inputs():
    rows, results = quotes(), [final()]
    before = copy.deepcopy((rows, results))
    assert market_has_final(rows, results)
    assert (rows, results) == before


@pytest.mark.parametrize("change", [
    {"tour": "ATP"}, {"round": "Semifinal"}, {"round": "Qualifying Final"},
    {"event_key": "Wimbledon"}, {"event_key": "US Open Juniors"},
    {"event_key": "US Open Doubles"}, {"event_key": "2025 US Open"},
    {"played_at": "2025-09-12T20:00:00Z"}, {"played_at": "09/13"},
    {"played_at": "2026-09-12T20:00:00"}, {"played_at": None},
    {"played_at": "9999-12-31T23:59:59-12:00"},
    {"played_at": "0001-01-01T00:00:00+23:00"},
    {"winner": ""}, {"loser": None},
])
def test_nonmatching_or_insufficient_evidence_keeps_market(change):
    assert not market_has_final(quotes(), [{**final(), **change}])


@pytest.mark.parametrize("rows", [None, {}, [], [None], [{}],
    [{"event_slug": []}], [{"event_slug": "unknown"}],
    [{"event_slug": "2026-mens-us-open-winner-tennis"}],
    [{"event_slug": "2027-womens-us-open-winner-tennis"}],
])
def test_unknown_or_different_market_kept(rows):
    assert not market_has_final(rows, [final()])


def test_mixed_market_rows_do_not_silently_drop_another_market():
    assert not market_has_final(quotes() + [{"event_slug": "other"}], [final()])
    assert not market_has_final(quotes() + [{"event_slug": []}], [final()])


def test_rounded_hundred_percent_is_not_a_result():
    assert not market_has_final(quotes(), [])
    assert not market_has_final(quotes(), None)


def test_final_not_price_is_authority():
    assert market_has_final([{**quotes()[0], "prob": 75}], [final()])


def test_one_bad_result_does_not_hide_a_valid_final():
    assert market_has_final(quotes(), [None, {}, final()])


def test_result_date_retains_year_and_displays_taipei_day():
    assert result_dates("2026-09-12T20:00:00Z") == {
        "played_at": "2026-09-12T20:00:00Z", "date": "09/13"}


@pytest.mark.parametrize("timestamp", [None, "09/13", "2026-09-12T20:00:00",
                                      "9999-12-31T23:59:59-12:00"])
def test_ambiguous_or_invalid_source_date_is_not_inferred(timestamp):
    assert result_dates(timestamp)["date"] == ""
