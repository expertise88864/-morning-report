"""使用者 2026-10-29:「太多科技類股新聞,常常是買什麼設備、增資等等;有沒有更大條更全面、
科技業之外的」。四項:例行公告不上卡、科技 6 則、大條優先、台灣經濟與產業。"""
import datetime as dt

import announcement_filter as af
import editorial_priority as ep
import news_display_quality as q
import reader_selection as rs

TODAY = dt.date(2026, 10, 29)


def test_routine_small_announcements_do_not_become_news():
    caps = {"3711": 1.0e12, "8046": 2.0e11}
    small = {"code": "3711", "title": "代子公司日月光半導體公告取得營業用機器設備",
             "summary": "交易總金額:新台幣1,520,000,000元"}            # 0.15% 市值
    big = {"code": "8046", "title": "公告本公司取得機器設備", "summary": "交易總金額:新台幣9,045,093,013元"}  # 4.5%
    assert af.routine(small, caps) and not af.routine(big, caps)
    for keep in ("公告本公司115年第3季合併財務報告業經董事會通過", "代子公司中龍鋼鐵公司公告總經理變動",
                 "公告本公司115年09月份自結合併營收及稅前損益",
                 "代子公司環旭電子(股)公司公告董事會決議出售子公司 Asteelflash USA Corp.特定業務及相關資產等"):
        assert not af.routine({"code": "3711", "title": keep}, caps), keep
    for drop in ("本公司受邀參加高盛證券舉辦之線上法人座談會", "代子公司中龍鋼鐵公司公告發言人異動",
                 "公告本公司限制員工權利新股註銷完成變更登記事宜",
                 "代子公司中國信託產物保險股份有限公司公告向關係人處分使用 權資產",
                 "本公司董事會決議增資100%轉投資子公司詮鼎科技(股)公司"):
        assert af.routine({"code": "3711", "title": drop}, caps), drop
    # 查不到市值時改用 50 億的絕對門檻
    assert af.routine({"code": "9999", "title": "公告本公司取得機器設備", "summary": "新台幣3億元"}, {})
    assert not af.routine({"code": "9999", "title": "公告本公司取得機器設備", "summary": "約新台幣509億元"}, {})
    assert af.amount_twd("交易總金額:美元100,000,000元") == 3.2e9


def test_routine_announcements_are_removed_at_the_shared_entry():
    small = {"code": "3711", "title": "代子公司日月光半導體公告取得廠務工程", "summary": "新台幣2億元"}
    research = [dict(small, source="MOPS", entities=["3711"]), {"title": "Fed 維持利率不變 - CNBC"}]
    universe = [{"code": "3711", "market_cap": 1.0e12}]
    _, quotes = q.current_inputs([], {"TW_MOPS": [small], "NEWS_RESEARCH_SOURCES": research}, TODAY, universe)
    assert quotes["TW_MOPS"] == []
    assert [n["title"] for n in quotes["NEWS_RESEARCH_SOURCES"]] == ["Fed 維持利率不變 - CNBC"]


def test_tech_takes_six_slots_and_other_sectors_ten():
    news = [{"source_item_id": f"t{i}", "title": f"台積擴產第{i}案"} for i in range(10)]
    news += [{"source_item_id": f"o{i}", "title": f"航運運價第{i}案"} for i in range(12)]
    cards = [{"source_item_id": n["source_item_id"], "why_it_matters": "有新的需求"} for n in news]
    selected, _ = rs.select_cards(cards, {"news": news})
    tech = sum(rs.article_is_tech(c, {"news": news}) for c in selected)
    assert tech == rs.FRONT_LIMIT_TECH == 6 and len(selected) - tech == rs.FRONT_LIMIT_OTHER == 10


def test_a_story_carried_by_more_outlets_comes_first():
    news = [{"source_item_id": "a", "title": "台積擴產第一案", "source_name": "鉅亨"},
            {"source_item_id": "b", "title": "台積擴產第二案", "source_name": "經濟日報"},
            {"source_item_id": "b2", "title": "台積擴產第二案(轉載)", "source_name": "工商時報"},
            {"source_item_id": "b3", "title": "台積擴產第二案(轉載)", "source_name": "中央社"}]
    packet = {"news": news, "news_clusters": {"clusters": [
        {"cluster_id": "c:a", "member_source_ids": ["a"]},
        {"cluster_id": "c:b", "member_source_ids": ["b", "b2", "b3"]}]}}
    ordered = ep.order([{"source_item_id": "a"}, {"source_item_id": "b"}], packet)
    assert [c["source_item_id"] for c in ordered] == ["b", "a"]


def test_a_taiwan_economy_card_is_reserved_in_section_nine():
    news = [{"source_item_id": f"f{i}", "title": f"航運運價第{i}案"} for i in range(12)]
    news.append({"source_item_id": "x", "title": "9月外銷訂單年增三成 連六紅"})
    cards = [{"source_item_id": n["source_item_id"], "why_it_matters": "有新的需求"} for n in news]
    selected, _ = rs.select_cards(cards, {"news": news})
    assert "x" in {c["source_item_id"] for c in selected}
