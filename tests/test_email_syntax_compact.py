"""Offline checks for final email syntax compaction."""
from __future__ import annotations

from bs4 import BeautifulSoup

import email_content_audit
import email_syntax_compact
from render_utils import _style_analysis_html, compact_inline_styles


def _observable(html: str) -> tuple:
    soup = BeautifulSoup(html, "html.parser")
    return (
        tuple(soup.stripped_strings),
        tuple((a.get_text(), a.get("href")) for a in soup.find_all("a")),
        tuple((name, len(soup.find_all(name))) for name in ("table", "tr", "td", "th")),
        tuple((tag.get("class"), (tag.get("style") or "").rstrip(";"))
              for tag in soup.find_all(True)),
    )


def test_only_optional_syntax_changes_and_is_idempotent():
    html = ("<html><head><style>.s1{color:red;}</style></head><body>"
            "<table><tr><td class=\"s1\" style=\"font-size:14px;\">A</td> \n"
            "<td class='s2' style='text-align:right;'>B</td></tr></table>"
            "<a href=\"https://example.test/?a=1&amp;b=2\">來源</a></body></html>")
    out = email_syntax_compact.compact(html)
    assert 'class=s1 style="font-size:14px"' in out
    assert "class='s2' style='text-align:right'" in out
    assert "</td><td" in out
    assert _observable(out) == _observable(html)
    assert email_syntax_compact.compact(out) == out


def test_protected_content_and_quoted_greater_than_are_untouched():
    protected = ("<!-- <td class=\"s1\" style=\"color:red;\"> -->"
                 "<style>td:before{content:'<td class=\"s1\">';}</style>"
                 "<pre><td class=\"s1\" style=\"color:red;\"></pre>"
                 "<script>const x='<td class=\"s1\">';</script>")
    html = protected + '<td title="a > b" class="s2" style="color:blue;">T</td>'
    out = email_syntax_compact.compact(html)
    assert out.startswith(protected)
    assert 'title="a > b" class=s2 style="color:blue"' in out


def test_syntax_looking_text_inside_other_attributes_is_not_rewritten():
    html = ('<span title=\'example class="s1"\' '
            'data-note=\'sample style="color:red;"\'>x</span>')
    assert email_syntax_compact.compact(html) == html


def test_html_entity_semicolon_is_not_a_css_declaration_separator():
    html = '<span style="content:&amp;">x</span>'
    assert email_syntax_compact.compact(html) == html


def test_stable_long_email_gets_smaller_without_losing_reader_content():
    """A fixed stress case must outlive rotating production email archives."""
    paragraphs = "".join(
        '<p style="line-height:1.7;font-size:14px;color:#0f172a;'
        'padding:6px 10px;border-bottom:1px solid #e2e8f0;">'
        f'離線分析段落 {i:04d}：事件、反證與觀察條件均保留。</p>'
        for i in range(700)
    )
    sources = "".join(
        f'<a href="https://example.test/source/{i}">來源 {i}</a>'
        for i in range(24)
    )
    original = f"<html><head></head><body>{paragraphs}{sources}</body></html>"
    assert len(original.encode()) > 102 * 1024
    before = compact_inline_styles(original)
    after = email_syntax_compact.compact(before)
    assert len(after.encode()) < len(before.encode())
    assert len(after.encode()) < 102 * 1024
    assert _observable(after) == _observable(before)


def test_fresh_analysis_markup_stays_below_previous_compactor_size():
    """An archived, already-classed email cannot measure a fresh render."""
    analysis = _style_analysis_html(
        "".join(f"<h2>區塊 {i}</h2><h3>公司 {i}</h3>" for i in range(12))
        + "".join(f"<p>離線段落 {i}</p>" for i in range(90))
        + "".join(f"<strong>重點 {i}</strong>" for i in range(36))
    )
    links = "".join(
        f'<a href="https://example.test/{i}" '
        'style="color:#0f172a;text-decoration:none;">來源</a>'
        for i in range(42)
    )
    cells = "".join(
        '<td style="padding:4px 10px;border-bottom:1px solid #f1f5f9;'
        'text-align:right;font-size:13px;color:#64748b;">數字</td>'
        for _ in range(22)
    )
    fresh = f"<html><head></head><body>{analysis}{links}<table><tr>{cells}</tr></table></body></html>"
    compacted = email_syntax_compact.compact(compact_inline_styles(fresh))
    # The pre-change compactor produced 13,585 UTF-8 bytes on this exact
    # fresh fixture. New safety fallbacks must not reintroduce size growth.
    assert len(compacted.encode("utf-8")) <= 13_585
    before, after = BeautifulSoup(fresh, "html.parser"), BeautifulSoup(compacted, "html.parser")
    assert tuple(before.stripped_strings) == tuple(after.stripped_strings)
    assert [(a.get("href")) for a in before.find_all("a")] == [
        a.get("href") for a in after.find_all("a")]
    assert len(before.find_all("td")) == len(after.find_all("td")) == 22
    # Removing the sheet still leaves pale headings and legible dark text.
    assert all("background" in heading.get("style", "")
               for heading in after.find_all(["h2", "h3"]))
    assert all(not any(part.startswith("color:") for part in
                       heading.get("style", "").split(";"))
               for heading in after.find_all(["h2", "h3"]))
    for heading in after.find_all("h3"):
        # An inline background shorthand would win over the class gradient.
        assert "background-color:#fef3c7" in heading.get("style", "")
        assert "background:" not in heading.get("style", "")
        name = heading.get("class", [""])[0]
        assert f".{name}{{" in compacted
    assert "background:linear-gradient(90deg,#fef3c7,#fde68a)" in compacted


def test_finalization_records_savings_and_compactor_failure_is_visible(monkeypatch, capsys):
    monkeypatch.setattr(email_content_audit.email_mobile, "enhance", lambda html: html)
    html = '<html><head></head><body><td class="s1" style="font-size:14px;">字</td></body></html>'
    manifest = {}
    out = email_content_audit.finalize("", html, manifest)
    assert out != html
    assert manifest["llm"]["email_html"]["syntax_bytes_saved"] > 0

    def fail(_html: str) -> str:
        raise ValueError("offline failure")

    monkeypatch.setattr(email_syntax_compact, "compact", fail)
    manifest = {}
    out = email_content_audit.finalize("", html, manifest)
    assert out == html
    assert manifest["llm"]["email_html"]["syntax_error"] == "ValueError"
    assert "syntax compaction failed" in capsys.readouterr().err
