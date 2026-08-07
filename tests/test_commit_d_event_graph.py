# -*- coding: utf-8 -*-
"""**Commit D:事件之間的關係。**

三件事,都是「兩段各自寫完就結束了」的變體:

  * 同一個標的被推往相反方向 → 讀者要的是**合起來是利多還是利空**;
  * 三則新聞其實是同一個底層驅動 → 各加一次權重 = 同一件事說三次;
  * 總經發布是情境樹的**分岔本身** → 三個分支要條件在同一件事上。
"""
import analysis_schema as sch
import event_graph as eg
import evidence_packet as ep
import fixtures_analysis as fx
import news_clusters as nc


def _macro_news():
    return [{"source_item_id": "m1",
             "title": "美國7月非農就業新增18.5萬人 低於預期",
             "summary": "失業率升至4.3%", "entities": ["美國"],
             "source_name": "Reuters"},
            {"source_item_id": "m2", "title": "Fed 官員鴿派發言 暗示9月降息",
             "summary": "", "entities": ["聯準會"], "source_name": "CNBC"},
            {"source_item_id": "m3", "title": "台積電熊本廠恢復產線運作",
             "summary": "", "entities": ["台積電"], "source_name": "經濟日報"}]


def _packet():
    news = _macro_news()
    return ep.build({}, {}, {}, news, [], {}, as_of="x",
                    target_session_date="y", sanitize=lambda s: s)


# ---------------------------------------------------------------- 共同驅動

def test_the_same_macro_driver_shows_up_as_one_group():
    """就業數據 → 降息預期 是**同一條傳導鏈上的兩個位置**,不是兩個
    獨立確認。它們的驅動代號不同(`us_labor` / `fed_policy`),
    家族相同(`us_monetary`)。"""
    news = _macro_news()
    g = eg.build(nc.clusters(news), news)
    groups = {x["driver"]: set(x["cluster_ids"]) for x in g["shared_driver_groups"]}
    assert "us_monetary" in groups
    assert groups["us_monetary"] == {"cluster:m1", "cluster:m2"}


def test_an_unrelated_event_is_not_pulled_into_the_group():
    """**誤歸類會讓一個真的獨立訊號被當成重複計權而消失** ——
    台積電熊本廠與總經無關,不得被吸進去。"""
    news = _macro_news()
    g = eg.build(nc.clusters(news), news)
    inside = {c for x in g["shared_driver_groups"] for c in x["cluster_ids"]}
    assert "cluster:m3" not in inside


def test_a_single_cluster_is_not_a_shared_driver_group():
    """一群自己一個驅動不構成重複計權的風險。"""
    news = [_macro_news()[0], _macro_news()[2]]
    g = eg.build(nc.clusters(news), news)
    assert g["shared_driver_groups"] == []


def test_the_longest_keyword_wins():
    """**同一段文字命中兩個驅動時,取比對長度最長的那個。**

    反例要只靠這條規則分勝負:下面這句同時含「升息」(`fed_policy`,
    兩個字,在表裡排第一)與「出口管制」(`export_control`,四個字,
    排第六)。取最長 → export_control;取先命中 → fed_policy。
    這句的主題顯然是出口管制,升息只是背景。
    """
    both = "美國升息預期升溫之際 商務部宣布新一輪半導體出口管制"
    assert eg.driver_of(both) == "export_control"
    assert eg.driver_of("台積電熊本廠恢復產線") == ""


def test_using_two_events_from_one_driver_requires_an_explanation():
    pk = _packet()
    obj = fx.valid_analysis()
    base = obj["key_drivers"][0]
    obj["key_drivers"] = [dict(base, cluster_id=c)
                          for c in ("cluster:m1", "cluster:m2")]
    hits = [p for p in sch.validate(obj, pk) if "共用同一個底層驅動" in p]
    assert hits, "同一個驅動的兩件事被當成兩個獨立確認"
    obj["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "us_monetary", "cluster_ids": ["cluster:m1", "cluster:m2"],
         "why_not_double_counted": "只把降息預期計一次,殖利率當它的價格表現"}]
    assert not [p for p in sch.validate(obj, pk) if "共用同一個底層驅動" in p]


def test_one_event_from_a_group_needs_no_explanation():
    """只用其中一件事不構成重複計權 —— 要求說明只會逼出湊字數的段落。"""
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:m1")]
    assert not [p for p in sch.validate(obj, _packet())
                if "共用同一個底層驅動" in p]


# ---------------------------------------------------------------- 淨效果

def _conflicting(obj):
    a = obj["top_news_analysis"][0]
    a["affected_assets"][0].update(asset_id="2330", direction="bullish")
    obj["top_news_analysis"] = [a, dict(
        a, source_item_id="n2",
        affected_assets=[dict(a["affected_assets"][0], direction="bearish")])]
    return obj


def test_conflicting_directions_demand_a_net_effect():
    """**使用者原話:「利多還是利空」。** 一則正面一則負面,兩段各自
    寫完就結束了 —— 讀者不知道合起來是什麼。"""
    obj = _conflicting(fx.valid_analysis())
    # 第二十三輪 P1-7:鍵是**別名組的代表寫法**(台積電)——
    # 「2330 bullish」與「台積電 bearish」是同一個標的的衝突。
    assert eg.conflicting_assets(obj) == {"台積電": ["n1", "n2"]}
    hits = [p for p in sch.validate(obj, _packet()) if "asset_net_effects" in p]
    assert hits


def test_a_net_effect_must_say_which_side_is_heavier():
    obj = _conflicting(fx.valid_analysis())
    obj["asset_net_effects"] = [{"asset_id": "2330", "net_direction": "bullish",
                                 "net_magnitude_band": "moderate",
                                 "offsetting_cluster_ids": ["cluster:m1",
                                                            "cluster:m2"],
                                 "why": "", "claim_ids": []}]
    assert [p for p in sch.validate(obj, _packet()) if "`why`" in p]
    obj["asset_net_effects"][0]["why"] = "產能恢復的量級大於降息預期的折現效果"
    # 第二十四輪 P1-8:淨效果還要**站在被稽核過的主張上** —— 「合起來是
    # 利多還是利空」是會進信的判斷,空的 `claim_ids` 等於沒有根據。
    obj["claim_audit"].append(dict(obj["claim_audit"][0], claim_id="cn",
                                   asset_scope="2330"))
    obj["asset_net_effects"][0]["claim_ids"] = ["cn"]
    assert not [p for p in sch.validate(obj, _packet())
                if "asset_net_effects" in p]


def test_a_net_effect_without_a_conflict_is_padding():
    """**湊一段不會讓分析更深。** 沒有互相抵銷的標的不必列 ——
    列了反而讓讀者以為那裡有衝突要調和。"""
    obj = fx.valid_analysis()
    obj["asset_net_effects"] = [{"asset_id": "2330", "net_direction": "bullish",
                                 "net_magnitude_band": "small",
                                 "offsetting_cluster_ids": [], "why": "x",
                                 "claim_ids": []}]
    assert [p for p in sch.validate(obj, _packet()) if "沒有方向衝突" in p]


def test_same_direction_twice_is_not_a_conflict():
    obj = fx.valid_analysis()
    a = obj["top_news_analysis"][0]
    a["affected_assets"][0].update(asset_id="2330", direction="bullish")
    obj["top_news_analysis"] = [a, dict(a, source_item_id="n2")]
    assert eg.conflicting_assets(obj) == {}


# ---------------------------------------------------------------- 聯合情境

def test_a_macro_release_must_condition_all_three_branches():
    """非農不是「一件會影響台股的事」,它是**分岔本身**:
    數字強 → 升息預期 → 台股承壓;數字弱 → 反之。
    三個分支若條件在三件不同的事上,那不是情境樹,是三個故事。"""
    pk = _packet()
    assert pk["event_graph"]["macro_release_cluster_id"] == "cluster:m1"
    obj = fx.valid_analysis()
    # 第二十三輪 P1-8:Fed 發言(m2)現在也是總經發布 —— 第二發布要嘛
    # 進重點要嘛 dismissed,先處理掉,聚焦驗「三分支要條件在主發布上」。
    obj["dismissed_events"] = [{"cluster_id": "cluster:m2",
                                "why_not_material": "與主發布同一驅動,合併談",
                                "reason": "同驅動", "revisit_trigger": "x",
                                "supporting_evidence_ids": ["m2"]}]
    hits = [p for p in sch.validate(obj, pk) if "分岔本身" in p]
    assert len(hits) == 3, hits          # base / bull / bear 各一
    # 三個分支都引用一條指向 m1 的主張就通過
    obj["claim_audit"].append(dict(obj["claim_audit"][0], claim_id="cm",
                                   evidence_ids=["m1"]))
    for br in ("base", "bull", "bear"):
        obj["scenario_tree"][br]["claim_ids"] = ["cm"]
    assert not [p for p in sch.validate(obj, pk) if "分岔本身" in p]


def test_a_second_macro_release_cannot_be_silently_ignored():
    """**第二十三輪 P1-8:CPI 與 Fed 決議同日,兩個都要被處理。**"""
    pk = _packet()
    assert pk["event_graph"]["macro_release_cluster_ids"] == [
        "cluster:m1", "cluster:m2"]
    obj = fx.valid_analysis()
    hits = [p for p in sch.validate(obj, pk) if "第二個總經發布" in p]
    assert hits and "cluster:m2" in hits[0]


def test_no_macro_release_means_no_requirement():
    """今天沒有總經發布就不要求 —— 硬要三個分支都指向同一件事,
    只會逼出形式上的引用。"""
    news = [_macro_news()[2]]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x",
                  target_session_date="y", sanitize=lambda s: s)
    assert pk["event_graph"]["macro_release_cluster_id"] == ""
    assert not [p for p in sch.validate(fx.valid_analysis(), pk)
                if "總經發布" in p]


def test_the_macro_pick_is_deterministic():
    """兩個總經發布時挑哪一個要有規則 —— 靠輸入順序的話,
    同一天兩次執行會要求不同的情境樹。"""
    # 標題刻意寫得不會與非農那則併群(併群的話 cluster_id 會變成最小
    # 的 m0,量到的就不是「挑哪一個總經發布」而是「併了沒有」)。
    news = _macro_news() + [
        {"source_item_id": "m0", "title": "核心通膨數據意外走高 市場預期修正",
         "summary": "PCE 年增率上修", "entities": ["物價"],
         "source_name": "日經"}]
    g1 = eg.build(nc.clusters(news), news)
    g2 = eg.build(nc.clusters(list(reversed(news))), news)
    # 就業排在通膨前面(`MACRO_RELEASE_DRIVERS` 的順序)
    assert g1["macro_release_cluster_id"] == g2["macro_release_cluster_id"]
    assert g1["macro_release_cluster_id"] == "cluster:m1"


def test_the_prompt_states_all_three_rules():
    import prompt_profiles as pp
    text = pp.LUNA_DEVELOPER_INSTRUCTIONS
    assert "asset_net_effects" in text
    assert "shared_driver_groups" in text and "shared_driver_notes" in text
    assert "macro_release_cluster_id" in text


def test_every_undismissed_macro_release_must_condition_all_branches():
    """**第二十四輪 P1-7(必補測試 9):CPI＋Fed 同日,每個分支要同時涵蓋兩者。**

    先前只驗 `macro_release_cluster_id`(單數,排序後的第一個)——
    第二個發布只要「被具名或被駁回」就過關,於是情境樹可以完全只建在
    CPI 上,而 Fed 被降級成一則新聞。兩個發布的交叉組合才是真正的分岔。
    """
    pk = _packet()
    assert pk["event_graph"]["macro_release_cluster_ids"] == [
        "cluster:m1", "cluster:m2"], "fixture 需要兩個總經發布"
    obj = fx.valid_analysis()
    # 兩個都不駁回 → 兩個都要條件在三個分支上(2 × 3 = 6 條問題)
    obj["dismissed_events"] = []
    hits = [p for p in sch.validate(obj, pk) if "分岔本身" in p]
    assert len(hits) == 6, hits

    # **只條件在第一個發布上仍然不夠** —— 這正是先前會放行的情況
    obj["claim_audit"].append(dict(obj["claim_audit"][0], claim_id="cm1",
                                   evidence_ids=["m1"]))
    for br in ("base", "bull", "bear"):
        obj["scenario_tree"][br]["claim_ids"] = ["cm1"]
    hits = [p for p in sch.validate(obj, pk) if "分岔本身" in p]
    assert len(hits) == 3 and all("cluster:m2" in p for p in hits), hits

    # 兩個都條件上去才通過
    obj["claim_audit"].append(dict(obj["claim_audit"][0], claim_id="cm2",
                                   evidence_ids=["m2"]))
    for br in ("base", "bull", "bear"):
        obj["scenario_tree"][br]["claim_ids"] = ["cm1", "cm2"]
    assert not [p for p in sch.validate(obj, pk) if "分岔本身" in p]


def test_a_dismissed_macro_release_is_exempt_from_conditioning():
    """駁回是模型說「今天這個真的不影響」的唯一出口 —— 兩邊都要求等於沒有出口。

    (駁回本身已被品質門檻把關:非套語、要引用自身事件群、要寫 revisit_trigger。)
    """
    pk = _packet()
    obj = fx.valid_analysis()
    obj["dismissed_events"] = [{"cluster_id": "cluster:m2",
                                "why_not_material": "與主發布同一驅動,合併談",
                                "reason": "同驅動", "revisit_trigger": "x",
                                "supporting_evidence_ids": ["m2"]}]
    hits = [p for p in sch.validate(obj, pk) if "分岔本身" in p]
    assert len(hits) == 3 and all("cluster:m1" in p for p in hits), hits
