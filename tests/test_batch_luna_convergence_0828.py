# -*- coding: utf-8 -*-
"""2026-08-28 生產:Luna 連日落 legacy,而時間已經不是原因。

那一班 `repair_budget.used.semantic = 2/2`(1800s 之後兩輪修補都跑滿),
13 條駁回裡有 4 條是同一對規則在來回:一關要模型寫 `shared_driver_notes`
並**指名交集**,另一關要求 `cluster_ids` 與**整組**完全一致 —— 模型照著
訊息寫下交集,下一關就打回。兩關各自看都合理,合起來收斂不了。
"""
import analysis_contracts as ac
import analysis_crosscheck as cc


def _packet(group_ids=("cluster:a", "cluster:b", "cluster:d"),
            required=("cluster:a",)):
    return {
        "news_clusters": {
            "clusters": [{"cluster_id": c, "member_source_ids": [f"n{i}"]}
                         for i, c in enumerate(group_ids)],
            "required_cluster_ids": list(required)},
        "event_graph": {"shared_driver_groups": [
            {"driver": "fed_path", "label": "聯準會政策路徑",
             "cluster_ids": list(group_ids)}]}}


def _obj(notes=()):
    return {"key_drivers": [{"cluster_id": "cluster:a"},
                            {"cluster_id": "cluster:b"}],
            "cross_market_synthesis": {"shared_driver_notes": list(notes)},
            "dismissed_events": []}


def _shared(problems):
    return [str(p) for p in problems if "共用" in str(p)]


def test_following_the_instruction_satisfies_the_other_gate():
    """**這條測試的形狀就是缺陷本身**:照著駁回訊息說的做一遍,
    另一關必須也過。只驗單邊(訊息有出現、或契約會擋)兩邊都會綠,
    而生產仍然收斂不了 —— 08/28 那班就是這樣。"""
    pk = _packet()
    told = _shared(cc.event_graph_problems(_obj(), pk))
    assert told, "第一關要先真的開口要求"
    msg = told[0]
    # 訊息必須指名**整組**(不是只有三大重點用到的那兩個)
    assert "cluster:d" in msg, msg
    # 照訊息做:整組都列進去
    obj = _obj([{"driver": "fed_path",
                 "cluster_ids": ["cluster:a", "cluster:b", "cluster:d"],
                 "why_not_double_counted": "同一個驅動只計一次"}])
    assert not _shared(cc.event_graph_problems(obj, pk)), "第一關沒被滿足"
    assert not _shared(ac.reference_problems(obj, pk)), "第二關把照做的打回"


def test_the_old_intersection_answer_is_still_rejected():
    """**契約沒有被放寬**:只列交集仍然不合格(那正是第二關要防的
    「存在不等於共用同一個驅動」)。這批修的是訊息說錯了要什麼,
    不是把關卡拆掉。"""
    pk = _packet()
    obj = _obj([{"driver": "fed_path",
                 "cluster_ids": ["cluster:a", "cluster:b"],
                 "why_not_double_counted": "同一個驅動只計一次"}])
    assert _shared(ac.reference_problems(obj, pk))


def test_a_duplicated_required_cluster_is_reported_once():
    """重複的駁回對模型是零資訊,卻讓問題數虛胖 —— 而問題數正是
    「收斂了沒有」的判準(08/28 那班 13 條裡有 2 條是這樣來的)。"""
    pk = _packet(required=("cluster:a", "cluster:a"))
    probs = [str(p) for p in cc._coverage_problems(_obj(), pk, set())
             if "cluster:a" in str(p)]
    assert len(probs) == 1, probs
