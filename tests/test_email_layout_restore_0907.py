"""Regression: compact type and original tables survive missing email CSS."""
import html
from pathlib import Path

from bs4 import BeautifulSoup
import yaml

import email_mobile
import morning_report as mr
import render_utils as render
from test_golden_faults import _golden_quotes
from test_mobile_compact_0906 import standings


def without_stylesheets(markup):
    soup = BeautifulSoup(markup, "html.parser")
    for style in soup.find_all("style"):
        style.decompose()
    for node in soup.find_all(True):
        node.attrs.pop("class", None)
    return soup


def test_compacted_paragraphs_keep_small_inline_type_without_css():
    content = render._style_analysis_html("<p>新聞分析</p>" * 20)
    markup = render.compact_inline_styles("<html><head></head><body>" + content + "</body></html>")
    soup = without_stylesheets(email_mobile.enhance(markup))
    assert len(soup.find_all("p")) == 20
    assert all("font-size:14px" in p["style"] and "line-height:1.7" in p["style"]
               for p in soup.find_all("p"))


def test_both_cpbl_tables_keep_small_type_and_alignment_without_css():
    raw = render._render_sports_html(standings(), html)
    markup = render.compact_inline_styles("<html><head></head><body>" + raw + "</body></html>")
    soup = without_stylesheets(email_mobile.enhance(markup))
    tables = soup.select('table[data-mobile-layout="table"]')
    assert len(tables) == 2
    for table in tables:
        assert [x.text for x in table.find_all("th")] == ["排名", "勝-和-敗", "勝率", "勝差"]
        rows = table.find_all("tr")[1:]
        assert len(rows) == 6
        for row in rows:
            cells = row.find_all("td")
            assert len(cells) == 4
            assert all("font-size:13px" in c["style"] for c in cells)
            assert all("text-align:right" in c["style"] for c in cells[1:])


def test_final_price_table_is_not_mobile_cards(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Render must not fetch")
    monkeypatch.setattr(mr, "_http_get", no_network)
    monkeypatch.setattr(mr.requests, "get", no_network)
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    quotes = _golden_quotes()
    markup = mr.render_html(quotes, {"fair_price": 120.54, "last_00662_price": 120.55,
                                   "implied_change_pct": -0.01, "qqq_pct": 0.18},
                            {"mid": 2440.44, "last_2330": 2410.0, "error": "fixture"},
                            "## 分析\n測試正文", "2026-09-07", "每日報")
    soup = BeautifulSoup(markup, "html.parser")
    header = next(x for x in soup.find_all("th") if x.text == "預測開盤／公允價")
    table = header.find_parent("table")
    assert table["data-mobile-layout"] == "table"
    assert "mail-stack" not in table.get("class", [])
    assert not table.select(".mail-label")
    assert len(table.find_all("tr")) == 4
    assert all(len(row.find_all(["td", "th"])) == 4 for row in table.find_all("tr"))
    assert "120.54" in table.text and "2440.44" in table.text
    assert soup.select('table[role="presentation"].mail-reading')


def test_dependency_canary_installs_shared_development_requirements():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/deps-canary.yml").read_text(encoding="utf-8"))
    install = next(s["run"] for s in workflow["jobs"]["canary"]["steps"]
                   if s.get("name") == "Install latest allowed deps")
    commands = "\n".join(s for s in install.splitlines() if not s.strip().startswith("#"))
    assert "pip install -r requirements-dev.txt" in commands
    dev = (root / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in dev and "mypy==" in dev
