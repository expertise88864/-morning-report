# -*- coding: utf-8 -*-
"""**第二十五輪:身分從「主體過粗」改成了「動作過粗」。**

上一輪把延燒事件的身分從 entity 換成 action,方向對 —— 但同一個月裡
每一樁軍售、每一起資安事件、每一個關稅案都變成同一條線。
**動作過粗與主體過粗是同一個錯的兩面**,只是換了一個方向。

其餘三條是同一個形狀:**有結構,但沒有真正完成比較** ——
空的 `offsetting_cluster_ids`、只引勝方的 `claim_ids`、
空的 `shared_driver_notes.cluster_ids`,三者都讓契約看起來被滿足了。
"""
import analysis_schema as sch
import deepseek_responses as dsr
import event_identity as ei
import event_score as es
import evidence_packet as ep
import fixtures_analysis as fx
import instrument_registry as ir

_TODAY = "2026-08-06"


def _key(title, subjects, etype="geopolitical"):
    return ei.timeline_identity({"event_type": etype, "title": title},
                                subjects, _TODAY)["key"]


# ---------------------------------------------------------------- 事件身分

def test_two_arms_sales_to_different_recipients_do_not_merge():
    """**同一個月的兩樁軍售不是同一件事。** 上一版的鍵是
    `{型別}:{動作}:{月}`,完全不含對象。"""
    assert _key("美國宣布對台軍售", ["美國", "台灣"]) != \
        _key("美國宣布對日本軍售", ["美國", "日本"])


def test_two_cyberattacks_on_different_companies_do_not_merge():
    assert _key("藥華藥遭勒索軟體攻擊", ["藥華藥"]) != \
        _key("某銀行遭駭客入侵", ["某銀行"])


def test_two_tariff_actions_on_different_products_do_not_merge():
    assert _key("美國對多晶矽加徵關稅", ["美國"]) != \
        _key("歐盟對中國電動車加徵關稅", ["歐盟", "中國"])


def test_two_unrelated_summits_in_same_month_do_not_merge():
    assert _key("美中元首會談", ["美國", "中國"]) != \
        _key("歐日貿易談判", ["歐盟", "日本"])


def test_a_single_object_action_still_merges_across_wordings():
    """**修正不得把該合併的拆開。** 荷姆茲海峽只有一個 —— 它的對象是
    常數,不進鍵;所以中英文、不同主體集合的同一件事仍要合併。"""
    a = _key("伊朗與阿曼就荷姆茲航道達共識", ["伊朗", "阿曼"])
    b = _key("Hormuz passage framework agreed", ["Iran"])
    assert a == b, (a, b)
    assert "hormuz_passage" in a


def test_the_object_signature_only_applies_to_object_bearing_actions():
    assert ei.object_signature("hormuz_passage", ["伊朗"]) == ""
    assert ei.object_signature("arms_sale", ["美國", "台灣"]) == "台灣、美國"


# ---------------------------------------------------------------- 遷移

def _legacy(title, subjects, days=4):
    return {"geopolitical:X": {"days": days, "subjects": subjects,
                               "latest_title": title, "identity_schema": 4}}


def _adopt(legacy, title, subjects):
    ev = {"event_type": "geopolitical", "title": title}
    ident = ei.timeline_identity(ev, subjects, _TODAY)
    return ei.adopt_legacy(legacy, ev, subjects, ident)


def test_legacy_same_subject_different_action_is_not_adopted():
    """**主體有交集不代表是同一件事。** 制裁案的四天不得接到軍售案上 ——
    軍售案第一天就顯示「延燒第 5 天」是重構本來要消掉的錯誤。"""
    rec, _ = _adopt(_legacy("美國宣布對某國制裁", ["美國"]),
                    "美國宣布對台軍售", ["美國", "台灣"])
    assert rec is None


def test_legacy_adoption_requires_matching_action():
    rec, key = _adopt(_legacy("美國宣布對台軍售", ["美國", "台灣"]),
                      "美國宣布對台軍售", ["美國", "台灣"])
    assert rec is not None and rec["days"] == 4 and key == "geopolitical:X"


def test_an_unreadable_legacy_action_is_not_adopted_on_subject_overlap():
    """舊標題認不出動作時**不靠主體交集接天數** —— 低估連續天數只是少
    一句「第 N 天」,接錯會讓讀者以為今天才發生的事已經追蹤一週。"""
    rec, _ = _adopt(_legacy("(舊格式沒有存標題)", ["伊朗"]),
                    "荷姆茲海峽通行談判", ["伊朗", "阿曼"])
    assert rec is None


# ---------------------------------------------------------------- 淨效果

def _pk():
    news = [{"source_item_id": "n1", "title": "台積電熊本廠恢復產線",
             "summary": "產能回升", "entities": ["台積電"],
             "source_name": "經濟日報"},
            {"source_item_id": "n2", "title": "央行宣布調升存款準備率1碼",
             "summary": "資金收緊", "entities": ["央行"],
             "source_name": "中央銀行", "official": True}]
    return ep.build({}, {}, {}, news, [], {}, as_of="x",
                    target_session_date="y", sanitize=lambda s: s)


def _conflicting(net, claims=None):
    o = fx.valid_analysis()
    a = o["top_news_analysis"][0]
    a["affected_assets"][0].update(asset_id="2330", direction="bullish")
    o["top_news_analysis"] = [a, dict(
        a, source_item_id="n2",
        affected_assets=[dict(a["affected_assets"][0], direction="bearish")])]
    if claims:
        o["claim_audit"] = claims
    o["asset_net_effects"] = [net]
    return [p for p in sch.validate(o, _pk()) if "asset_net_effects" in p]


def _both_sides():
    base = fx.valid_analysis()["claim_audit"][0]
    return [dict(base, claim_id="cb", direction="bullish", asset_scope=["2330"]),
            dict(base, claim_id="cs", direction="bearish", asset_scope=["2330"])]


def test_real_conflict_rejects_empty_offsetting_clusters():
    """**空陣列先前整段跳過三道檢查。** 算得出衝突時,留空必敗。"""
    hits = _conflicting(
        {"asset_id": "2330", "net_direction": "bullish",
         "net_magnitude_band": "moderate", "offsetting_cluster_ids": [],
         "why": "利多較大", "claim_ids": ["cb", "cs"]}, _both_sides())
    assert [h for h in hits if "offsetting_cluster_ids" in h], hits


def test_winning_side_only_cannot_support_a_net_effect():
    """`offsetting_cluster_ids` 說有兩邊,`claim_ids` 只分析一邊 ——
    那不是淨效果,是選邊之後補一句理由。"""
    hits = _conflicting(
        {"asset_id": "2330", "net_direction": "bullish",
         "net_magnitude_band": "moderate",
         "offsetting_cluster_ids": ["cluster:n1", "cluster:n2"],
         "why": "利多較大", "claim_ids": ["cb"]}, _both_sides())
    assert [h for h in hits if "這一側" in h], hits


def test_both_sides_cited_passes():
    hits = _conflicting(
        {"asset_id": "2330", "net_direction": "bullish",
         "net_magnitude_band": "moderate",
         "offsetting_cluster_ids": ["cluster:n1", "cluster:n2"],
         "why": "產能恢復的量級大於資金面壓力", "claim_ids": ["cb", "cs"]},
        _both_sides())
    assert not hits, hits


# ---------------------------------------------------------------- 共用驅動

def test_correct_driver_name_with_empty_cluster_ids_does_not_count_as_handled():
    """**「已處理」的身分是 (驅動, 群集)**,不是驅動名稱 —— 只比名稱時,
    一則 `cluster_ids=[]` 的 note 就算處理過了。"""
    o = fx.valid_analysis()
    o["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "us_monetary", "cluster_ids": [],
         "why_not_double_counted": "已避免重複計權"}]
    assert [p for p in sch.validate(o, _pk()) if "shared_driver_notes" in p]


def test_shared_driver_handled_identity_is_driver_plus_cluster_set():
    """**反例要只靠這條規則分勝負。**

    上一條(空 `cluster_ids`)其實是被 `analysis_contracts` 的「少於兩件」
    先擋下的 —— 把 `analysis_crosscheck` 的「已處理身分」改回只比 driver
    名稱,那條測試照樣綠。這裡給一則 **cluster_ids 有兩個、都存在、
    但不是這個驅動群的** note:contracts 那邊過得去(數量夠),
    只有「(驅動, 群集)」這個身分分得出它其實沒有處理到那一群。
    """
    news = [{"source_item_id": "m1",
             "title": "美國7月非農就業新增18.5萬人 低於預期",
             "summary": "失業率升至4.3%", "entities": ["美國"],
             "source_name": "Reuters"},
            {"source_item_id": "m2", "title": "Fed 利率決議按兵不動",
             "summary": "點陣圖不變", "entities": ["聯準會"],
             "source_name": "CNBC"},
            {"source_item_id": "m3", "title": "台積電熊本廠恢復產線",
             "summary": "產能回升", "entities": ["台積電"],
             "source_name": "經濟日報"}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x",
                  target_session_date="y", sanitize=lambda s: s)
    assert pk["event_graph"]["shared_driver_groups"][0]["cluster_ids"] == [
        "cluster:m1", "cluster:m2"]
    obj = fx.valid_analysis()
    base = obj["key_drivers"][0]
    obj["key_drivers"] = [dict(base, cluster_id=c)
                          for c in ("cluster:m1", "cluster:m2")]
    obj["cross_market_synthesis"]["shared_driver_notes"] = [
        # driver 對、數量夠、群也都存在 —— 但講的是**別的兩群**
        {"driver": "us_monetary",
         "cluster_ids": ["cluster:m3", "cluster:m1"],
         "why_not_double_counted": "只計一次"}]
    hits = [p for p in sch.validate(obj, pk) if "共用同一個底層驅動" in p]
    assert hits, "只比 driver 名稱時,這則 note 會被當成已經處理過"


# ---------------------------------------------------------------- 標的身分

def test_business_abbreviations_are_not_instruments():
    """`CEO resigns after earnings miss` 不得讓 `CEO` 成為受影響標的 ——
    它長得像 ticker、出現在標題、不在概念詞表。"""
    pk = ep.build({}, {}, {},
                  [{"source_item_id": "n1",
                    "title": "NVDA 財報優於預期,CEO 樂觀看待 AI 需求",
                    "entities": ["輝達"], "source": "CNBC"},
                   {"source_item_id": "n2", "title": "b", "entities": ["c"],
                    "source": "d"}],
                  [], {}, as_of="x", target_session_date="y",
                  sanitize=lambda s: s)
    for bad in ("CEO", "IPO", "EPS", "GDP", "ADR"):
        o = fx.valid_analysis()
        o["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = bad
        assert [p for p in sch.validate(o, pk) if "不在這則" in p], bad
    # **不得誤殺真 ticker**:NVDA 在標題裡,它是這則新聞的主角
    o = fx.valid_analysis()
    o["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "NVDA"
    assert not [p for p in sch.validate(o, pk) if "不在這則" in p]


def test_missing_universe_does_not_verify_a_fake_code():
    """**「沒驗」與「驗過是標的」不是同一件事。** 三態把它們分開。"""
    assert ir.resolve_status("9999", None)[2] == ir.UNVERIFIED
    assert ir.resolve_status("9999", {"tw_universe": [{"code": "2330"}]})[2] \
        == ir.INVALID
    assert ir.resolve_status("2330", {"tw_universe": [{"code": "2330"}]})[2] \
        == ir.VERIFIED
    assert ir.resolve_status("CEO", None)[2] == ir.INVALID


# ---------------------------------------------------------------- 解析與計分

def test_commentary_plus_empty_final_is_flagged_empty():
    """**commentary 永遠不能當 final 的替補。** 上一版是
    `finals if finals else others`,而 `others` 裡混著 commentary。"""
    out = dsr.extract_output({"output": [
        {"type": "message", "phase": "commentary",
         "content": [{"type": "output_text", "text": "我先想一下…"}]},
        {"type": "message", "phase": "final_answer", "content": []}]})
    assert out["text"] == ""
    assert out["empty_content"] is True
    assert out["had_commentary"] is True


def test_unphased_message_still_works_as_fallback():
    """沒標階段的 message 仍是合法答案(舊回應形狀)。"""
    out = dsr.extract_output({"output": [
        {"type": "message",
         "content": [{"type": "output_text", "text": '{"a":1}'}]}]})
    assert out["text"] == '{"a":1}'


def test_nasdaq_cut_losses_is_still_a_price_move():
    """`cut` 是完整的一個 token,邊界救不了它 —— 這幾個過廣的英文詞
    只在「同時有價格詞」時起作用,而那正是它們最會誤判的場合。"""
    assert es.is_price_move("Nasdaq cut losses and rose 2%")
    assert not es.is_price_move("Fed cuts rates by 25bp")


# ============================================================ 2026-08-08 生產
#
# 那天的信同時暴露三個問題,其中兩個是第二十五輪改動的直接後果 ——
# **修正比缺陷更糟**的那個形狀,由生產抓到而不是測試。

def test_the_internal_identity_key_never_reaches_the_email():
    """**鍵是給程式用的,標籤是給人看的。**

    信裡的「延燒中事件」印的是 `key.split(":", 1)[-1]` —— 舊的兩段式鍵
    剛好切出主體(「伊朗」),而第二十五輪的三段式鍵切出來是
    `hormuz_passage:2026-08`。2026-08-08 那封信第一次讓它現形。
    """
    label = ei.display_label(
        {"key": "geopolitical:hormuz_passage:2026-08",
         "action": "hormuz_passage", "subjects": ["伊朗", "阿曼"]})
    assert "hormuz_passage" not in label and "2026-08" not in label
    assert "荷姆茲" in label and "伊朗" in label
    # 舊格式仍要看得懂
    assert ei.display_label(
        {"key": "geopolitical:伊朗", "action": "", "subjects": ["伊朗"]}) == "伊朗"
    # 什麼都沒有時也不得吐出日期段
    assert "2026" not in ei.display_label({"key": "geopolitical:x:2026-08"})


def test_an_unadopted_legacy_line_is_swept_not_left_running():
    """**同一件事不得有兩個「第 N 天」。**

    認領收緊(P1-3)之後,舊鍵接不到就自己留著繼續累計 —— 2026-08-08
    的信因此同時出現「伊朗(第 7 天)」與「hormuz_passage(第 2 天)」。
    低估天數只是少一句話;**兩個互相矛盾的天數比那更糟**。
    """
    state = {"geopolitical:伊朗": {
        "days": 7, "subjects": ["伊朗"], "identity_schema": 4,
        "latest_title": "川普預告戰爭快結束,稱親自參與談判"}}
    ev = {"event_type": "geopolitical", "title": "荷姆茲海峽有望重啟"}
    ident = ei.timeline_identity(ev, ["伊朗", "阿曼"], "2026-08-08")
    assert ei.adopt_legacy(state, ev, ["伊朗", "阿曼"], ident)[0] is None
    assert ei.supersede_legacy(state, ev, ["伊朗", "阿曼"], ident) == [
        "geopolitical:伊朗"]
    assert state == {}


def test_a_legacy_line_about_a_different_event_is_kept():
    """**收掉要保守。** 舊動作認得出來而且不同 —— 那是真的另一件事,
    它應該繼續有自己的天數,不能被順手掃掉。"""
    state = {"geopolitical:日本": {
        "days": 3, "subjects": ["日本"], "identity_schema": 4,
        "latest_title": "日本干預匯市"}}
    ev = {"event_type": "geopolitical", "title": "荷姆茲海峽有望重啟"}
    ident = ei.timeline_identity(ev, ["日本"], "2026-08-08")
    assert ei.supersede_legacy(state, ev, ["日本"], ident) == []
    assert "geopolitical:日本" in state
