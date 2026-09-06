"""Source links must survive the real Markdown-to-email formatting pipeline."""
from bs4 import BeautifulSoup
import pytest

import render_utils as render
import news_research_context as research
from test_news_research import packet


def email_fragment(markdown):
    return BeautifulSoup(render._dim_source_citations(
        render._style_analysis_html(render._md_to_html(markdown))), "html.parser")


def test_historical_source_is_a_clickable_label_not_a_visible_long_url():
    pk = packet()
    expected = pk["historical_sources"][0]["url"]
    fragment = email_fragment(research.history_prose({"source_item_id": "n1"}, pk))
    link = fragment.find("a")
    assert link is not None and link["href"] == expected
    assert "原始報導" in link.get_text()
    assert expected not in fragment.get_text()
    assert all(rule in link.get("style", "") for rule in
               ("color:#94a3b8", "font-size:12px", "font-weight:400"))


def test_long_rss_link_and_query_are_not_truncated_or_double_escaped():
    url = "https://example.com/rss/" + "A" * 530 + "?oc=5&source=news"
    fragment = email_fragment(f"[2026-09-05 原始報導]({url})")
    assert fragment.a is not None and fragment.a["href"] == url
    assert url not in fragment.get_text()


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,bad", "//example.com",
                                 "https://example.com/" + "A" * 2050])
def test_unsafe_or_oversized_markdown_links_never_become_anchors(url):
    assert email_fragment(f"[source]({url})").find("a") is None


def test_markdown_link_cannot_inject_html_or_attributes():
    fragment = email_fragment('[<img src=x onerror=alert(1)>](https://example.com/?q="x")')
    assert not fragment.find("img")
    assert all(not key.startswith("on") for tag in fragment.find_all() for key in tag.attrs)


def test_existing_source_url_limit_is_preserved():
    assert render.safe_href("https://example.com/" + "A" * 530) == ""
