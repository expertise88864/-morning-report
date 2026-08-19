# -*- coding: utf-8 -*-
"""**Commit E:信裡看得到;Commit F:2026-08-05 那封信不會再出現。**

E 的三個缺口都是同一個形狀:**schema 收了、驗證器擋了、渲染層一個字
都沒印**。那個必填只保護了 JSON,沒有保護讀者。

F 是黃金 fixture:用使用者實際抱怨的那封信的形狀當輸入,逐條斷言
新的規則會攔下它。**不是「看起來像」,是那幾條回饋逐字變成斷言。**
"""
import analysis_render as ar
import analysis_schema as sch
import event_score as es
import evidence_packet as ep
import fixtures_analysis as fx
import news_normalize as nn
import source_registry as sr


# ================================================================ Commit E

def _packet_with_events():
    news = [{"source_item_id": "n1", "title": "央行宣布調升存款準備率1碼",
             "summary": "新台幣升值0.3%", "entities": ["央行"],
             "source_name": "中央銀行", "official": True},
            {"source_item_id": "n2", "title": "台積電法說會下週登場",
             "entities": ["台積電"], "source_name": "經濟日報"}]
    return ep.build({}, {}, {}, news, [], {}, as_of="x",
                    target_session_date="y", sanitize=lambda s: s)


def test_the_top_three_carry_where_the_event_came_from():
    """「三個獨立媒體證實」與「僅一家、未經證實」先前在信裡長得一模一樣
    —— **可信度是分析的一部分,不是附註。**"""
    pk = _packet_with_events()
    obj = fx.valid_analysis()
    obj["key_drivers"][0]["cluster_id"] = "cluster:n1"
    text = ar.render(obj, pk)
    # 2026-08-18:「七之一」併進「九、今日市場關注與預測」,
    # 三大重點那一段的下界改成第八段的開頭。
    top = text[text.index("## 七、"):text.index("## " + ar.SECTION_NEWS)]
    # 2026-08-17 使用者定案:來歷從獨立一行收成句尾括號。判準不變 ——
    # 「官方公告」與「僅單一來源」在信裡仍然長得不一樣。
    assert "（官方公告" in top, top


def test_a_single_source_event_says_so_in_the_first_screen():
    pk = _packet_with_events()
    obj = fx.valid_analysis()
    obj["key_drivers"][0]["cluster_id"] = "cluster:n2"
    top = ar.render(obj, pk)
    assert "僅單一來源" in top or "未驗證" in top


def test_a_driver_without_a_cluster_still_renders():
    """非新聞的驅動因子(外資期貨部位)`cluster_id` 留空 —— 不得因此
    整條消失。**渲染層的降級是「少一行來歷」,不是「少一條重點」。**"""
    obj = fx.valid_analysis()
    obj["key_drivers"][0]["cluster_id"] = ""
    text = ar.render(obj, _packet_with_events())
    assert obj["key_drivers"][0]["statement"] in text





def test_the_net_effect_and_shared_driver_sections_are_retired():
    """「各標的合計影響」與共用驅動說明隨「今日市場關注與預測」被使用者
    整段刪掉(2026-08-19)。欄位仍在 schema 裡被要求與驗證;這條釘住
    它們**不再**出現在信裡。"""
    obj = fx.valid_analysis()
    obj["asset_net_effects"] = [
        {"asset_id": "2330", "net_direction": "bullish",
         "net_magnitude_band": "moderate", "offsetting_cluster_ids": ["c"],
         "why": "獨特的淨效果理由句", "claim_ids": ["c1"]}]
    obj["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "us_monetary", "cluster_ids": ["cluster:n1"],
         "why_not_double_counted": "獨特的不重複計權句"}]
    text = ar.render(obj, _packet_with_events())
    assert "獨特的淨效果理由句" not in text
    assert "獨特的不重複計權句" not in text
    assert "各標的合計影響" not in text


# ================================================================ Commit F
#
# **黃金 fixture:2026-08-05 那封信的形狀。** 使用者的七條回饋裡,
# 有三條是這一批的規則直接處理的;逐條變成斷言。

def _the_0805_shape():
    """那天信件第一段的實際內容形狀:兩則價格變化 + 一則 ADR 報價,
    來源全部是 Google News 聚合器帶回的同一批媒體。"""
    return [
        {"source_item_id": "g1", "title": "美股四大指數收紅 那斯達克漲1.2%",
         "summary": "科技股領漲", "entities": ["美股"],
         "source": "Google:美股", "source_name": "經濟日報"},
        {"source_item_id": "g2", "title": "台積電ADR收跌0.4%",
         "summary": "", "entities": ["台積電"],
         "source": "Google:2330", "source_name": "聯合報"},
        {"source_item_id": "g3", "title": "費城半導體指數收漲2.1%",
         "summary": "", "entities": ["費半"],
         "source": "Google:費半", "source_name": "鉅亨網"},
        # 同一天真正的事件 —— 三家**真的獨立**的媒體同時報導
        {"source_item_id": "g4", "title": "美國7月非農就業新增18.5萬人 低於預期",
         "summary": "失業率升至4.3%,市場預期聯準會9月降息機率升高",
         "entities": ["美國"], "source": "Google:非農", "source_name": "Reuters"},
        {"source_item_id": "g5", "title": "美國就業數據不如預期 失業率升至4.3%",
         "summary": "市場反映降息預期", "entities": ["美國"],
         "source": "Google:非農", "source_name": "日經"},
        {"source_item_id": "g6", "title": "美7月就業增幅低於預期 失業率走高",
         "summary": "降息預期升溫", "entities": ["美國"],
         "source": "Google:非農", "source_name": "自由時報"},
    ]


def test_0805_the_price_moves_cannot_be_the_top_three():
    """**使用者原話:「我要的是真正國際上昨夜三大發生得重大事件,
    而不是數據文字堆疊」。**"""
    news = _the_0805_shape()
    pk = ep.build({}, {}, {}, news, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=lambda s: s)
    te = pk["top_events"]
    excluded = set(te["excluded_price_moves"])
    assert len(excluded) == 3, te                # 三則價格文全部排除
    assert not (set(te["top_cluster_ids"]) & excluded)
    assert te["top_cluster_ids"], "把價格文排掉之後就沒有事件了"


def test_0805_three_publishers_survive_the_near_duplicate_pass():
    """**Commit B 的生產缺陷,由這個 fixture 抓出來。**

    上一版正規化把 `source_name` 丟掉、近似去重又拿聚合器別名
    (`Google:非農`)當「同一家」的鍵 —— 於是同一個 Google 查詢帶回的
    三家媒體被判成「同一家改版重發」,砍掉兩則。獨立來源數在生產
    因此**永遠是 1**,而單元測試全綠(它們直接餵 `source_name` 給
    `clusters()`,繞過了正規化)。
    """
    kept, _, info = nn.normalize_news(_the_0805_shape(), lambda s: s)
    ids = {k["source_item_id"] for k in kept}
    assert {"g4", "g5", "g6"} <= ids, sorted(ids)
    assert all(k.get("source_name") for k in kept), "發布者身分又被丟掉了"


def test_0805_same_event_different_wording_still_splits_KNOWN_GAP():
    """**已知缺口,刻意記在這裡。**

    三則講同一件事(非農)的中文標題,詞彙重疊只有 0.25–0.33,而
    `news_clusters.TITLE_OVERLAP` 是 0.5 —— 它們不會併成一群。
    模組自己量過的分佈是:同語言同事件 0.69/0.90、**跨語言同事件 0.33**、
    不同事件同主體 0.18。這三則落在「刻意接受的漏併」那一區。

    降門檻到 0.3 會讓「不同事件同主體」只剩 0.12 的間距 —— **誤併比
    漏併危險**,所以這一批不動它;正解是另加一條不靠詞彙重疊的路徑
    (共同數字指紋 / 事件類型),那要獨立一批做並重新量分佈。

    **這條測試釘住現況**:有人修好它時這裡會紅,而那應該是一個刻意的
    決定,不是順手改個常數。
    """
    _, _, info = nn.normalize_news(_the_0805_shape(), lambda s: s)
    jobs = [c for c in info["clusters"] if "g4" in c["member_source_ids"]][0]
    assert len(jobs["member_source_ids"]) == 1, (
        "非農三則併起來了 —— 若這是刻意修好的,請更新這條測試與 "
        "`news_clusters` 的門檻說明,並重新量誤併率")


def test_0805_the_aggregator_alias_is_not_a_publisher():
    """`Google:非農` 不是發布者 —— 它是我們自己的查詢代號。"""
    assert sr.is_aggregator("Google:非農")
    assert sr.owner_of("Google:非農") == ""
    assert sr.owner_of_item({"source": "Google:非農",
                             "source_name": "Reuters"}) == "wire:Reuters"


def test_0805_the_macro_release_is_detected():
    """非農是那天真正的分岔 —— 情境樹要條件在它上面。"""
    news = _the_0805_shape()
    pk = ep.build({}, {}, {}, news, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=lambda s: s)
    assert pk["event_graph"]["macro_release_cluster_id"], pk["event_graph"]


def test_0805_a_report_shaped_like_that_day_is_rejected():
    """**整合驗收**:一份把三則價格變化當成三大重點的分析,
    在今天的驗證器下擋得下來。"""
    news = _the_0805_shape()
    pk = ep.build({}, {}, {}, news, [], {}, as_of="2026-08-05T06:00",
                  target_session_date="2026-08-05", sanitize=lambda s: s)
    obj = fx.valid_analysis()
    base = obj["key_drivers"][0]
    obj["key_drivers"] = [dict(base, cluster_id=c) for c in
                          sorted(pk["top_events"]["excluded_price_moves"])[:3]]
    problems = sch.validate(obj, pk)
    assert [p for p in problems if "純價格變化" in p], problems[:3]
    assert [p for p in problems if "沒有指向任何真正的事件群" in p], problems[:3]


def test_0805_price_move_detection_is_not_over_broad():
    """**誤殺比漏放危險。** 那天真正的事件標題不得被當成價格文。"""
    for t in ("美國7月非農就業新增18.5萬人 低於預期",
              "美國就業數據不如預期 失業率升至4.3%",
              "美7月就業增幅低於預期 失業率走高"):
        assert not es.is_price_move(t), t
