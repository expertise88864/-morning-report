"""Offline final-pass HTML compaction must preserve reader-visible content."""
from bs4 import BeautifulSoup

import email_content_audit
import email_syntax_compact


def _observable(markup: str) -> tuple:
    soup = BeautifulSoup(markup, "html.parser")
    return (
        tuple(soup.stripped_strings),
        tuple((a.get_text(), a.get("href")) for a in soup.find_all("a")),
        tuple((name, len(soup.find_all(name))) for name in ("table", "tr", "td", "th")),
        tuple((tag.get("class"), (tag.get("style") or "").rstrip(";"))
              for tag in soup.find_all(True)),
    )


def test_optional_syntax_only_and_idempotent():
    markup = ('<html><head><style>.s1{color:red;}</style></head><body>'
              '<table><tr><td class="s1" style="font-size:14px;">A</td> \n'
              '<td class="s2" style="text-align:right;">B</td></tr></table>'
              '<a href="https://example.test/?a=1&amp;b=2">來源</a></body></html>')
    result = email_syntax_compact.compact(markup)
    assert 'class=s1 style=font-size:14px' in result
    assert "</td><td" in result
    assert _observable(result) == _observable(markup)
    assert email_syntax_compact.compact(result) == result


def test_protected_content_and_quoted_greater_than_are_untouched():
    protected = ('<!-- <td class="s1" style="color:red;"> -->'
                 '<style>td:before{content:\'<td class="s1">\';}</style>'
                 '<pre><td class="s1" style="color:red;"></pre>'
                 '<script>const x=\'<td class="s1">\';</script>')
    markup = protected + '<td title="a > b" class="s2" style="color:blue;">T</td>'
    result = email_syntax_compact.compact(markup)
    assert result.startswith(protected)
    assert 'title="a > b" class=s2 style=color:blue' in result


def test_attribute_lookalikes_and_entity_semicolon_are_untouched():
    lookalikes = ('<span title=\'example class="s1"\' '
                  'data-note=\'sample style="color:red;"\'>x</span>')
    assert email_syntax_compact.compact(lookalikes) == lookalikes
    assert email_syntax_compact.compact('<span style="content:&amp;">x</span>') == (
        '<span style="content:&amp;">x</span>')


def test_unquoted_style_requires_html_safe_single_token():
    markup = ('<td style="color:#0f172a;">A</td>'
              '<td style="font-family:PingFang TC;">B</td>'
              '<td style="content:\'quoted\';">C</td>'
              '<td style="background:url(a>b);">D</td>')
    result = email_syntax_compact.compact(markup)
    assert 'style=color:#0f172a' in result
    assert 'style="font-family:PingFang TC"' in result
    assert 'style="content:\'quoted\'"' in result
    assert 'style="background:url(a>b)"' in result
    assert _observable(result) == _observable(markup)


def test_only_known_editor_comments_are_removed():
    markup = ('<!-- HERO --><div>正文</div><!--PF_ROW_START-->'
              '<!--[if mso]><table><tr><td>Office</td></tr></table><![endif]-->'
              '<!-- FOOTER: visual marker -->')
    result = email_syntax_compact.compact(markup)
    assert '<!-- HERO -->' not in result
    assert '<!-- FOOTER: visual marker -->' not in result
    assert '<!--PF_ROW_START-->' in result
    assert '<!--[if mso]>' in result
    assert _observable(result) == _observable(markup)


def test_finalization_records_savings_and_failure_without_dropping_mail(monkeypatch, capsys):
    monkeypatch.setattr(email_content_audit.email_mobile, "enhance", lambda markup: markup)
    markup = '<html><head></head><body><td class="s1" style="font-size:14px;">字</td></body></html>'
    manifest = {}
    result = email_content_audit.finalize("", markup, manifest)
    assert _observable(result) == _observable(markup)
    assert manifest["llm"]["email_html"]["syntax_bytes_saved"] > 0

    def fail(_markup: str) -> str:
        raise ValueError("offline failure")

    monkeypatch.setattr(email_syntax_compact, "compact", fail)
    manifest = {}
    assert email_content_audit.finalize("", markup, manifest) == markup
    assert manifest["llm"]["email_html"]["syntax_error"] == "ValueError"
    assert "syntax compaction failed" in capsys.readouterr().err
