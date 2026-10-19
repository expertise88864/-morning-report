"""2026-10-19: finance.yahoo.com/news/rssindex returned 404 for ten days.

The replacement is Yahoo's live headline feed for the S&P 500 (same publisher and
role in the international-finance bucket), verified on 10/19 with 20 fresh items.
"""
from urllib.parse import parse_qs, urlparse

import morning_report as mr


def test_yahoo_feed_uses_the_live_headline_endpoint():
    url = urlparse(mr.RSS_FEEDS["Yahoo Finance"])
    assert url.scheme == "https" and url.netloc == "feeds.finance.yahoo.com"
    assert url.path == "/rss/2.0/headline"
    assert parse_qs(url.query) == {"s": ["^GSPC"], "region": ["US"], "lang": ["en-US"]}
    assert all("rssindex" not in u for u in mr.RSS_FEEDS.values())
