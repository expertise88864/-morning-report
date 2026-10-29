"""2026-10-29 實信:颱風舊聞進了世界大事;特化修補把事件群自家新聞漏給模型、四輪落 legacy。"""
import datetime as dt

import morning_report as mr
import news_display_quality as q
import repair_contract_context as rcc

TODAY = dt.date(2026, 10, 29)


def test_a_headline_date_days_ago_marks_a_reindexed_old_story():
    """「薔琵颱風24日颱風假資訊…」RSS 發布時間是 10/28,標題寫的是 10/24。"""
    assert q.stale_title_date("薔琵颱風24日颱風假資訊，全台停班課一次看 - 天下雜誌", TODAY)
    assert q.stale_title_date("10月20日美伊會談破局", TODAY)
    for fresh in ("台積電28日法說會釋出樂觀展望", "台股26日休市", "聯發科30日法說",
                  "外資近5日賣超", "股價站上20日均線", "11月24日將舉行投票", "1月3日開始交易",
                  "颱風農損2.6億元"):
        assert not q.stale_title_date(fresh, TODAY), fresh


def test_the_shared_entry_drops_stale_and_forum_items():
    news = [{"title": "薔琵颱風24日颱風假資訊 - 天下雜誌"},
            {"title": "2891 中信金 - 貼文 - 股市爆料同學會 - CMoney"},
            {"title": "Fed 維持利率不變 - CNBC"}]
    assert [n["title"] for n in q.without_forum_posts(news, TODAY)] == ["Fed 維持利率不變 - CNBC"]
    assert len(q.without_forum_posts(news)) == 2   # 沒給日期就只濾討論區


def test_a_named_event_group_brings_its_own_news_into_the_repair():
    """問題要求分析或駁回 cluster:n1234…,修補切片就要附上那群的新聞。"""
    packet = {"news_clusters": {"clusters": [
        {"cluster_id": "cluster:n1234", "member_source_ids": ["n1234", "n5678", "nX"]},
        {"cluster_id": "cluster:other", "member_source_ids": ["n9"]}]}}
    problems = ["本報要求分析的事件 cluster:n1234 既沒有分析、也沒有說為什麼不談"]
    assert rcc.cluster_members(problems, packet, ["n1234", "n5678", "n9"], []) == ["n1234", "n5678"]
    assert rcc.cluster_members(problems, packet, ["n1234", "n5678"], ["n1234"]) == ["n5678"]


def test_the_repair_slice_shows_the_named_groups_news(monkeypatch):
    """端到端:前一版引用了 150 則新聞(第 1、2 層名額吃滿),被點名事件群的新聞仍要看得到。"""
    import json
    import llm_postprocess as lp
    news = [{"source_item_id": f"n{i:04d}", "title": f"標題{i}" * 6, "summary": f"摘要{i}" * 12,
             "entities": ["2330"]} for i in range(400)]
    packet = {"news": news, "market": {"TAIEX": {"close": 45224.29}},
              "news_clusters": {"clusters": [{"cluster_id": "cluster:n0399",
                                              "member_source_ids": ["n0399"]}]}}
    ids = [n["source_item_id"] for n in news]
    monkeypatch.setattr(mr._ep, "evidence_ids", lambda p: set(ids))
    prev = json.dumps({"cited": ids[:150]}, ensure_ascii=False)
    problems = ["本報要求分析的事件 cluster:n0399 既沒有分析、也沒有說為什麼不談"]
    tail = lp.repair_instruction(problems, [], previous_json=prev)
    _, rec = mr._repair_request_payload({"model": "m"}, "x" * mr._pb.MAX_REQUEST_CHARS, tail, packet,
                                        problems=problems, hints=[])
    assert rec and rec["mode"] == "evidence_slice", rec
    assert "n0399" in rec["visible_ids"], sorted(rec["visible_ids"])[-5:]
