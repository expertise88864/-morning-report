"""The archived 9/25 buyer/target reversal, without calls or state writes."""

import analysis_origin
import fallback_recap
import morning_report as mr
import news_events
from analysis_render_depth import news_subject
from legacy_actor_guard import correct_investment_target_headings
from news_heading import companies
from reader_selection import article_is_tech


URL = "https://news.cnyes.com/news/id/6615287"
TITLE = "中鋼斥資3.37億元加碼台積電158張 買進均價2135.31元"
ITEM = {"source_item_id": "archived", "title": TITLE, "link": URL,
        "entities": ["2002", "2330"]}
PARAGRAPH = ("**台積電（2330，全球晶圓代工龍頭）**：中鋼子公司超揚投資公告"
             "自 2025 年 11 月起至 2026 年 9 月 24 日買進 158 張台積電"
             f" [鉅亨台股]({URL})。這是財務性投資，不是台積電營運訊號。")
TEXT = "## 八、科技板塊脈動\n\n" + PARAGRAPH + "\n\n## 九、其他類股資訊\n"


def _packet():
    return {"news": [ITEM], "tw_universe": [
        {"code": "2002", "name": "中鋼", "industry": "鋼鐵工業"},
        {"code": "2330", "name": "台積電", "industry": "半導體業"}]}


def test_structured_news_names_buyer_and_keeps_the_source_headline():
    packet = _packet()
    assert companies(ITEM, packet) == "中鋼（2002）"
    subject = news_subject({"source_item_id": "archived"}, packet)
    assert subject["name"] == "中鋼"
    assert not article_is_tech({"source_item_id": "archived"}, packet)
    assert ITEM["title"] == TITLE


def test_legacy_paragraph_and_next_day_opinion_name_buyer_not_target():
    manifest = {}
    corrected = correct_investment_target_headings(
        TEXT, [ITEM], origin=analysis_origin.LEGACY_AFTER_LUNA_FAILURE,
        manifest=manifest)
    assert "**中鋼加碼台積電（非台積電營運新聞）**：" in corrected
    assert "**台積電（2330" not in corrected
    assert f"[鉅亨台股]({URL})" in corrected
    assert manifest["llm"]["investment_target_headings_corrected"] == 1
    recap = fallback_recap.extract(
        corrected, [ITEM], "2026-09-25", analysis_origin.LEGACY_AFTER_LUNA_FAILURE)
    assert recap["status"] == "unvalidated_model_opinion"
    assert recap["items"][0]["statement"].startswith("**中鋼加碼台積電")


def test_shared_writer_boundary_applies_guard_before_recap(monkeypatch):
    def fake_writer(*args):
        mr._set_analysis_origin(analysis_origin.LEGACY_AFTER_LUNA_FAILURE)
        return TEXT

    monkeypatch.setattr(mr, "_call_llm_analysis_impl", fake_writer)
    monkeypatch.setattr(mr, "_record_report_writer", lambda text: None)
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    result = mr.call_llm_analysis({}, {}, {}, [ITEM])
    assert "**中鋼加碼台積電" in result
    assert mr._RUN_MANIFEST["llm"]["investment_target_headings_corrected"] == 1


def test_ambiguous_or_unlinked_text_is_preserved():
    cases = [
        (TEXT, [{**ITEM, "title": "中鋼股價上漲，台積電法說在即"}]),
        (TEXT.replace(f" [鉅亨台股]({URL})", ""), [ITEM]),
        (TEXT.replace("中鋼子公司", "某公司子公司"), [ITEM]),
    ]
    for prose, sources in cases:
        manifest = {}
        assert correct_investment_target_headings(
            prose, sources, origin=analysis_origin.LEGACY_PRIMARY,
            manifest=manifest) == prose
        assert manifest == {}
    assert correct_investment_target_headings(
        TEXT, [ITEM], origin=analysis_origin.LUNA_SPECIALIZED,
        manifest={}) == TEXT


def test_retained_earnings_in_the_archived_purchase_is_not_a_profit_release():
    archived = TITLE + " 中鋼代子公司公告買進股票；處分差額將列入保留盈餘。"
    assert news_events._event_type(archived) == "general"
    assert news_events.normalize_event_type("earnings", archived) == "general"
    assert news_events._event_type("台積電公布本季財報，保留盈餘同步增加") == "earnings"
    capital_expenditure = "台積電加碼研發，保留盈餘創新高"
    assert news_events._event_type(capital_expenditure) == "earnings"
    assert news_events.normalize_event_type("earnings", capital_expenditure) == "earnings"
    mixed = "台積電加碼研發，外資持股升至75%，保留盈餘創新高"
    assert news_events._event_type(mixed) == "earnings"
    assert news_events.normalize_event_type("earnings", mixed) == "earnings"
    for purchase in (
            "中鋼斥資3億元加碼台積電持股；處分差額列入保留盈餘",
            "中鋼增持台積電持股；處分差額列入保留盈餘",
            "中鋼買進台積電證券；處分差額列入保留盈餘"):
        assert news_events._event_type(purchase) == "general"
        assert news_events.normalize_event_type("earnings", purchase) == "general"
