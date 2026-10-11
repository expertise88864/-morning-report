"""Source links must survive the real Markdown-to-email formatting pipeline."""
from bs4 import BeautifulSoup
import pytest

import render_utils as render
import news_research_context as research
from test_news_research import packet


def email_fragment(markdown, trusted_urls=()):
    return BeautifulSoup(render._dim_source_citations(
        render._style_analysis_html(render._md_to_html(markdown, trusted_urls=trusted_urls))), "html.parser")


def test_historical_source_is_a_clickable_label_not_a_visible_long_url():
    pk = packet()
    expected = pk["historical_sources"][0]["url"]
    fragment = email_fragment(research.history_prose({"source_item_id": "n1"}, pk), [expected])
    link = fragment.find("a")
    assert link is not None and link["href"] == expected
    assert "原始報導" in link.get_text()
    assert expected not in fragment.get_text()
    assert all(rule in link.get("style", "") for rule in
               ("color:#94a3b8", "font-size:12px", "font-weight:400"))


def test_long_rss_link_and_query_are_not_truncated_or_double_escaped():
    url = "https://example.com/rss/" + "A" * 530 + "?oc=5&source=news"
    fragment = email_fragment(f"[2026-09-05 原始報導]({url})", [url])
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


@pytest.mark.parametrize("length", [753, 956, 2048])
def test_sports_collected_long_rss_links_survive_rendering(length):
    import html
    prefix = "https://news.google.com/rss/articles/"
    suffix = "?oc=5&hl=zh-TW"
    url = prefix + "A" * (length - len(prefix) - len(suffix)) + suffix
    sports = {"news": {"中華職棒": [{"title": "Collected sports story", "link": url}]}}
    soup = BeautifulSoup(render._render_sports_html(sports, html), "html.parser")
    assert soup.find("a", href=url) is not None
    assert url not in soup.get_text()


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,bad",
                                 "https://example.com/\nattack",
                                 "https://example.com/" + "A" * 2048])
def test_sports_links_still_reject_unsafe_or_oversized_sources(url):
    import html
    sports = {"news": {"中華職棒": [{"title": "Collected sports story", "link": url}]}}
    soup = BeautifulSoup(render._render_sports_html(sports, html), "html.parser")
    assert soup.find("a") is None
    assert "Collected sports story" in soup.get_text()


def test_model_written_off_list_https_link_stays_plain_text():
    fragment = email_fragment("[Official source](https://uncollected.example/story)",
                              ["https://collected.example/story"])
    assert fragment.find("a") is None
    assert fragment.get_text() == "Official source"


def test_source_allowlist_never_reads_generated_analysis():
    from source_link_policy import collected_urls
    quotes = {"NEWS_LINK_INDEX": [{"u": "https://source.example/today"}],
              "NEWS_MEMORY": [{"url": "https://source.example/history"}],
              "NEWS_RESEARCH_BACKGROUND": [{"url": "https://source.example/older"}],
              "NEWS_RESEARCH_SOURCES": [{"link": "https://source.example/extra"}],
              "TW_MOPS": [{"link": "https://source.example/announcement"}],
              "analysis": {"url": "https://uncollected.example/model"}}
    assert collected_urls(quotes) == {
        "https://source.example/" + suffix for suffix in
        ("today", "history", "older", "extra", "announcement")}


def test_source_allowlist_handles_missing_or_malformed_sections():
    from source_link_policy import collected_urls
    assert collected_urls({"NEWS_LINK_INDEX": "not rows", "NEWS_MEMORY": [None, {"url": []}]}) == set()


@pytest.mark.parametrize("minimal", [False, True])
def test_final_email_only_links_collected_sources(monkeypatch, minimal):
    import morning_report as mr
    from test_golden_faults import _golden_quotes
    def no_network(*args, **kwargs):
        raise AssertionError("Offline renderer regression must not fetch")
    monkeypatch.setattr(mr, "_http_get", no_network)
    monkeypatch.setattr(mr.requests, "get", no_network)
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    quotes = _golden_quotes()
    known = "https://source.example/history"
    unknown = "https://uncollected.example/model"
    quotes["NEWS_MEMORY"] = [{"url": known}]
    renderer = mr._render_minimal_html if minimal else mr.render_html
    result = renderer(quotes, {"error": "fixture"}, {"error": "fixture"},
        f"## 分析\n[已收集來源]({known}) 與 [模型來源]({unknown})", "2026-10-09", "每日報")
    soup = BeautifulSoup(result, "html.parser")
    assert soup.find("a", href=known) is not None
    assert soup.find("a", href=unknown) is None
