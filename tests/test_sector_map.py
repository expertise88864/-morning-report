# -*- coding: utf-8 -*-
"""**橫向傳導要走宣告過的邊**(縱深第四批 C)。

模型自己也會沿供應鏈猜名字 —— 但猜出來的名字正是 instrument authority
要擋的東西。這張圖是宣告:每條邊是人寫的、每個名字都要通得過標的驗證、
候選不是證據(新聞支持那一步才走)。
"""
from __future__ import annotations

import evidence_packet as ep
import sector_map as sm


def test_every_name_on_the_map_is_a_declared_instrument():
    """**表的完整性守衛**:加了一個沒宣告的名字(`ASEAN` 那一類)
    當場紅 —— 這張圖存在的前提就是它只含通得過標的驗證的名字。"""
    import instrument_registry as ir
    bad = [n for e in sm.EDGES for n in e[:2] if not ir.is_declared(n)]
    assert not bad, f"這些節點沒有被宣告成標的:{bad}"
    assert sm.EDGES, "空表不算通過"


def test_candidates_follow_declared_edges_in_both_directions():
    """台積電的事件走得到設備商與客戶;客戶的事件走得回代工。"""
    got = {c["name"] for c in sm.transmission_candidates(["台積電"])}
    assert "ASML" in got and "AMAT" in got, got
    back = {c["name"] for c in sm.transmission_candidates(["ASML"])}
    assert "台積電" in back, back
    # 關係說明帶著(模型要寫得出「為什麼走這一步」)
    rel = next(c for c in sm.transmission_candidates(["台積電"])
               if c["name"] == "ASML")
    assert "設備" in rel["relation"], rel


def test_table_nodes_are_normalised_before_matching():
    """**表的節點也要正規化**:表裡寫 `NVDA`,別名組的代表寫法是
    「輝達」—— 兩邊不走同一套的話,英文節點的邊整條失效
    (第一版實測 `NVDA → []`,宣告守衛驗不到這件事)。"""
    a = {c["name"] for c in sm.transmission_candidates(["NVDA"])}
    b = {c["name"] for c in sm.transmission_candidates(["輝達"])}
    assert a and a == b, (a, b)
    assert "台積電" in a, a


def test_the_subject_itself_is_not_a_candidate():
    """已在主體集合裡的不列 —— 那不是傳導,是本人。"""
    got = {c["name"] for c in sm.transmission_candidates(["台積電", "NVDA"])}
    assert "台積電" not in got and "輝達" not in got, got


def test_unknown_subjects_get_no_candidates():
    """認不出主體、或主體不在圖上 → 空清單(**不猜**)。"""
    assert sm.transmission_candidates(["伊朗"]) == []
    assert sm.transmission_candidates([]) == []


def test_candidates_are_capped():
    """多了會稀釋 —— 模型該走的是新聞支持的那一兩步。"""
    assert len(sm.transmission_candidates(["台積電"])) <= sm.MAX_CANDIDATES


def test_the_candidates_reach_the_packet_cluster():
    """**沒接上等於不存在**:候選要真的出現在事件群上。"""
    news = [{"source_item_id": "n1", "title": "台積電法說會上修資本支出",
             "entities": ["台積電"], "source": "經濟日報"},
            {"source_item_id": "n2", "title": "台積電資本支出上修 設備股受惠",
             "entities": ["台積電", "ASML"], "source": "Reuters"}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="2026-08-09 06:00",
                  target_session_date="2026-08-09",
                  sanitize=lambda s, *a: s)
    clu = pk["news_clusters"]["clusters"][0]
    names = {c["name"] for c in clu["transmission_candidates"]}
    assert names, clu
    # 群裡已有 ASML(本人)→ 不在候選;設備同業仍在
    assert "ASML" not in names and "AMAT" in names, names


def test_the_prompt_says_candidates_are_not_evidence():
    """prompt 要說出「候選不是證據、新聞支持那一步才走」——
    沒說的話,這張圖等於邀請模型把整條鏈抄一遍。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parents[1] / "prompt_profiles.py",
                  encoding="utf-8").read()
    anchor = "`transmission_candidates` 時"
    assert anchor in src
    seg = src[src.index(anchor):src.index(anchor) + 500]
    assert "不是證據" in seg, seg
    assert "新聞" in seg and "支持" in seg, seg
