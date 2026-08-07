# -*- coding: utf-8 -*-
"""**不得把沒有根據的話洗成有根據**(外審 Commit 2 / P1-6·P1-7·P1-8)。

四個繞法共同的形狀是同一個:**存在性檢查通過 ≠ 這句話有根據**。

  * 剪掉捏造的證據、留下無關但合法的證據 → 主張看起來有根據;
  * 空的 `asset_scope` 支撐任何一檔的淨判斷;
  * bearish 的主張支撐 bullish 的淨方向;
  * 一個事件群被稱為「互相抵銷」;兩個無關的事件群被稱為「共用驅動」。

這一份專門守這四條 —— 每一條都要能單獨紅。
"""
from __future__ import annotations

import analysis_contracts as ac


def _packet(**over):
    pk = {"news_clusters": {"clusters": [
        {"cluster_id": "cluster:a", "member_source_ids": ["n1"]},
        {"cluster_id": "cluster:b", "member_source_ids": ["n2"]},
        {"cluster_id": "cluster:c", "member_source_ids": ["n3"]}]},
        "event_graph": {"shared_driver_groups": [
            {"driver": "ai_capex", "cluster_ids": ["cluster:a", "cluster:b"]}]}}
    pk.update(over)
    return pk


def _obj(**over):
    base = {
        "claim_audit": [
            {"claim_id": "c_bull", "asset_scope": ["2330"], "direction": "bullish"},
            {"claim_id": "c_bear", "asset_scope": ["2330"], "direction": "bearish"},
            {"claim_id": "c_wide", "asset_scope": ["market-wide"],
             "direction": "bullish"},
            {"claim_id": "c_none", "asset_scope": [], "direction": "bullish"},
            {"claim_id": "c_other", "asset_scope": ["2317"], "direction": "bullish"}],
        "asset_net_effects": [
            {"asset_id": "2330", "net_direction": "bullish",
             "claim_ids": ["c_bull"], "offsetting_cluster_ids": [],
             "why": "訂單利多大於成本利空"}],
        "cross_market_synthesis": {"shared_driver_notes": []},
    }
    base.update(over)
    return base


def _hits(problems, needle):
    return [p for p in problems if needle in p]


# ---------------------------------------------------------------- 標的範圍

def test_an_empty_asset_scope_cannot_support_a_named_asset():
    """**空範圍不是「涵蓋全部」**(外審 P1-7.1)。

    上一版的判準是 `asset_scope in ("", aid)` —— 空的那格因此支撐任何
    一檔的淨判斷,而「一句沒指定對象的話」正是最容易寫得漂亮的那種。
    """
    obj = _obj()
    obj["asset_net_effects"][0]["claim_ids"] = ["c_none"]
    assert _hits(ac.reference_problems(obj, _packet()), "關於 2330")


def test_a_market_wide_claim_cannot_support_a_named_asset():
    """`market-wide` 是**刻意的泛稱寫法**(schema 自己這樣要求)——
    它說的是整體,不是這一檔。"""
    obj = _obj()
    obj["asset_net_effects"][0]["claim_ids"] = ["c_wide"]
    assert _hits(ac.reference_problems(obj, _packet()), "關於 2330")


def test_a_correctly_scoped_claim_is_accepted():
    """**反向也要測。** 上一版把陣列 `["2330"]` 整個字串化成
    `"['2330']"`,於是正確標註的主張反而被拒 —— 判準剛好相反,
    而沒有任何測試看得到(fixture 的 `asset_net_effects` 是空的)。
    """
    assert not _hits(ac.reference_problems(_obj(), _packet()), "關於 2330")


def test_an_alias_of_the_same_asset_still_counts():
    """`2330` 與「台積電」是同一檔 —— 別名不該讓正確的主張落空。"""
    obj = _obj()
    obj["claim_audit"][0]["asset_scope"] = ["台積電"]
    assert not _hits(ac.reference_problems(obj, _packet()), "關於 2330")


def test_a_claim_about_another_asset_cannot_support_this_one():
    obj = _obj()
    obj["asset_net_effects"][0]["claim_ids"] = ["c_other"]
    assert _hits(ac.reference_problems(obj, _packet()), "關於 2330")


# ---------------------------------------------------------------- 方向

def test_net_bullish_cannot_rest_only_on_bearish_claims():
    """**方向也要對得上**(外審 P1-7.2)。一條「2330 偏空」的主張
    可以支撐「2330 合計偏多」—— 只要標的一樣,那是最隱蔽的一種。"""
    obj = _obj()
    obj["asset_net_effects"][0]["claim_ids"] = ["c_bear"]
    assert _hits(ac.reference_problems(obj, _packet()), "沒有一條是同方向")


def test_net_bullish_passes_when_a_same_direction_claim_exists():
    """同向的有一條就夠 —— 淨判斷本來就會引用兩側的主張。"""
    obj = _obj()
    obj["asset_net_effects"][0]["claim_ids"] = ["c_bear", "c_bull"]
    assert not _hits(ac.reference_problems(obj, _packet()), "沒有一條是同方向")


def test_a_neutral_net_direction_is_not_asked_to_pick_a_side():
    """`neutral` 的意思就是兩邊差不多 —— 要求它有同向主張沒有意義。"""
    obj = _obj()
    obj["asset_net_effects"][0].update(net_direction="neutral",
                                       claim_ids=["c_bear"])
    assert not _hits(ac.reference_problems(obj, _packet()), "沒有一條是同方向")


# ---------------------------------------------------------------- 抵銷

def test_one_cluster_cannot_offset_anything():
    """**「互相抵銷」至少要兩件事**(外審 P1-7.3)。先前連測試裡的
    「乾淨答案」都只有一個 cluster —— 規則從來沒有被表達過。"""
    obj = _obj()
    obj["asset_net_effects"][0]["offsetting_cluster_ids"] = ["cluster:a"]
    assert _hits(ac.reference_problems(obj, _packet()), "至少要兩件事")


def test_offsetting_clusters_must_match_the_detected_conflict_exactly():
    """**哪些事互相抵銷由資料決定,不由輸出自選。**

    模型挑一個子集(或挑別的群)仍然「都存在」,而讀者看到的是
    「這兩件事互相抵銷」—— 那句話的根據是衝突偵測,不是模型的挑選。
    """
    obj = _obj()
    # 讓 2330 在輸出裡真的有方向衝突(n1 多、n2 空)→ 期望集合 = {a, b}
    obj["top_news_analysis"] = [
        {"source_item_id": "n1",
         "affected_assets": [{"asset_id": "2330", "direction": "bullish"}]},
        {"source_item_id": "n2",
         "affected_assets": [{"asset_id": "2330", "direction": "bearish"}]}]
    obj["asset_net_effects"][0]["claim_ids"] = ["c_bull"]

    obj["asset_net_effects"][0]["offsetting_cluster_ids"] = ["cluster:a", "cluster:b"]
    assert not _hits(ac.reference_problems(obj, _packet()), "不一致")

    obj["asset_net_effects"][0]["offsetting_cluster_ids"] = ["cluster:a", "cluster:c"]
    assert _hits(ac.reference_problems(obj, _packet()), "不一致")


# ---------------------------------------------------------------- 共用驅動

def test_two_unrelated_clusters_cannot_be_called_a_shared_driver():
    """**存在不等於共用同一個驅動**(外審 P1-8)。這一段的用途正是
    「所以不算重複計權」—— 指到兩個真實但無關的事件群,那句話是假的。"""
    obj = _obj()
    obj["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "ai_capex", "cluster_ids": ["cluster:a", "cluster:c"],
         "why_not_double_counted": "只計一次"}]
    assert _hits(ac.reference_problems(obj, _packet()), "不是本日任何一組共用驅動")


def test_the_real_shared_driver_group_passes():
    obj = _obj()
    obj["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "ai_capex", "cluster_ids": ["cluster:a", "cluster:b"],
         "why_not_double_counted": "只計一次"}]
    assert not _hits(ac.reference_problems(obj, _packet()), "共用驅動")


def test_the_claimed_driver_name_must_match_the_python_grouping():
    """組對了但驅動名說錯,信裡寫的仍是錯的歸因。"""
    obj = _obj()
    obj["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "fed_policy", "cluster_ids": ["cluster:a", "cluster:b"],
         "why_not_double_counted": "只計一次"}]
    assert _hits(ac.reference_problems(obj, _packet()), "被歸類為")


def test_a_single_cluster_shared_driver_note_is_rejected():
    obj = _obj()
    obj["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "ai_capex", "cluster_ids": ["cluster:a"],
         "why_not_double_counted": "只計一次"}]
    assert _hits(ac.reference_problems(obj, _packet()), "至少要兩件事")


def test_no_event_graph_means_no_shared_driver_judgement():
    """**沒有分母就不猜。** packet 沒帶 `event_graph` 時,這條規則
    整個不適用 —— 不是「所有的組都是錯的」。"""
    obj = _obj()
    obj["cross_market_synthesis"]["shared_driver_notes"] = [
        {"driver": "ai_capex", "cluster_ids": ["cluster:a", "cluster:b"],
         "why_not_double_counted": "只計一次"}]
    pk = _packet()
    pk.pop("event_graph")
    assert not _hits(ac.reference_problems(obj, pk), "共用驅動")


# ---------------------------------------------------------------- key driver 反證

def test_a_fabricated_key_driver_counterevidence_is_rejected():
    """**renderer 看到非空的反證就標「有反面證據」**(外審 P1-8)。
    先前只驗支持證據 —— 捏一個 ID,讀者就看到一個不存在的反面觀點,
    而那是「這條判斷有多穩」的訊號。"""
    import analysis_validate as av
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    obj["key_drivers"][0]["counterevidence_ids"] = ["完全捏造的ID"]
    problems = av.validate(obj, fx.ids())
    assert [p for p in problems
            if "key_drivers[0] 的反證" in p and "完全捏造的ID" in p], problems
