"""Final email, not just intermediate Markdown, is the acceptance boundary."""
import copy
from html.parser import HTMLParser
import re

import pytest

import analysis_render as ar
import email_content_audit as audit
import email_mobile as mobile
import morning_report as mr
import run_quality


def page(body):
    return '<html><head></head><body class="original">' + body + '</body></html>'


def table(columns=5, rows=2):
    return '<table class="s0"><tr>' + ''.join(f'<th>欄{i}</th>' for i in range(columns)) + '</tr>' + ''.join(
        '<tr>' + ''.join(f'<td><a href="https://example.test/{r}/{i}">值{r}-{i}</a></td>'
                        for i in range(columns)) + '</tr>' for r in range(rows)) + '</table>'


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))


def test_mobile_stacks_wide_table_preserving_content_links_and_classes():
    raw = page(table())
    out = mobile.enhance(raw)
    assert mobile.enhance(out) == out
    assert 'class="original mail-reading"' in out
    assert 'class="s0 mail-data mail-stack"' in out
    assert out.count('class="mail-label"') == 10
    for r in range(2):
        for i in range(5):
            assert out.count(f'值{r}-{i}') == 1
            assert f'href="https://example.test/{r}/{i}"' in out
    for _, attrs in Tags(out).tags:
        assert len([a for a in attrs if a[0] == "class"]) <= 1


@pytest.mark.parametrize("body", [table(3), table().replace('<td>', '<td colspan="2">', 1),
                                  table().replace('<th>欄0</th>', '<th></th>', 1)])
def test_small_or_merged_or_unnamed_tables_do_not_stack(body):
    out = mobile.enhance(page(body))
    assert 'class="mail-label"' not in out


def test_nested_layout_is_not_stacked_but_its_data_table_is():
    raw = page('<table role="presentation"><tr><td>' + table() + '</td></tr></table>')
    out = mobile.enhance(raw)
    assert '<table role="presentation" class="mail-reading">' in out
    assert 'class="s0 mail-data mail-stack"' in out


def test_header_text_is_escaped_not_copied_as_markup():
    raw = page(table().replace('欄0', '&lt;img src=x onerror=evil()&gt;', 1))
    out = mobile.enhance(raw)
    assert '<img' not in out and '&lt;img' in out


def test_inline_minimal_email_remains_valid_without_head():
    assert mobile.enhance('<div>備援正文</div>') == '<div>備援正文</div>'


def test_finalization_errors_keep_mail_and_make_quality_defect(monkeypatch):
    def fail(_):
        raise ValueError("private error text")
    monkeypatch.setattr(mobile, "enhance", fail)
    m = {}
    raw = page("完整正文")
    assert audit.finalize("", raw, m) == raw
    assert m["llm"]["email_html"]["mobile_error"] == "ValueError"
    assert "private" not in str(m)
    assert "email_finalization_failed" in {f["code"] for f in run_quality.assess(m)}


def test_heading_in_css_is_not_a_visible_section_and_card_loss_is_detected():
    expected = '## ' + ar.SECTION_OTHER + '\n傳導:A → B。\n傳導:C → D。'
    html = page('<style>/* ' + ar.SECTION_OTHER + ' */</style><h2>' + ar.SECTION_OTHER + '</h2>傳導:A → B。')
    diag = audit.audit(expected, html)
    assert diag["missing_sections"] == [] and diag["lost_cards"] == 1
    empty = page('<style>/* ' + ar.SECTION_OTHER + ' */</style>')
    assert audit.audit(expected, empty)["missing_sections"] == [ar.SECTION_OTHER]


def test_trim_budget_includes_mobile_markup_not_just_the_raw_table(monkeypatch):
    from test_golden_faults import _golden_quotes
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "trim")
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    monkeypatch.setattr(mr, "_render_journals_html",
                        lambda *a, **k: "JOURNAL_SENTINEL" + table(5, 70))
    # Before the fix, the raw candidate fits 95 KB but becomes 106.8 KB after
    # adding mobile cell labels. With correct sizing the optional table is cut.
    html = mr.render_html(_golden_quotes(), {"error": "fixture"}, {"error": "fixture"},
                          "## 八、科技板塊脈動\n" + "測" * 16000, "2026-09-06", "每日報")
    assert "JOURNAL_SENTINEL" not in html
    assert len(html.encode("utf-8")) <= 95 * 1024
    assert mr._RUN_MANIFEST["llm"]["email_html"]["html_bytes"] == len(html.encode("utf-8"))


def test_weekend_and_minimal_renderer_diagnostics_describe_returned_html(monkeypatch):
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    html = mr.render_weekend_digest_html("2026-09-06", table(), "", "", "", "")
    diag = mr._RUN_MANIFEST["llm"]["email_html"]
    assert diag["mobile"] == "enhanced" and diag["html_bytes"] == len(html.encode("utf-8"))
    minimal = mr._render_minimal_html({}, {}, {}, "## 九、其他類股資訊\n傳導:A → B。",
                                      "2026-09-06", "每日報")
    diag = mr._RUN_MANIFEST["llm"]["email_html"]
    assert diag["mobile"] == "inline_fallback" and diag["lost_cards"] == 0
    assert diag["html_bytes"] == len(minimal.encode("utf-8"))


@pytest.mark.parametrize("tech,other", [(9, 9), (12, 10)])
def test_production_sized_report_reaches_final_html_without_missing_sectors(monkeypatch, tech, other):
    from test_analysis_cap_0905 import _render
    from test_golden_faults import _golden_quotes
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    def no_network(*args, **kwargs):
        raise AssertionError("Rendering must not fetch")
    monkeypatch.setattr(mr, "_http_get", no_network)
    monkeypatch.setattr(mr.requests, "get", no_network)
    analysis, _ = _render(tech, other)
    analysis = 'PREAMBLE_MUST_NOT_REACH_EMAIL\n' + analysis
    html = mr.render_html(copy.deepcopy(_golden_quotes()), {"error": "fixture"},
                          {"error": "fixture"}, analysis,
                          "2026-09-06", "每日報")
    diag = mr._RUN_MANIFEST["llm"]["email_html"]
    assert 'PREAMBLE_MUST_NOT_REACH_EMAIL' not in html
    assert diag["missing_sections"] == [] and diag["lost_cards"] == 0
    assert diag["chain_counts"][ar.SECTION_OTHER] == {"expected": min(6, other), "html": min(6, other)}
    assert diag["chain_counts"][ar.SECTION_TECH] == {"expected": min(6, tech), "html": min(6, tech)}
    assert 'id="morning-mobile"' in html
    assert html.index(ar.SECTION_TECH) < html.index(ar.SECTION_OTHER) < html.index(ar.SECTION_MACRO)
    assert len(re.findall(r"傳導[:：]", html)) == min(6, tech) + min(6, other)
