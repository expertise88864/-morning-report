"""Source links must survive the real Markdown-to-email formatting pipeline."""
import html
import json

from bs4 import BeautifulSoup
import pytest

import morning_report as report
import render_utils as render
import news_research_context as research
import week_review
from test_news_research import packet


def email_fragment(markdown, allowed_urls=()):
    return BeautifulSoup(render._dim_source_citations(
        render._style_analysis_html(render._md_to_html(
            markdown, allowed_urls=allowed_urls))), "html.parser")


def test_historical_source_is_a_clickable_label_not_a_visible_long_url():
    pk = packet()
    expected = pk["historical_sources"][0]["url"]
    fragment = email_fragment(research.history_prose({"source_item_id": "n1"}, pk),
                              allowed_urls=[expected])
    link = fragment.find("a")
    assert link is not None and link["href"] == expected
    assert "原始報導" in link.get_text()
    assert expected not in fragment.get_text()
    assert all(rule in link.get("style", "") for rule in
               ("color:#94a3b8", "font-size:12px", "font-weight:400"))


def test_long_rss_link_and_query_are_not_truncated_or_double_escaped():
    url = "https://example.com/rss/" + "A" * 530 + "?oc=5&source=news"
    fragment = email_fragment(f"[2026-09-05 原始報導]({url})", allowed_urls=[url])
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


def test_model_markdown_link_needs_a_trusted_source_url():
    known = "https://news.example.test/actual"
    unknown = "https://attacker.example/fake"
    fragment = email_fragment(f"[已核實來源]({unknown})", allowed_urls=[known])
    assert fragment.find("a") is None
    assert unknown in fragment.get_text()
    assert email_fragment(f"[原始報導]({known})", allowed_urls=[known]).a["href"] == known
    assert email_fragment(f"[原始報導]({known})").find("a") is None


def test_report_link_allowlist_uses_fetched_news_and_packet_sources_only():
    current = "https://news.example.test/current"
    historical = "https://archive.example.test/history?q=台股"
    supplemental = "https://research.example.test/new-discovery"
    urls = render.analysis_source_urls({
        "NEWS_LINK_INDEX": [{"u": current}, {"u": ""}],
        "_ANALYSIS_PACKET_URLS": [historical, supplemental],
    })
    assert current in urls and historical in urls and supplemental in urls
    fragment = email_fragment(f"[當日新聞]({current}) [舊文]({historical}) "
                              f"[補查]({supplemental})",
                              allowed_urls=urls)
    assert [link["href"] for link in fragment.find_all("a")] == [
        current, historical, supplemental]
    encoded = "https://archive.example.test/history?q=%E5%8F%B0%E8%82%A1"
    assert email_fragment(f"[舊文]({encoded})", allowed_urls=urls).a["href"] == encoded


def test_specialized_packet_source_urls_include_supplemental_current_news():
    historical = "https://archive.example.test/old"
    supplemental = "https://research.example.test/new-discovery"
    urls = render.packet_source_urls({
        "historical_sources": [{"url": historical}],
        "news": [{"url": supplemental}, {"url": ""}],
        "untrusted_model_links": [{"url": "https://attacker.example/fake"}],
    })
    assert urls == [historical, supplemental]
    assert [link["href"] for link in email_fragment(
        f"[舊文]({historical}) [補查]({supplemental})",
        allowed_urls=render.analysis_source_urls({"_ANALYSIS_PACKET_URLS": urls})
    ).find_all("a")] == urls


def test_weekly_review_links_only_the_urls_in_its_bounded_source_material(monkeypatch):
    known = "https://news.example.test/week"
    unknown = "https://attacker.example/claim"
    material = "■ 本週主題與相關原始報導\n" + json.dumps([{
        "representative_source": {"url": known},
        "related_sources": [{"url": ""}],
    }])
    urls = week_review.material_source_urls(material)
    assert urls == [known]
    monkeypatch.setattr(report, "_RUN_MANIFEST", {})
    rendered = report._render_week_review_html(
        f"[本週原文]({known}) [未核對]({unknown})", html, source_urls=urls)
    soup = BeautifulSoup(rendered, "html.parser")
    assert [link["href"] for link in soup.find_all("a")] == [known]
    assert unknown in soup.get_text()
