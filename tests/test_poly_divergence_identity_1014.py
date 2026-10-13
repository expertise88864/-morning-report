"""Actual binary producer and history projection must reach the display warning."""
import copy
import datetime as dt

import pytest

import morning_report as mr
from polymarket_pulse_history import prepare


URL = "https://polymarket.com/event/another-fed-rate-hike-in-2026"


@pytest.mark.parametrize("probability,stance,expected", [
    (.62, "偏多", True), (.08, "偏空", True),
    (.55, "偏多", True), (.15, "偏空", True),
    (.5499, "偏多", False), (.1501, "偏空", False),
    (.625, "偏多", True), (.085, "偏空", True),
    (.62, "中性", False), (.62, "偏空", False), (.08, "偏多", False),
])
def test_producer_history_and_divergence_use_exact_contract(monkeypatch, tmp_path, probability, stance, expected):
    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def get(url, params, **kwargs):
        if url.endswith("/public-search"):
            return Response({"events": []})
        assert url.endswith("/events")
        if params.get("slug") != "another-fed-rate-hike-in-2026":
            return Response([])
        return Response([{"endDate": "2026-12-10T04:59:00Z", "closed": False,
            "markets": [{"id": "fed-binary", "outcomes": '["Yes", "No"]',
                "outcomePrices": mr.json.dumps([probability, 1 - probability]),
                "volume24hr": 1, "spread": .2, "bestBid": .4, "bestAsk": .6}]}])

    monkeypatch.setattr(mr, "_http_get", get)
    now = dt.datetime(2026, 10, 14, 6, tzinfo=mr.TPE)
    produced = mr.fetch_polymarket_pulse(now)
    assert len(produced) == 1 and produced[0]["source_url"] == URL
    assert produced[0]["label"] == "2026年12月決議完成前 Fed 再升息"
    rows = prepare(produced, now, tmp_path / "history.json", mr._atomic_write_text)
    stance_data = {"label": stance, "total": 5}
    before_rows, before_stance = copy.deepcopy(rows), copy.deepcopy(stance_data)
    note = mr._poly_divergence_note(rows, stance_data)
    assert bool(note) is expected
    if expected:
        assert "2026年12月決議完成前" in note and "年內" not in note
    assert rows == before_rows and stance_data == before_stance
    rows[0]["label"] = "renamed display label"
    assert mr._poly_divergence_note(rows, stance_data) == note
    rendered = mr._render_poly_pulse_html(rows, stance_data)
    assert ("分歧提示" in rendered) is expected
    assert "量低" not in rendered and "價差寬" not in rendered and "(買" not in rendered


@pytest.mark.parametrize("url", [
    "", "https://polymarket.com/event/fed-rate-hike-in-2026",
    "https://polymarket.com/event/fed-decision-in-december-20260729232808632",
    "https://polymarket.com/event/how-many-fed-rate-hikes-in-2026-20260623190717369",
    URL + "-other", URL.replace("polymarket.com", "example.com"),
])
@pytest.mark.parametrize("label,detail", [("偏多", "機率 62%"), ("偏空", "機率 8%")])
def test_unrelated_or_missing_contract_identity_never_triggers(url, label, detail):
    rows = [{"label": "2026年12月決議完成前 Fed 再升息", "source_url": url, "detail": detail}]
    assert mr._poly_divergence_note(rows, {"label": label}) == ""


@pytest.mark.parametrize("value,stance,expected", [
    (52.35, "偏多", False), (55.01, "偏多", True), (54.999, "偏多", False),
    (14.99, "偏空", True), (15.001, "偏空", False),
])
def test_structured_probability_overrides_conflicting_rounded_text(value, stance, expected):
    row = {"source_url": URL, "detail": "機率 99%" if stance == "偏多" else "機率 0%",
           "outcomes": [{"name": "機率", "prob_raw": value, "prob": round(value)}]}
    assert bool(mr._poly_divergence_note([row], {"label": stance})) is expected


@pytest.mark.parametrize("outcomes", [
    None, [], [{}, {}], [None], [{"prob_raw": True}], [{"prob_raw": "62.5"}],
    [{"prob_raw": float("nan")}], [{"prob_raw": float("inf")}],
    [{"prob_raw": -1}], [{"prob_raw": 101}], [{"prob_raw": None, "prob": 62}],
])
def test_invalid_structured_data_never_falls_back_to_text(outcomes):
    row = {"source_url": URL, "detail": "機率 62%", "outcomes": outcomes}
    assert mr._poly_divergence_note([row], {"label": "偏多"}) == ""


@pytest.mark.parametrize("detail,stance,expected", [
    ("機率 52.35%", "偏多", False), ("機率 55.01%(↑0.01pp)", "偏多", True),
    ("機率 54.99%", "偏多", False), ("機率 14.99%", "偏空", True),
    ("機率 15.01%", "偏空", False), ("機率 101%", "偏多", False),
    ("機率 NaN%", "偏空", False), ("機率 Infinity%", "偏多", False),
    ("機率 " + "9" * 400 + "%", "偏多", False),
])
def test_legacy_text_requires_finite_in_range_decimal(detail, stance, expected):
    assert bool(mr._poly_divergence_note([{"source_url": URL, "detail": detail}],
                                       {"label": stance})) is expected


def test_structured_probability_accepts_prob_fallback():
    row = {"source_url": URL, "detail": "", "outcomes": [{"name": "機率", "prob": 62.5}]}
    assert "62.5%" in mr._poly_divergence_note([row], {"label": "偏多"})
