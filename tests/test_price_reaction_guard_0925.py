"""Offline regression for the 9/25 unsupported price-causality wording."""

import analysis_origin
from analysis_render_depth import _news_line
import morning_report as mr
from price_reaction_guard import neutralize


_ARCHIVED = ("玻璃基板若在兩年內導入，設備與材料認證會先反映在資本支出上；"
             "聯茂昨日則以漲停放量回應（CMoney）。")


def test_archived_price_fact_stays_but_implied_cause_is_removed():
    manifest = {}
    revised = neutralize(_ARCHIVED, manifest)
    assert "聯茂昨日則出現漲停放量，惟無法僅憑股價確認原因（CMoney）" in revised
    assert "玻璃基板若在兩年內導入" in revised
    assert manifest["llm"]["price_reaction_claims_neutralized"] == 1


def test_shared_writer_boundary_covers_both_specialized_and_legacy(monkeypatch):
    monkeypatch.setattr(mr, "_record_report_writer", lambda text: None)
    for origin in (analysis_origin.LUNA_SPECIALIZED,
                   analysis_origin.LEGACY_AFTER_LUNA_FAILURE):
        def writer(*_args):
            mr._set_analysis_origin(origin)
            return _ARCHIVED

        monkeypatch.setattr(mr, "_call_llm_analysis_impl", writer)
        monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
        result = mr.call_llm_analysis({}, {}, {}, [])
        assert "以漲停放量回應" not in result
        assert "漲停放量" in result
        assert mr._RUN_MANIFEST["llm"]["price_reaction_claims_neutralized"] == 1


def test_non_price_response_is_not_rewritten():
    for text in ("公司以回購回應投資人。", "管理層以財測回應市場。"):
        manifest = {}
        assert neutralize(text, manifest) == text
        assert manifest == {}


def test_emergency_source_title_list_stays_verbatim(monkeypatch):
    raw = "- [媒體] 聯茂法說報喜 股價以漲停回應"
    monkeypatch.setattr(mr, "_record_report_writer", lambda text: None)

    def writer(*_args):
        mr._set_analysis_origin(analysis_origin.EMERGENCY_FALLBACK)
        return raw

    monkeypatch.setattr(mr, "_call_llm_analysis_impl", writer)
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    assert mr.call_llm_analysis({}, {}, {}, []) == raw
    assert "price_reaction_claims_neutralized" not in mr._RUN_MANIFEST.get("llm", {})


def test_quoted_source_is_preserved_while_surrounding_report_prose_is_corrected():
    source = "分析師表示：「聯茂以漲停回應。」本報認為聯茂以漲停回應（CMoney）。"
    manifest = {}
    revised = neutralize(source, manifest)
    assert "「聯茂以漲停回應。」" in revised
    assert "本報認為聯茂出現漲停，惟無法僅憑股價確認原因（CMoney）" in revised
    assert manifest["llm"]["price_reaction_claims_neutralized"] == 1


def test_rendered_and_source_headlines_are_not_rewritten():
    headlines = (
        "新聞標題：聯茂以漲停回應。\n"
        "**聯茂**｜[聯茂以漲停回應](https://example.test/news)\n"
        "本報解讀：聯茂以漲停回應（CMoney）。\n"
    )
    manifest = {}
    revised = neutralize(headlines, manifest)
    assert revised.startswith(
        "新聞標題：聯茂以漲停回應。\n"
        "**聯茂**｜[聯茂以漲停回應](https://example.test/news)\n"
    )
    assert "本報解讀：聯茂出現漲停，惟無法僅憑股價確認原因（CMoney）。" in revised
    assert manifest["llm"]["price_reaction_claims_neutralized"] == 1


def test_inline_source_link_and_blockquote_remain_verbatim():
    link = "[聯茂以漲停回應（CMoney）](https://example.test/news)"
    source = (
        f"本報整理：{link}；本報認為聯茂以漲停回應（CMoney）。\n"
        "> 新聞標題：聯茂以漲停回應（來源）\n"
    )
    manifest = {}
    revised = neutralize(source, manifest)
    assert link in revised
    assert "> 新聞標題：聯茂以漲停回應（來源）\n" in revised
    assert "本報認為聯茂出現漲停，惟無法僅憑股價確認原因（CMoney）" in revised
    assert manifest["llm"]["price_reaction_claims_neutralized"] == 1


def test_model_written_blockquote_price_cause_and_unrecognized_label_are_corrected():
    source = (
        "> 理由：聯茂以漲停回應（CMoney）。\n"
        "> 媒體標題：聯茂以漲停回應（來源）。\n"
    )
    manifest = {}
    revised = neutralize(source, manifest)
    assert "> 理由：聯茂出現漲停，惟無法僅憑股價確認原因（CMoney）。\n" in revised
    assert "> 媒體標題：聯茂出現漲停，惟無法僅憑股價確認原因（來源）。\n" in revised
    assert manifest["llm"]["price_reaction_claims_neutralized"] == 2


def test_unlinked_company_source_headline_is_preserved_after_rendering():
    packet = {
        "tw_universe": [{"code": "2383", "name": "台光電", "industry": "電子零組件業"}],
        "news": [{"source_item_id": "n1", "title": "台光電法說報喜 股價以漲停回應",
                  "entities": ["2383"], "source_name": "鉅亨", "url": ""}],
    }
    card = {"source_item_id": "n1", "why_it_matters": "本報認為台光電以漲停回應（鉅亨）。"}
    rendered = _news_line(card, packet)
    manifest = {}
    revised = neutralize(rendered, manifest)
    assert revised.splitlines()[0] == rendered.splitlines()[0]
    assert "本報解讀：本報認為台光電出現漲停，惟無法僅憑股價確認原因" in revised
    assert manifest["llm"]["price_reaction_claims_neutralized"] == 1


def test_bold_model_analysis_heading_is_not_a_source_headline():
    source = "**風險觀察**｜台光電以漲停回應（鉅亨）。\n"
    manifest = {}
    revised = neutralize(source, manifest)
    assert "**風險觀察**｜台光電出現漲停，惟無法僅憑股價確認原因（鉅亨）。" in revised
    assert manifest["llm"]["price_reaction_claims_neutralized"] == 1
