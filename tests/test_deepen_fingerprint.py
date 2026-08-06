# -*- coding: utf-8 -*-
"""**加深不得改變信裡看得到的東西**(第二十四輪 P1-10 回歸)。

`_identity()` 是「第二版必須保留什麼」的定義。先前四個會**渲染進信**的
段落只被保護了一部分欄位,於是加深可以在「集合存在」不變的情況下改變語意:

  * 三大重點:調高 confidence、刪掉反證與失效條件、換掉 claim 根據
  * 逐標的淨效果:換掉抵銷的事件群與 claim 根據
  * 共同驅動說明:改寫它指向的事件群
  * 駁回的事件:抽掉支持證據與回頭條件

必補測試 14:**加深改動任何 rendered semantic field 必須被拒。**
"""
from __future__ import annotations

import copy

import analysis_depth as ad


def _obj():
    return {
        "key_drivers": [{
            "cluster_id": "cluster:a", "statement": "台積電上修資本支出",
            "direction": "bullish", "materiality": "high", "horizon": "1-5d",
            "confidence": 0.6, "falsification_trigger": "法說下修",
            "counterevidence_ids": ["n2"], "claim_ids": ["c1"],
            "evidence_ids": ["n1"]}],
        "asset_net_effects": [{
            "asset_id": "2330", "net_direction": "bullish",
            "net_magnitude_band": "moderate", "why": "訂單大於成本",
            "offsetting_cluster_ids": ["cluster:b"], "claim_ids": ["c1"]}],
        "cross_market_synthesis": {"shared_driver_notes": [{
            "driver": "rates", "cluster_ids": ["cluster:a", "cluster:b"],
            "why_not_double_counted": "只計一次"}]},
        "dismissed_events": [{
            "cluster_id": "cluster:c", "why_not_material": "與主線同驅動",
            "revisit_trigger": "官方改口", "supporting_evidence_ids": ["n3"]}],
    }


def _changed(mutate):
    """回傳 (原身分, 改過的身分) —— 兩者不同才代表那一格被保護住。"""
    before = _obj()
    after = copy.deepcopy(before)
    mutate(after)
    return ad._identity(before), ad._identity(after)


def test_driver_confidence_is_protected():
    ib, ia = _changed(lambda o: o["key_drivers"][0].__setitem__("confidence", 0.95))
    assert ib["三大重點"] != ia["三大重點"], "調高信心沒有被身分抓到"


def test_driver_counterevidence_is_protected():
    ib, ia = _changed(
        lambda o: o["key_drivers"][0].__setitem__("counterevidence_ids", []))
    assert ib["三大重點"] != ia["三大重點"], "刪掉反證沒有被身分抓到"


def test_driver_falsification_trigger_is_protected():
    ib, ia = _changed(
        lambda o: o["key_drivers"][0].__setitem__("falsification_trigger", ""))
    assert ib["三大重點"] != ia["三大重點"], "刪掉失效條件沒有被身分抓到"


def test_driver_claim_ids_are_protected():
    ib, ia = _changed(
        lambda o: o["key_drivers"][0].__setitem__("claim_ids", ["c9"]))
    assert ib["三大重點"] != ia["三大重點"], "換掉 claim 根據沒有被身分抓到"


def test_net_effect_offsetting_clusters_are_protected():
    ib, ia = _changed(lambda o: o["asset_net_effects"][0].__setitem__(
        "offsetting_cluster_ids", ["cluster:zzz"]))
    assert ib["逐標的淨效果"] != ia["逐標的淨效果"]


def test_net_effect_claim_ids_are_protected():
    ib, ia = _changed(
        lambda o: o["asset_net_effects"][0].__setitem__("claim_ids", []))
    assert ib["逐標的淨效果"] != ia["逐標的淨效果"]


def test_shared_driver_cluster_ids_are_protected():
    ib, ia = _changed(lambda o: o["cross_market_synthesis"][
        "shared_driver_notes"][0].__setitem__("cluster_ids", ["cluster:a"]))
    assert ib["共同驅動說明"] != ia["共同驅動說明"]


def test_dismissal_revisit_trigger_is_protected():
    ib, ia = _changed(
        lambda o: o["dismissed_events"][0].__setitem__("revisit_trigger", ""))
    assert ib["駁回的事件"] != ia["駁回的事件"]


def test_dismissal_supporting_evidence_is_protected():
    ib, ia = _changed(lambda o: o["dismissed_events"][0].__setitem__(
        "supporting_evidence_ids", []))
    assert ib["駁回的事件"] != ia["駁回的事件"]


def test_an_untouched_object_keeps_the_same_identity():
    """反向:什麼都沒改時身分必須完全相同(否則保護會變成噪音)。"""
    a, b = ad._identity(_obj()), ad._identity(_obj())
    assert a == b


def test_evidence_id_order_does_not_change_identity():
    """順序不是語意 —— 只是排列不同不該被判成退步。"""
    ib, ia = _changed(lambda o: o["key_drivers"][0].__setitem__(
        "evidence_ids", list(reversed(o["key_drivers"][0]["evidence_ids"]))))
    assert ib["三大重點"] == ia["三大重點"]
