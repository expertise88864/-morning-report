"""The Sept 27 Podcast quote supports a spoken name, not a ticker identity."""
import html

from podcast_entity_identity import conflicting_spoken_name
from render_utils import _render_podcast_html


def _ticker(name: str, quote: str) -> dict:
    return {"name": name, "code": "BE", "market": "US", "direction": "neutral",
            "reason": "主持人提及資料中心供電。", "stance_basis": "investment_view",
            "direction_evidence": {"version": 1, "status": "attributed",
                                   "direction": "neutral", "quote": quote}}


def test_different_spoken_energy_name_is_not_displayed_as_confirmed_ticker():
    ticker = _ticker("Bloom Energy", "Blue Energy就是你要去賭那個什麼,表貨供電嘛")
    before = dict(ticker)
    assert conflicting_spoken_name(ticker) == "Blue Energy"
    output = _render_podcast_html(
        [{"show": "股癌", "title": "EP700", "digest": {"tickers": [ticker]}}],
        [], html, related_sources={"Bloom Energy": [
            {"url": "https://example.invalid/be", "title": "Bloom Energy news"}]})
    assert "Blue Energy（公司對應待核實）" in output
    assert "Bloom Energy" not in output and "（BE）" not in output
    assert "[中性]" not in output and "提及，未確認多空表態" in output
    assert "example.invalid" not in output
    assert ticker == before


def test_matching_name_and_other_language_alias_are_not_downgraded():
    matching = _ticker("Bloom Energy", "主持人提及 Bloom Energy 的供電方案。")
    assert conflicting_spoken_name(matching) == ""
    chinese = _ticker("台積電", "主持人提及台積電的投資觀點。")
    assert conflicting_spoken_name(chinese) == ""
    output = _render_podcast_html(
        [{"show": "節目", "digest": {"tickers": [matching]}}], [], html)
    assert "Bloom Energy（BE）" in output and "[中性]" in output


def test_energy_sector_phrase_does_not_hide_an_attributed_ticker():
    ticker = _ticker("Bloom Energy", "Bloom 在 Clean Energy 裡面算是供電解方")
    assert conflicting_spoken_name(ticker) == ""
    output = _render_podcast_html(
        [{"show": "節目", "digest": {"tickers": [ticker]}}], [], html,
        related_sources={"Bloom Energy": [
            {"url": "https://example.invalid/be", "title": "相關新聞"}]})
    assert "Bloom Energy（BE）" in output
    assert "[中性]" in output and "公司對應待核實" not in output
    assert "example.invalid" in output


def test_generic_energy_words_need_a_category_context_to_be_ignored():
    assert conflicting_spoken_name(_ticker(
        "Bloom Energy", "Nuclear Energy 族群近期受關注")) == ""
    assert conflicting_spoken_name(_ticker(
        "Bloom Energy", "Clean Energy 這家公司正在擴張")) == "Clean Energy"


def test_multiple_spoken_energy_names_do_not_certify_a_ticker():
    ticker = _ticker("Bloom Energy", "Blue Energy 和 Solar Energy 這兩家公司都被提到")
    assert conflicting_spoken_name(ticker) == "Blue Energy／Solar Energy"
    output = _render_podcast_html(
        [{"show": "節目", "digest": {"tickers": [ticker]}}], [], html)
    assert "Blue Energy／Solar Energy（公司對應待核實）" in output
    assert "Bloom Energy" not in output and "（BE）" not in output
    assert "[中性]" not in output


def test_matching_and_conflicting_spoken_names_remain_ambiguous():
    ticker = _ticker("Bloom Energy", "Bloom Energy 與 Blue Energy 都有討論")
    assert conflicting_spoken_name(ticker) == "Bloom Energy／Blue Energy"


def test_grounded_quote_conflict_is_not_hidden_by_unknown_stance():
    ticker = _ticker("Bloom Energy", "Blue Energy就是供電題材，但未評論多空")
    ticker["stance_basis"] = "unclear"
    ticker["direction"] = "unknown"
    ticker["direction_evidence"]["status"] = "unverified"
    ticker["direction_evidence"]["direction"] = ""
    assert conflicting_spoken_name(ticker) == "Blue Energy"
    output = _render_podcast_html(
        [{"show": "節目", "digest": {"tickers": [ticker]}}], [], html)
    assert "Blue Energy（公司對應待核實）" in output
    assert "Bloom Energy" not in output and "（BE）" not in output


def test_spoken_name_case_does_not_certify_a_different_ticker():
    assert conflicting_spoken_name(_ticker(
        "Bloom Energy", "blue energy就是供電題材")) == "blue energy"
    assert conflicting_spoken_name(_ticker(
        "Bloom Energy", "bloom energy的方案值得追蹤")) == ""
    assert conflicting_spoken_name(_ticker(
        "Bloom Energy", "clean energy產業近期受關注")) == ""


def test_lowercase_generic_energy_is_not_a_second_company_when_ticker_was_spoken():
    for quote in ("Bloom Energy 是 new energy 的龍頭",
                  "and energy 只是轉錄雜訊，Bloom Energy 的供電方案有討論"):
        assert conflicting_spoken_name(_ticker("Bloom Energy", quote)) == ""
    # Generic wording alone is not a competing company identity.
    assert conflicting_spoken_name(_ticker(
        "Bloom Energy", "new energy 的供電方案有討論")) == ""
