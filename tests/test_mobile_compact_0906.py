"""Compact reading type and tabular CPBL standings in the final email."""
import html
from html.parser import HTMLParser

import email_mobile as mobile
import morning_report as mr
import render_utils as render


class Tables(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.tables = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append(dict(attrs))


def standings():
    teams = [dict(rank=i + 1, team=team, wdl="20-1-18", pct="0.526", gb="1.5")
             for i, team in enumerate(["樂天桃猿", "中信兄弟", "統一7-ELEVEn獅",
                                        "味全龍", "台鋼雄鷹", "富邦悍將"])]
    return dict(cpbl=teams, cpbl_label="下半季", cpbl_full_year=teams,
                cpbl_full_year_label="全年", cpbl_source="Wikipedia 備援")


def test_cpbl_tables_keep_original_markup_through_compaction_and_mobile():
    raw = render._render_sports_html(standings(), html)
    assert raw.count('data-mobile-layout="table"') == 2
    page = "<html><head></head><body>" + raw + "</body></html>"
    compact = render.compact_inline_styles(page)
    final = mobile.enhance(compact)
    for table in Tables(final).tables:
        if table.get("data-mobile-layout") == "table":
            assert "mail-data" not in table.get("class", "")
            assert "mail-stack" not in table.get("class", "")
    assert 'class="mail-label"' not in final
    assert "font-size:13px" in compact and "font-size:12px" in compact
    assert "text-align:right" in compact
    assert compact.split("<body>", 1)[1].split("</body>", 1)[0] in final
    for label in ("下半季", "全年", "統一7-ELEVEn獅", "20-1-18", "0.526", "1.5", "Wikipedia 備援"):
        assert final.split("</head>", 1)[1].count(label) == compact.split("</head>", 1)[1].count(label)
    assert mobile.enhance(final) == final


def test_compact_type_sizes_are_mobile_only():
    css = mobile._CSS
    assert "@media screen and (max-width:600px)" in css
    assert ".mail-reading p,.mail-reading li{font-size:14px!important;line-height:1.7!important" in css
    assert ".mail-reading h2{font-size:18px!important" in css
    assert ".mail-reading h3{font-size:16px!important" in css
    assert "-webkit-text-size-adjust:100%" in css


def test_weekend_final_html_preserves_both_cpbl_tables(monkeypatch):
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    def no_network(*args, **kwargs):
        raise AssertionError("Rendering must not fetch")
    monkeypatch.setattr(mr, "_http_get", no_network)
    monkeypatch.setattr(mr.requests, "get", no_network)
    sports = render._render_sports_html(standings(), html)
    final = mr.render_weekend_digest_html("2026-09-06", "<p>測試正文</p>", sports, "", "", "")
    tables = [t for t in Tables(final).tables if t.get("data-mobile-layout") == "table"]
    assert len(tables) == 2
    assert all("mail-stack" not in t.get("class", "") for t in tables)
    assert "中華職棒戰績（下半季）" in final and "中華職棒戰績（全年）" in final
    assert mr._RUN_MANIFEST["llm"]["email_html"]["html_bytes"] == len(final.encode("utf-8"))
