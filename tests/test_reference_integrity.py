# -*- coding: utf-8 -*-
"""**結構化引用的指涉完整性**(第二十四輪 P1-8/P1-9 回歸)。

schema 保證得了「有這一格」,保證不了「這一格指到的東西真的存在、而且指對了」。
四個缺口各自都能讓一段沒有根據的話進信:

  * `asset_net_effects.claim_ids` 可以是空的 —— 「2330 合計偏多」於是可以
    完全沒有任何被稽核的主張支撐(必補測試 10);
  * 淨效果引用的主張與那個標的無關也照過;
  * `offsetting_cluster_ids` 指到不存在的事件群(必補測試 11);
  * `shared_driver_notes.cluster_ids` 同上(必補測試 13)。
"""
from __future__ import annotations

import analysis_contracts as ac


def _packet():
    return {"news_clusters": {"clusters": [
        {"cluster_id": "cluster:a", "member_source_ids": ["a"]},
        {"cluster_id": "cluster:b", "member_source_ids": ["b"]}]}}


def _obj(**over):
    base = {
        "claim_audit": [
            {"claim_id": "c1", "asset_scope": "2330", "direction": "bullish"},
            {"claim_id": "c2", "asset_scope": "2317", "direction": "bearish"}],
        "asset_net_effects": [
            {"asset_id": "2330", "net_direction": "bullish",
             "offsetting_cluster_ids": ["cluster:a", "cluster:b"],
             "claim_ids": ["c1"],
             "why": "訂單利多大於成本利空"}],
        "cross_market_synthesis": {"shared_driver_notes": [
            {"driver": "rates", "cluster_ids": ["cluster:a", "cluster:b"],
             "why_not_double_counted": "只計一次"}]},
    }
    base.update(over)
    return base


def test_a_clean_object_has_no_reference_problems():
    assert ac.reference_problems(_obj(), _packet()) == []


def test_net_effect_without_claims_is_rejected():
    """**必補測試 10**:淨效果必須有合法的 claim 根據。"""
    obj = _obj()
    obj["asset_net_effects"][0]["claim_ids"] = []
    problems = ac.reference_problems(obj, _packet())
    assert any("沒有引用任何 `claim_ids`" in p for p in problems)


def test_net_effect_citing_a_missing_claim_is_rejected():
    obj = _obj()
    obj["asset_net_effects"][0]["claim_ids"] = ["c9"]
    assert any("不存在的主張" in p for p in ac.reference_problems(obj, _packet()))


def test_net_effect_claim_must_be_about_that_asset():
    """方向靠一條、標的靠另一條 —— 等於沒有任何一條真的支撐這個淨判斷。"""
    obj = _obj()
    obj["asset_net_effects"][0]["claim_ids"] = ["c2"]      # c2 是 2317 的主張
    problems = ac.reference_problems(obj, _packet())
    assert any("沒有一條是關於 2330" in p for p in problems)


def test_offsetting_cluster_ids_must_exist():
    """**必補測試 11**:互相抵銷的事件群要指得到真的東西。"""
    obj = _obj()
    obj["asset_net_effects"][0]["offsetting_cluster_ids"] = ["cluster:y", "cluster:z"]
    problems = ac.reference_problems(obj, _packet())
    assert any("offsetting_cluster_ids" in p and "不存在" in p for p in problems)


def test_shared_driver_cluster_ids_must_exist():
    """**必補測試 13**:共用驅動的 cluster 要與 Python event graph 對得上。"""
    obj = _obj()
    obj["cross_market_synthesis"]["shared_driver_notes"][0][
        "cluster_ids"] = ["cluster:a", "cluster:nope"]
    problems = ac.reference_problems(obj, _packet())
    assert any("shared_driver_notes" in p and "不存在" in p for p in problems)


def test_no_packet_means_no_cluster_judgement():
    """拿不到 packet 就沒有事件群清單 —— **不猜**(但 claim 側仍然要驗)。"""
    obj = _obj()
    obj["asset_net_effects"][0]["offsetting_cluster_ids"] = ["cluster:y", "cluster:z"]
    problems = ac.reference_problems(obj, None)
    assert not any("offsetting_cluster_ids" in p for p in problems)
    obj["asset_net_effects"][0]["claim_ids"] = []
    assert any("claim_ids" in p for p in ac.reference_problems(obj, None))


def test_unknown_direction_is_exempt_from_asset_match():
    """判斷不出來時寫 `unknown` 是誠實的 —— 不必再要求標的對得上。"""
    obj = _obj()
    obj["asset_net_effects"][0]["net_direction"] = "unknown"
    obj["asset_net_effects"][0]["claim_ids"] = ["c2"]
    assert not any("關於 2330" in p for p in ac.reference_problems(obj, _packet()))


def test_reference_contract_is_wired_into_validate():
    """棘輪:只在契約模組裡驗得動等於沒有 —— 要真的接進 `validate`。"""
    from pathlib import Path
    import analysis_validate as av
    src = Path(av.__file__).read_text(encoding="utf-8")
    assert "reference_problems(obj, packet)" in src
