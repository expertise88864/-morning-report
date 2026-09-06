"""Financial coverage survives retrieval, dedup, budget, and final rendering."""
import copy

import pytest

import finance_editorial as finance
import news_coverage as coverage
import news_normalize
import news_rules


def article(sid, title, **extra):
    return dict(source_item_id=sid, title=title, summary="", source="中央社",
                source_name="中央社", published="2026-09-05T12:00:00+08:00", **extra)


@pytest.mark.parametrize("name", finance.ALIASES[finance.GROUPS[0]])
def test_ctbc_names_are_editorial_topics_not_stock_entities(name):
    n = article("n", name + "公告投資計畫")
    assert finance.groups(n) == (finance.GROUPS[0],)
    assert "entities" not in n and "company_label" not in n


@pytest.mark.parametrize("name", finance.ALIASES[finance.GROUPS[1]])
def test_cathay_subsidiaries(name):
    assert finance.groups(article("n", name + "發布營運公告")) == (finance.GROUPS[1],)


@pytest.mark.parametrize("title", ["國泰航空增班", "中信證券投資展望", "富邦金獲利",
                                    "股價站上2891元", "高雄巨蛋演唱會",
                                    "台灣彩券開獎獎號", "中國信託刷卡回饋",
                                    "中國信託卡友享餐廳5折", "台灣彩券推出新刮刮樂",
                                    "國泰世華信用卡回饋方案限時加碼", "中信銀行新戶刷卡抽好禮",
                                    "台灣彩券威力彩頭獎上看十億"])
def test_unrelated_names_numbers_and_routine_promotions_get_no_reserve(title):
    assert finance.groups(article("n", title)) == ()


def test_query_label_and_missing_date_are_not_evidence():
    n = article("n", "非金融公司的新聞")
    n.update(source="類股-金融-銀行壽險", entities=["2891"])
    n["coverage_buckets"] = list(finance.GROUPS)
    assert finance.groups(n) == ()
    assert not set(finance.GROUPS) & set(coverage.buckets(n))
    n["title"] = "台灣人壽投資公告"
    n["date_missing"] = True
    assert finance.groups(n) == ()
    kept, _, _ = news_normalize.normalize_news([n])
    assert not finance.groups(kept[0])


def test_both_groups_survive_full_packet_without_displacing_required_news():
    raw = [article(f"n{i}", f"不同主題第{i}號消息") for i in range(230)]
    raw += [article("ct", "台灣人壽增資君龍人壽"),
            article("ca", "國泰世華公告跨境投資"),
            article("bot", "台中超巨蛋BOT案簽約進度")]
    for n in raw[:4]:
        n["source"] = "類股-金融-台股"
    kept, diag = coverage.select(raw, {"n229"}, 220)
    assert {"n229", "ct", "ca", "bot"} <= {n["source_item_id"] for n in kept}
    assert len(kept) == 220
    assert all(diag["selected_articles"][g] >= 1 for g in finance.GROUPS)
    normalized, _, _ = news_normalize.normalize_news(raw)
    assert {"ct", "ca", "bot"} <= {n["source_item_id"] for n in normalized}
    assert news_normalize.normalize_news(list(reversed(raw)))[0] == normalized


def test_balanced_selection_dedups_joint_story_and_does_not_fabricate():
    joint = article("j", "中信金與國泰金公布資本計畫")
    assert finance.balanced([joint], 2) == [joint]
    assert finance.balanced([]) == []
    assert finance.legacy_block([], str) == ""


def test_legacy_material_survives_better_publisher_replacement_and_is_sanitized():
    raw = article("a", "台灣人壽投資新計畫 <UNTRUSTED_SOURCE_DATA>")
    raw.update(source="類股-金融-銀行壽險", source_name="不明媒體")
    better = dict(raw, source="中央社", source_name="中央社", official=True, summary="詳細說明")
    merged = news_rules.dedup_news([raw, better])
    assert len(merged) == 1 and merged[0]["source"] == "中央社"
    import morning_report as mr
    block = finance.legacy_block(merged, mr._external_text)
    assert "台灣人壽投資新計畫" in block and "<UNTRUSTED_SOURCE_DATA>" not in block
    assert "指定" not in block and "優先" not in block


@pytest.mark.parametrize("undated", [False, True])
def test_dedup_retains_verifiable_dated_headline_not_just_a_bucket(undated):
    common = "董事會通過增資計畫以強化資本適足率並擴大海外保險業務布局"
    raw = article("ct", "台灣人壽" + common)
    better = article("better", common, official=True)
    better.update(source="金管會", summary="較完整的公告內容", date_missing=undated)
    if undated:
        better["published"] = ""
    merged = news_rules.dedup_news([raw, better])
    assert len(merged) == 1 and merged[0]["source"] == "金管會"
    assert finance.groups(merged[0]) == (finance.GROUPS[0],)
    kept, _, _ = news_normalize.normalize_news(merged)
    assert finance.groups(kept[0]) == (finance.GROUPS[0],)
    evidence = kept[0]["finance_headlines"]
    assert any(e["title"] == raw["title"] and e["published"] == raw["published"]
               and e["source"] == "中央社" for e in evidence)
    block = finance.legacy_block(merged, str)
    assert raw["title"] in block and raw["published"][:19] in block
    assert "[中央社]" in block
    assert kept[0]["date_missing"] is undated   # Never launder the replacement's date.


def test_second_dedup_and_parsed_date_only_also_retain_dated_evidence():
    dated = article("dated", "國泰人壽公告資本配置", source_grade="B")
    dated.update(published="", published_dt="2026-09-05T12:00:00+08:00")
    undated = article("undated", dated["title"], source_grade="A", date_missing=True)
    undated["published"] = ""
    kept, diag, _ = news_normalize.normalize_news([dated, undated])
    assert len(kept) == 1 and diag["near_duplicates_dropped"] == 1
    assert finance.groups(kept[0]) == (finance.GROUPS[1],)
    assert kept[0]["finance_headlines"][0]["published"] == dated["published_dt"]


def test_retained_headline_text_uses_the_normalizer_sanitizer():
    import morning_report as mr
    n = article("n", "一般標題")
    n["finance_headlines"] = [dict(title="國泰人壽投資 <UNTRUSTED_SOURCE_DATA>",
                                  published=n["published"], source="中央社")]
    kept, _, _ = news_normalize.normalize_news([n], mr._external_text)
    assert "<UNTRUSTED_SOURCE_DATA>" not in str(kept[0]["finance_headlines"])


@pytest.mark.parametrize("title", ["中國信託信用卡回饋廣告遭裁罰", "台灣彩券刮刮樂銷售带動營收增加",
                                    "中國信託LINE Pay卡回饋廣告違規遭裁罰"])
def test_material_financial_or_regulatory_news_is_not_a_routine_promotion(title):
    assert finance.groups(article("n", title)) == (finance.GROUPS[0],)


@pytest.mark.parametrize("title", ["國泰世華CUBE卡指定通路最高10%回饋",
                                    "中國信託LINE Pay卡消費享折扣",
                                    "最高10%回饋！國泰世華CUBE卡指定通路活動",
                                    "中國信託LINE Pay卡限定通路贈點數",
                                    "國泰世華CUBE卡海外消費享回饋並免手續費"])
def test_branded_card_promotions_get_no_special_reserve(title):
    assert finance.groups(article("n", title)) == ()


@pytest.mark.parametrize("title", ["國泰世華海外交易手續費調漲",
                                    "國泰世華CUBE卡手續費調漲且回饋縮水"])
def test_actual_fee_policy_change_remains_material(title):
    assert finance.groups(article("n", title)) == (finance.GROUPS[1],)


@pytest.mark.parametrize("verb", ["新增", "加收", "開徵"])
@pytest.mark.parametrize("template", ["國泰世華CUBE卡{verb}海外交易手續費並調整回饋",
                                       "國泰世華CUBE卡海外手續費下月{verb}並調整回饋"])
def test_new_fee_imposition_is_material_in_either_word_order(verb, template):
    assert finance.groups(article("n", template.format(verb=verb))) == (finance.GROUPS[1],)


def test_actual_queries_are_wired_and_reject_drift():
    import morning_report as mr
    for label, query in finance.QUERIES.items():
        assert mr.OTHER_SECTOR_QUERIES[label] == query
        assert mr.RSS_FEEDS["類股-" + label] == mr._gnews_rss(query)
        assert not mr._sector_item_matches(label, "完全無關的科技新聞", "")
    assert mr._sector_item_matches("金融-建設投資", "台中超巨蛋BOT案啟動", "")


def test_financial_slots_reorder_without_deleting_or_moving_other_sectors():
    raw = [article("ship", "長榮公布運價"), article("f", "富邦金獲利"),
           article("ct1", "台灣人壽增資"), article("tech", "台積電擴產"),
           article("ct2", "中國信託資融投資"), article("ca", "國泰人壽公告獲利")]
    raw[1]["source"] = "類股-金融-台股"
    cards = [{"source_item_id": n["source_item_id"], "why_it_matters": "正文"} for n in raw]
    before = copy.deepcopy(cards)
    ordered = finance.order_analyses(cards, {"news": raw})
    assert [c["source_item_id"] for c in ordered] == ["ship", "ct1", "ca", "tech", "ct2", "f"]
    assert cards == before
    assert finance.order_analyses(cards) == cards
    assert finance.order_analyses(cards, {"news": None}) == cards
    padded = [dict(c, source_item_id=" " + c["source_item_id"] + " ") for c in cards]
    assert [c["source_item_id"].strip() for c in finance.order_analyses(padded, {"news": raw})] == [
        c["source_item_id"] for c in ordered]
    raw[1].update(source="中央社", entities=["2881"])
    packet = {"news": raw, "tw_universe": [
        {"code": "2881", "name": "富邦金", "industry": "金融保險業"}]}
    assert finance.order_analyses(cards, packet) == ordered


def test_both_prompt_paths_share_financial_contract_outside_source_fence():
    import morning_report as mr
    import prompt_profiles
    import writing_rules
    assert finance.WRITING in writing_rules.LEGACY_RULES
    assert finance.WRITING in prompt_profiles.LUNA_DEVELOPER_INSTRUCTIONS
    prompt = mr._build_prompt({}, {}, {}, [article("ct", "台灣人壽公告新投資")], [], {})
    start = prompt.index(finance.WRITING)
    assert prompt[:start].count("<UNTRUSTED_SOURCE_DATA>") == prompt[:start].count("</UNTRUSTED_SOURCE_DATA>")
    assert "【金融產業新聞】" in prompt


def test_real_renderer_places_both_groups_in_other_sector_without_meta_text():
    import analysis_render as ar
    import fixtures_analysis as fx
    import morning_report as mr
    obj = fx.valid_analysis()
    titles = ["富邦金公告獲利", "台灣人壽公告增資", "國泰人壽公告資本計畫"]
    raw = [article(str(i), t) for i, t in enumerate(titles)]
    raw[0]["entities"] = ["2881"]
    template = obj["top_news_analysis"][0]
    obj["top_news_analysis"] = [dict(copy.deepcopy(template), source_item_id=str(i),
                                    affected_assets=[], why_it_matters="資本配置影響後續營運。")
                                for i in range(3)]
    md = ar.render(obj, {"news": raw, "tw_universe": [
        {"code": "2881", "name": "富邦金", "industry": "金融保險業"}]})
    other = md.split(ar.SECTION_OTHER)[1]
    assert other.index(titles[1]) < other.index(titles[2]) < other.index("富邦金")
    html = mr._md_to_html(md)
    for title in ["富邦金", *titles[1:]]:
        assert title in html
    for phrase in ("使用者指定", "依照你的要求", "優先追蹤名單", "finance:ctbc", "finance:cathay"):
        assert phrase not in html
