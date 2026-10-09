"""Offline contracts: exact office identity, honest missingness, no placeholder odds."""
import datetime as dt
from copy import deepcopy

import pytest

import polymarket_local_elections as pe

NOW = dt.datetime(2026, 10, 9, 6, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def market(name="Real Candidate", probability="0.7", **extra):
    return {"groupItemTitle": name, "outcomes": ["Yes", "No"],
            "outcomePrices": [probability, "0.3"], "closed": False,
            "active": True, "archived": False, "volume24hr": 20000, "spread": 0.01, **extra}


def event(index=0, **extra):
    _, office, slug = pe.MARKETS[index]
    return {"slug": slug or f"test-{index}", "title": f"{office} Election Winner",
            "description": "Taiwanese local elections November 28, 2026.",
            "endDate": "2026-11-29T04:59:00Z", "closed": False, "active": True,
            "archived": False, "markets": [market()], **extra}


def test_six_verified_markets_and_two_missing_cities_omitted():
    calls = []
    data = {s: event(i) for i, (_, _, s) in enumerate(pe.MARKETS) if s}

    def fetch(params):
        calls.append(params)
        return [data[params["slug"]]]

    def search(query):
        calls.append(query)
        return [event(3)]  # County is not the requested city, even on fuzzy search.

    rows = pe.fetch_rows(fetch, search, NOW)
    assert len(rows) == 6 and len(calls) == 8
    assert all("70.0%" in r["detail"] for r in rows)
    assert [r["label"] for r in rows] == [f"2026 {label}" for label, _, slug in pe.MARKETS if slug]
    assert all(r["kind"] == "tw_local_election" for r in rows)


@pytest.mark.parametrize("change", [
    {"closed": True}, {"active": False}, {"archived": True},
    {"endDate": "2022-11-29T00:00:00Z"}, {"endDate": "2030-11-29T00:00:00Z"},
    {"endDate": "2026-10-08T22:00:00Z"}, {"endDate": "bad"},
    {"endDate": "2026-11-29"}, {"description": "2022 election"},
    {"title": "New Taipei Mayor Election Winner"}, {"slug": "other-event"},
])
def test_known_market_identity_and_lifecycle_are_fail_closed(change, capsys):
    rows = pe.fetch_rows(lambda _: [event(**change)], lambda _: [], NOW)
    assert "2026 台北市長" not in [row["label"] for row in rows]
    assert "::warning::" in capsys.readouterr().err


def test_placeholders_and_closed_children_do_not_displace_genuine_fifty_percent():
    markets = [market(f"Candidate {letter}", "0.5") for letter in "ABCD"]
    markets += [market("Other", "0.8"), market("Closed", closed=True),
                market("Inactive", active=False), market("Archived", archived=True),
                market("Actual Name", "0.5"), market("Puma Shen", "0.3")]
    prices = pe._prices(event(markets=markets))
    assert prices == [(0.5, "Actual Name 50.0%"), (0.3, "沈伯洋 30.0%")]


@pytest.mark.parametrize("prices", [["nan", "0.2"], ["inf", "0.2"], ["-0.1", "1.1"],
                                    ["0.5"], [True, False], "broken-json", None])
def test_malformed_prices_never_become_probabilities(prices):
    assert pe._prices(event(markets=[market(outcomePrices=prices)])) == []


def test_reversed_yes_no_order_quality_and_real_low_probability():
    prices = pe._prices(event(markets=[market(
        outcomes='["No", "Yes"]', outcomePrices='["0.99", "0.01"]',
        volume24hr=10, spread=0.1)]))
    assert prices == [(0.01, "Real Candidate 1.0%（量低、價差大）")]
    assert "量未知、價差未知" in pe._prices(event(markets=[market(volume24hr=None, spread=None)]))[0][1]


@pytest.mark.parametrize("end", ["2026-10-08T00:00:00Z", "bad", "2026-11-29"])
def test_expired_or_invalid_child_market_is_not_current_price(end):
    assert pe._prices(event(markets=[market(endDate=end)]), NOW) == []


def test_search_discovers_city_only_and_rejects_ambiguous_or_invalid_data(capsys):
    rows = pe.fetch_rows(lambda _: [], lambda _: [event(4)], NOW)
    assert [row["label"] for row in rows] == ["2026 彰化市長"]
    rows = pe.fetch_rows(lambda _: [], lambda _: [event(4), event(4)], NOW)
    assert rows == []
    rows = pe.fetch_rows(lambda _: {}, lambda _: {}, NOW)
    assert rows == []
    assert "ValueError" in capsys.readouterr().err


def test_one_fetch_failure_preserves_other_markets(capsys):
    def fetch(params):
        if params["slug"] == pe.MARKETS[0][2]:
            raise TimeoutError("failure")
        return [event(i) for i, (_, _, slug) in enumerate(pe.MARKETS) if params["slug"] == slug]

    rows = pe.fetch_rows(fetch, lambda _: [], NOW)
    assert len(rows) == 5 and rows[0]["label"] == "2026 新北市長"
    assert "TimeoutError" in capsys.readouterr().err


def test_expired_election_does_not_fetch_or_reuse_old_prices():
    def forbidden(*args):
        pytest.fail("must not query expired election")

    rows = pe.fetch_rows(forbidden, forbidden, NOW.replace(month=12))
    assert rows == []


def test_render_escape_source_allowlist_and_market_price_legend():
    rows = pe.fetch_rows(lambda _: [event()], lambda _: [], NOW)
    rows[0]["detail"] = "<script>alert(1)</script>"
    rows += [{"label": "bad", "detail": "bad"}, {"label": "bad2", "detail": "bad2"}]
    rows[1]["source_url"] = 'javascript:alert(1)'
    rows[2]["source_url"] = 'https://polymarket.com/event/x" onclick="bad'
    out = pe.render_pulse(rows, "<b>divergence</b>")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "javascript:" not in out and "onclick" not in out
    assert out.count('<a href="https://polymarket.com/event/') == 1
    assert "非民調" in out and "不強制加總 100%" in out and "10/09 06:00 台北" in out
    assert pe.render_pulse([]) == ""


def test_pipeline_uses_shared_guard_without_new_state_or_prediction_fields(monkeypatch):
    import morning_report as mr

    calls = []

    def forbidden(*args, **kwargs):
        calls.append(kwargs)
        raise TimeoutError("offline")

    before = deepcopy(mr._POLY_GUARD)
    monkeypatch.setattr(mr, "_POLY_GUARD", {"spent": 0.0, "consecutive_failures": 0, "tripped": False})
    monkeypatch.setattr(mr, "_http_get", forbidden)
    rows = mr.fetch_polymarket_pulse(NOW)
    assert len(calls) == 2  # Remaining election calls share the already-tripped guard.
    assert rows == []
    assert mr._render_poly_pulse_html(rows) == ""
    assert before is not mr._POLY_GUARD
