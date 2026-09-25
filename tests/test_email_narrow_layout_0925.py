"""Offline narrow-reader markup: keep every value when CSS is unavailable."""

from bs4 import BeautifulSoup

import morning_report as mr
from test_golden_faults import _golden_quotes


def test_narrow_layout_targets_only_outer_cards_and_ma200(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Offline rendering must not fetch")

    monkeypatch.setattr(mr, "_http_get", no_network)
    monkeypatch.setattr(mr.requests, "get", no_network)
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    quotes = _golden_quotes()
    quotes["MA200_STATUS"] = {
        "00662.TW": {"name": "00662 富邦NASDAQ", "above": True,
                     "close": 124.25, "ma200": 111.52, "dist_pct": 11.4},
    }
    markup = mr.render_html(
        quotes,
        {"fair_price": 124.28, "last_00662_price": 124.25,
         "implied_change_pct": 0.02, "qqq_pct": 0.18},
        {"mid": 2482.29, "last_2330": 2475.0, "error": "fixture"},
        "## 分析\n離線測試正文", "2026-09-25", "每日報",
    )
    soup = BeautifulSoup(markup, "html.parser")
    assert len(soup.select("table.mail-container")) == 1
    assert len(soup.select("table.mail-kpi")) == 2
    assert len(soup.select("table.mail-ma200")) == 1
    assert "@media screen and (max-width:360px)" in markup
    assert "table-layout:fixed!important" in markup
    assert "124.28" in soup.select_one("table.mail-kpi").get_text()
    assert "站上(波段偏多) +11.4%" in soup.select_one("table.mail-ma200").get_text()

    for style in soup.find_all("style"):
        style.decompose()
    for node in soup.find_all(True):
        node.attrs.pop("class", None)
    assert "00662 富邦NASDAQ" in soup.get_text()
    assert "站上(波段偏多) +11.4%" in soup.get_text()
