"""Archived 9/23 headlines: distinguish the reporting company from a rival."""

from analysis_render_depth import news_subject, _news_line
from news_heading import companies
from reader_selection import article_is_tech
from analysis_depth import section_counts, depth_advisories
from reader_editorial import is_macro


def _packet(title: str) -> dict:
    return {
        "tw_universe": [
            {"code": "2383", "name": "台光電", "industry": "電子零組件業"},
            {"code": "6274", "name": "台燿", "industry": "電子零組件業"},
        ],
        "news": [{"source_item_id": "archived", "title": title,
                  "entities": ["NVDA", "2383"]}],
    }


def test_ali_challenges_nvidia_without_becoming_nvidia_news():
    title = "阿里推最強 AI 晶片槓輝達 真武 V900 明年Q1量產"
    packet = _packet(title)
    card = {"source_item_id": "archived", "why_it_matters": "需等量產與出貨驗證。"}
    assert companies(packet["news"][0], packet) == ""
    assert news_subject(card, packet)["name"] == ""
    assert article_is_tech(card, packet)
    lead = _news_line(card, packet).splitlines()[0]
    assert lead.startswith(f"**{title}**") and "**輝達（NVDA）**｜" not in lead


def test_two_named_expanders_remain_named_and_source_title_intact():
    title = "台光電、台燿海內外擴產 - UDN"
    packet = _packet(title)
    card = {"source_item_id": "archived", "why_it_matters": "投產時點待核。"}
    label = companies(packet["news"][0], packet)
    assert "台光電（2383）" in label and "台燿（6274）" in label
    assert title in _news_line(card, packet)


def test_subject_before_challenge_marker_is_not_dropped():
    assert companies({"title": "輝達挑戰超微，推出新晶片"},
                     {"tw_universe": []}).startswith("輝達（NVDA）")
    label = companies({"title": "超微挑戰輝達，推出新晶片"},
                      {"tw_universe": []})
    assert "超微（AMD）" in label and "輝達（NVDA）" not in label


def test_collaboration_is_not_a_challenge_target():
    assert companies({"title": "阿里與輝達合作開發 AI 服務"},
                     {"tw_universe": []}) == "輝達（NVDA）"


def test_unanchored_buy_verb_keeps_purchased_company_heading():
    title = "外資買進台積電 股價漲3%"
    packet = {"tw_universe": [{"code": "2330", "name": "台積電"}],
              "news": [{"source_item_id": "buy", "title": title}]}
    assert companies(packet["news"][0], packet) == "台積電（2330）"
    assert news_subject({"source_item_id": "buy"}, packet)["name"] == "台積電"


def test_noun_challenge_before_clause_break_keeps_actual_company_actor():
    title = "迎戰關稅挑戰 鴻海加速布局墨西哥"
    packet = {"tw_universe": [{"code": "2317", "name": "鴻海"}],
              "news": [{"source_item_id": "clause", "title": title}]}
    assert companies(packet["news"][0], packet) == "鴻海（2317）"
    assert news_subject({"source_item_id": "clause"}, packet)["name"] == "鴻海"
    for title in ("面臨挑戰 鴻海加速布局墨西哥", "迎接挑戰 鴻海布局墨西哥"):
        item = {"source_item_id": "clause", "title": title}
        variant_packet = {**packet, "news": [item]}
        assert companies(item, variant_packet) == "鴻海（2317）"
        assert news_subject({"source_item_id": "clause"}, variant_packet)["name"] == "鴻海"
    assert companies({"title": "AI 新挑戰 輝達股價重挫"},
                     {"tw_universe": []}) == "輝達（NVDA）"
    assert companies({"title": "迎戰關稅挑戰 Apple加速印度布局"},
                     {"tw_universe": []}).startswith("Apple（AAPL")
    cost_title = "通膨挑戰 Costco 財報 AI投資仍增"
    cost_packet = {"tw_universe": [], "news": [{"source_item_id": "cost",
                                              "title": cost_title, "entities": ["COST"]}]}
    cost_card = {"source_item_id": "cost"}
    assert news_subject(cost_card, cost_packet)["name"] == "COST"
    assert not article_is_tech(cost_card, cost_packet)
    tw_title = "通膨挑戰統一超 AI門市擴張"
    tw_packet = {"tw_universe": [{"code": "2912", "name": "統一超", "industry": "零售業"}],
                 "news": [{"source_item_id": "tw", "title": tw_title}]}
    tw_card = {"source_item_id": "tw"}
    assert companies(tw_packet["news"][0], tw_packet).startswith("統一超（2912")
    assert news_subject(tw_card, tw_packet)["name"] == "統一超"
    assert not article_is_tech(tw_card, tw_packet)


def test_geopolitical_rivalry_noun_before_clause_break_keeps_company_actor():
    packet = {"tw_universe": [{"code": "2330", "name": "台積電"}]}
    for title in ("美中對抗 台積電加速美國布局",
                  "中美抗衡 台積電擴產",
                  "科技戰超越 台積電提高研發支出"):
        assert companies({"title": title}, packet) == "台積電（2330）"
    assert companies({"title": "阿里對抗 台積電新製程"}, packet) == ""


def test_section_counts_without_packet_keeps_default_nontech_count():
    assert section_counts({"top_news_analysis": [{"source_item_id": "missing"}]}) == (0, 1)


def test_archived_rival_verb_and_parenthesized_ticker_stay_targets():
    for title in ("槓上輝達通用GPU？Google大咖首訪台叫陣",
                  "阿里推AI晶片挑戰輝達（NVDA）明年量產",
                  "阿里挑戰「輝達」（NVDA）明年量產",
                  "阿里挑戰 輝達推出新晶片"):
        packet = _packet(title)
        card = {"source_item_id": "archived", "why_it_matters": "出貨尚待驗證。"}
        assert companies(packet["news"][0], packet) == "", title
        assert news_subject(card, packet)["name"] == "", title


def test_unnamed_tech_actor_keeps_rival_industry_without_rival_heading():
    title = "小米力拚Apple 新機下月開賣"
    packet = {"tw_universe": [], "news": [{"source_item_id": "rival", "title": title,
                                           "entities": ["AAPL"]}]}
    card = {"source_item_id": "rival"}
    assert companies(packet["news"][0], packet) == ""
    assert news_subject(card, packet)["name"] == ""
    assert article_is_tech(card, packet)
    assert section_counts({"top_news_analysis": [card]}, packet) == (1, 0)


def test_rival_tech_source_counts_toward_feasible_coverage():
    packet = {"tw_universe": [], "news": [
        {"source_item_id": f"rival-{i}", "title": "小米力拚Apple 新機下月開賣",
         "entities": ["AAPL"]} for i in range(8)]}
    advisories = depth_advisories({"top_news_analysis": []}, packet)
    assert any("科技條目只有 0 則,而素材有 8 則" in note for note in advisories)


def test_tech_rivalry_with_macro_word_stays_in_tech_section():
    packet = _packet("阿里挑戰輝達 AI 晶片遇關稅")
    card = {"source_item_id": "archived"}
    assert news_subject(card, packet)["name"] == ""
    assert article_is_tech(card, packet)
    assert not is_macro(card, packet)
    assert section_counts({"top_news_analysis": [card]}, packet) == (1, 0)


def test_named_nontech_rival_stays_sector_even_with_macro_or_ai_words():
    title = "全聯挑戰統一超 通膨下搶客"
    packet = {"tw_universe": [{"code": "2912", "name": "統一超", "industry": "零售業"}],
              "news": [{"source_item_id": "retail", "title": title}]}
    card = {"source_item_id": "retail"}
    assert news_subject(card, packet)["name"] == ""
    assert not is_macro(card, packet)
    assert not article_is_tech(card, packet)
    assert section_counts({"top_news_analysis": [card]}, packet) == (0, 1)

    foreign = {"tw_universe": [], "news": [{"source_item_id": "costco",
                                                "title": "Temu挑戰Costco AI選品",
                                                "entities": ["COST"]}]}
    assert news_subject({"source_item_id": "costco"}, foreign)["name"] == ""
    assert not article_is_tech({"source_item_id": "costco"}, foreign)

    source_packet = {"tw_universe": packet["tw_universe"], "news": [
        {"source_item_id": f"retail-{i}", "title": title} for i in range(7)]}
    advisories = depth_advisories({"top_news_analysis": []}, source_packet)
    assert any("科技以外只有 0 則,而素材有 7 則" in note for note in advisories)
