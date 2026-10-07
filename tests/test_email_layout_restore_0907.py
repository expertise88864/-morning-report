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


def test_style_dictionary_does_not_repeat_inline_type_in_head():
    style = ("font-size:13px;text-align:right;line-height:1.4;"
             "background:#fef3c7;border:1px solid #e5e7eb;")
    original = ("<html><head></head><body>" +
                "".join(f'<p style="{style}">第{i}列</p>' for i in range(8)) +
                "</body></html>")
    compact = render.compact_inline_styles(original)
    soup = BeautifulSoup(compact, "html.parser")
    sheet = soup.style.string
    assert sheet and ".s0{" in sheet
    assert "background:#fef3c7" in sheet and "border:1px solid #e5e7eb" in sheet
    assert all(prop not in sheet for prop in ("font-size", "text-align", "line-height"))
    assert [p.text for p in soup.find_all("p")] == [f"第{i}列" for i in range(8)]
    assert all(all(prop in p["style"] for prop in
                   ("font-size:13px", "text-align:right", "line-height:1.4"))
               for p in without_stylesheets(compact).find_all("p"))
    assert len(compact.encode("utf-8")) < len(original.encode("utf-8"))


def test_style_dictionary_leaves_css_string_semicolons_intact():
    style = ("font-size:13px;text-align:right;line-height:1.4;"
             "content:'a; b';background:#fef3c7;border:1px solid #e5e7eb;")
    markup = ("<html><head></head><body>" +
              "".join(f'<p style="{style}">文字{i}</p>' for i in range(8)) +
              "</body></html>")
    compact = render.compact_inline_styles(markup)
    soup = BeautifulSoup(compact, "html.parser")
    assert compact == markup
    assert all("font-size:13px" in p.get("style", "") for p in soup.find_all("p"))


def test_style_dictionary_does_not_parse_properties_inside_css_strings():
    style = ("content:'a; font-size:1px; b';background:#fef3c7;"
             "border:1px solid #e5e7eb;")
    markup = ("<html><head></head><body>" +
              "".join(f'<p style="{style}">文字{i}</p>' for i in range(8)) +
              "</body></html>")
    compact = render.compact_inline_styles(markup)
    assert compact == markup


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


def test_cpbl_matches_user_original_table_reference():
    """User's original: wide four-column tables, gray header, bold team, thin rules."""
    soup = BeautifulSoup(render._render_sports_html(standings(), html), "html.parser")
    tables = soup.select('table[data-mobile-layout="table"]')
    assert len(tables) == 2
    for table in tables:
        assert "width:100%" in table["style"]
        assert "border-collapse:collapse" in table["style"]
        rows = table.find_all("tr")
        assert "background:#f8fafc" in rows[0]["style"]
        headers = rows[0].find_all("th")
        assert "text-align:left" in headers[0]["style"]
        assert all("text-align:right" in cell["style"] for cell in headers[1:])
        for row in rows[1:]:
            cells = row.find_all("td")
            assert cells[0].find("b") is not None
            assert all("padding:4px 10px" in cell["style"] for cell in cells)
            assert all("border-bottom:1px solid #f1f5f9" in cell["style"] for cell in cells)


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
