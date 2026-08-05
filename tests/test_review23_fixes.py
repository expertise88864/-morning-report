# -*- coding: utf-8 -*-
"""**第二十三輪:每個修法都再被抓出一個繞法。**

共同形狀:單元測試餵的是乾淨的形狀,生產走的是另一條路 ——
gate 量錯鍵、空計畫走錯分支、短別名裸比對、身分沒涵蓋新段落。
"""
import json

import analysis_depth as ad
import event_graph as eg
import evidence_packet as ep
import fixtures_analysis as fx
import payload_budget as pb
import source_registry as sr


def test_removing_the_schema_changes_the_measured_chars_by_its_size():
    """**P1-2 的驗收就是外審寫的那條**:拿掉 schema 後的差值要等於
    schema 序列化大小。"""
    schema = {"type": "object", "pad": "x" * 5000}
    base = {"developer_instructions": "a" * 100, "user_payload": "b" * 100}
    m1, m2 = {}, {}
    pb.request_gate(dict(base, response_schema=schema), manifest=m1)
    pb.request_gate(dict(base, response_schema={}), manifest=m2)
    got1 = m1["llm"]["payload_budget"]["final_request_chars"]
    got2 = m2["llm"]["payload_budget"]["final_request_chars"]
    assert got1 - got2 == (len(json.dumps(schema, ensure_ascii=False))
                           - len("{}")), (got1, got2)


def test_the_real_bundle_schema_is_counted():
    """生產 bundle 的 `structured_output` 是布林、schema 在
    `response_schema`(32K)—— gate 量的要是後者。"""
    import prompt_profiles as pp
    pk = ep.build({}, {}, {}, fx.news(), [], {}, as_of="x",
                  target_session_date="y", sanitize=lambda s: s)
    b = pp.build_luna_bundle(pk)
    assert b.get("structured_output") is True
    m = {}
    pb.request_gate(b, manifest=m)
    schema_len = len(json.dumps(b["response_schema"], ensure_ascii=False))
    assert schema_len > 20_000
    assert m["llm"]["payload_budget"]["final_request_chars"] >= schema_len


def test_an_empty_plan_makes_zero_fetches_and_never_falls_back(monkeypatch):
    """**P1-3:`[]` 是合法計畫,不是「沒有計畫」。** 空清單時
    `_fetch_one_fulltext` 呼叫數必須是 0,不得落回逐文章掃描。"""
    import morning_report as mr
    calls = []
    monkeypatch.setattr(mr, "_fetch_one_fulltext",
                        lambda n, timeout, limit: calls.append(n) or False)
    news = [{"source_item_id": "a", "importance": "critical",
             "link": "http://x", "title": "t"}]
    mr.fetch_news_fulltext(news, targets=[])
    assert calls == [], "空計畫落回了逐文章掃描"
    mr.fetch_news_fulltext(news, targets=None)      # None 才走舊路徑
    assert len(calls) == 1


def test_softbank_is_not_the_financial_times():
    """**P1-4:短 ASCII 別名要 token 邊界。**"""
    assert sr.owner_of("SoftBank Group") == ""
    assert sr.owner_of("Microsoft") == ""
    assert sr.owner_of("FT") == "ft"
    assert sr.owner_of("Financial Times") == "ft"
    assert sr.owner_of("ft.com") == "ft"            # 邊界是標點,合法命中
    assert sr.owner_of("中時新聞網") == "chinatimes"  # 中文子字串不受影響


def test_three_articles_from_one_unknown_site_are_one_potential_source():
    """**P2-5:未知來源以發布者字串去重**,不數文章。"""
    out = sr.independence([{"source_name": "某小站"}] * 3)
    assert out["unverified"] == 1 and out["potential"] == 1
    two = sr.independence([{"source_name": "甲站"}, {"source_name": "乙站"}])
    assert two["potential"] == 2


def test_2330_and_taiwan_semiconductor_are_the_same_asset():
    """**P1-7:「2330 bullish」與「台積電 bearish」是同一個標的的衝突。**"""
    got = eg.conflicting_assets({"top_news_analysis": [
        {"source_item_id": "n1",
         "affected_assets": [{"asset_id": "2330", "direction": "bullish"}]},
        {"source_item_id": "n2",
         "affected_assets": [{"asset_id": "台積電", "direction": "bearish"}]}]})
    assert got == {"台積電": ["n1", "n2"]}


def test_a_foreign_central_bank_is_not_taiwan_policy():
    """**P1-8:「日本央行升息」不是 tw_policy 也不是 fed_policy。**"""
    assert eg.driver_of("日本央行升息") == "foreign_cb"
    assert eg.driver_of("歐洲央行決議降息") == "foreign_cb"
    assert eg.driver_of("央行理監事會決議調升存準率") == "tw_policy"


def test_award_is_not_a_war():
    """ASCII 驅動詞要 token 邊界。"""
    assert eg.driver_of("firm wins award for design") == ""
    assert eg.driver_of("border war escalates") == "geopolitics"


def test_all_macro_releases_are_listed_not_just_one():
    news = [{"source_item_id": "m1", "title": "美國7月CPI年增2.9% 高於預期",
             "summary": "", "entities": ["物價"], "source_name": "Reuters"},
            {"source_item_id": "m2", "title": "Fed 利率決議按兵不動",
             "summary": "", "entities": ["聯準會"], "source_name": "CNBC"}]
    import news_clusters as nc
    g = eg.build(nc.clusters(news), news)
    assert set(g["macro_release_cluster_ids"]) == {"cluster:m1", "cluster:m2"}
    assert g["macro_release_cluster_id"] in g["macro_release_cluster_ids"]


def test_deepen_cannot_rewrite_a_key_driver():
    """**P1-9:首屏三條在加深身分裡。** 改寫 statement / 換 cluster /
    翻方向,任何一格都是「弄丟了三大重點」。"""
    before = fx.valid_analysis()
    for field, val in (("statement", "換一句話"), ("cluster_id", "cluster:x"),
                       ("direction", "bearish")):
        after = fx.valid_analysis()
        after["key_drivers"][0][field] = val
        lost = ad._identity(before)["三大重點"] - ad._identity(after)["三大重點"]
        assert lost, f"改 {field} 沒有被身分看見"


def test_deepen_cannot_drop_net_effects_or_shared_notes():
    before = fx.valid_analysis()
    before["asset_net_effects"] = [{"asset_id": "2330",
                                    "net_direction": "bullish",
                                    "net_magnitude_band": "small",
                                    "offsetting_cluster_ids": [], "why": "w",
                                    "claim_ids": []}]
    before["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "us_monetary", "cluster_ids": ["a", "b"],
         "why_not_double_counted": "只計一次"}]
    after = fx.valid_analysis()          # 兩個都空
    ib, ia = ad._identity(before), ad._identity(after)
    assert ib["逐標的淨效果"] - ia["逐標的淨效果"], "刪掉淨效果沒被看見"
    assert ib["共同驅動說明"] - ia["共同驅動說明"], "刪掉共同驅動說明沒被看見"


def test_aggregator_only_says_unresolved_not_single_source():
    """**P2-4:「僅單一來源」與「原始發布者未解析」是兩種可信度。**"""
    import analysis_render as ar
    line = ar._event_card(
        dict(fx.valid_analysis()["key_drivers"][0], cluster_id="cluster:g"),
        {"news_clusters": {"clusters": [
            {"cluster_id": "cluster:g", "official": False,
             "independent_sources": 0, "unverified_sources": 0,
             "aggregator_only_sources": 2, "continuing_days": 0}]}})
    assert "原始發布者未解析" in line
    assert "僅單一來源" not in line
