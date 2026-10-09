"""Plain English words must not acquire a company's ticker identity."""
import pytest

from analysis_render_depth import news_subject
from reader_selection import article_is_tech


@pytest.mark.parametrize("title", [
    "Fed officials say now is not the time to cut",
    "AI training cost falls as models improve",
    "Heavy snow disrupts travel",
    "Government reports net change in employment",
    "The diplomatic arm of government announces talks",
])
def test_common_words_are_not_unlinked_stock_tickers(title):
    packet = {"news": [{"source_item_id": "n1", "title": title, "entities": []}]}
    assert news_subject({"source_item_id": "n1"}, packet)["name"] == ""


@pytest.mark.parametrize("title,ticker", [
    ("NOW reports quarterly results", "NOW"),
    ("ServiceNow reports quarterly results", "NOW"),
    ("Cloudflare grows enterprise sales", "NET"),
    ("(COST) reports quarterly results", "COST"),
    ("NVDA launches new GPUs", "NVDA"),
    ("Meta launches a new model", "META"),
])
def test_named_companies_and_explicit_uppercase_tickers_remain_supported(title, ticker):
    packet = {"news": [{"source_item_id": "n1", "title": title, "entities": []}]}
    assert news_subject({"source_item_id": "n1"}, packet)["name"] == ticker


def test_plain_now_does_not_turn_fed_story_into_tech_stock():
    card = {"source_item_id": "n1", "why_it_matters": "Interest rate outlook"}
    packet = {"news": [{"source_item_id": "n1", "title": "Fed officials say now is not the time to cut"}]}
    assert not article_is_tech(card, packet)
