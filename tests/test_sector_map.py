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


# ===== 橫向抓取(縱深第四批 C 之二) =====

def _fu(key, entity, name):
    return {"key": key, "query": f"{name} 訂單", "entity": entity, "name": name}


def test_horizontal_queries_walk_declared_edges():
    """查詢綁「候選 + 本尊」,而 key/entity/name 都是**發起線索**的 ——
    抓回的文章走與縱向同一個貼標閘門,提到本尊才接回線索。"""
    import sector_map as sm
    got = sm.horizontal_queries([_fu("e:2330|l:orders", "2330", "台積電")])
    assert got and got[0]["query"] == "ASML 台積電", got
    assert got[0]["key"] == "e:2330|l:orders"
    assert got[0]["entity"] == "2330" and got[0]["name"] == "台積電"


def test_the_horizontal_budget_is_shared_round_robin():
    """**單一線索不獨占橫向預算**:每條線索先各拿第一個候選,
    額度還有再輪第二個。"""
    import sector_map as sm
    got = sm.horizontal_queries([_fu("k1", "2330", "台積電"),
                                 _fu("k2", "NVDA", "輝達")])
    assert len(got) == sm.HORIZONTAL_MAX_QUERIES
    assert [g["key"] for g in got] == ["k1", "k2", "k1"], got


def test_tracked_names_are_not_queried_horizontally():
    """縱向已經在追的名字不再橫向查(它自己有查詢);
    同一個候選被多條線索走到只查一次。"""
    import sector_map as sm
    got = sm.horizontal_queries([_fu("k1", "2330", "台積電"),
                                 _fu("k2", "NVDA", "輝達")])
    names = [g["query"].split()[0] for g in got]
    assert "輝達" not in names and "台積電" not in names, got
    assert len(names) == len(set(names)), got


def test_unknown_subjects_yield_no_horizontal_queries():
    """主體不在圖上 → 沒有橫向查詢(**不猜**,與候選同一條規矩)。"""
    import sector_map as sm
    assert sm.horizontal_queries([_fu("k", "9999", "不在圖上的公司")]) == []
    assert sm.horizontal_queries([]) == []
    # 形狀防禦:混進非 dict / 空名不炸
    assert sm.horizontal_queries([None, {"key": "k"}]) == []


def test_fetch_news_does_not_silently_drop_horizontal_queries(monkeypatch,
                                                              capsys):
    """**生產的切片要蓋到橫向**:`fetch_news` 原本切 `FOLLOWUP_MAX_QUERIES`,
    橫向多出來的三條會被**悄悄丟掉**(沒有任何日誌)—— 悄悄截斷讀起來
    就像全部蓋到了。走生產的呼叫形狀驗:縱向 5 + 橫向 3 全部要成為
    工作項,再多的要有日誌。"""
    import morning_report as mr
    import sector_map as sm
    import story_ledger as sl
    seen = []
    monkeypatch.setattr(mr, "RSS_FEEDS", {})
    monkeypatch.setattr(mr, "GOOGLE_NEWS_COMPANIES", [])
    monkeypatch.setattr(mr, "_process_feed_item",
                        lambda w, cutoff: (seen.append(w), [])[1])
    vert = [_fu(f"k{i}", f"23{i}0", f"直向{i}")
            for i in range(sl.FOLLOWUP_MAX_QUERIES)]
    horiz = [{"key": "k0", "query": f"橫向{i} 直向0", "entity": "2300",
              "name": "直向0"} for i in range(sm.HORIZONTAL_MAX_QUERIES)]
    mr.fetch_news(vert + horiz)
    assert len(seen) == len(vert) + len(horiz), [w["source"] for w in seen]
    assert any("橫向2" in w["source"] for w in seen), "橫向被切掉了"
    # 每一條都帶著接回線索用的欄位(與縱向同一個貼標閘門)
    assert all(w.get("followup_key") for w in seen), seen
    # 真的超額才丟,而且要說
    seen.clear()
    mr.fetch_news(vert + horiz + [_fu("k9", "9999", "超額")])
    assert len(seen) == len(vert) + len(horiz)
    assert "超額" in capsys.readouterr().err


def test_the_producer_wires_horizontal_queries_into_the_fetch():
    """**沒接上等於不存在**:`horizontal_queries` 要真的在抓新聞的相位被
    呼叫、而且結果併進 `fetch_news` 的清單 —— 只有函式沒有呼叫端的話,
    上面每一條測試都在替一個不存在的行為背書。"""
    import io as _io
    from pathlib import Path
    src = _io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                   encoding="utf-8").read()
    i = src.index("horizontal_queries(_followups)")
    seg = src[i:src.index("news = fetch_news(_followups)", i)]
    assert "_followups = _followups + _horiz" in seg, seg[:400]


def test_fetch_news_survives_a_broken_sector_map(monkeypatch, capsys):
    """**橫向是選配,抓新聞不是**(外審 r1):`sector_map` 載入失敗時
    `fetch_news` 要退回純縱向(上限、日誌),不是把整封晨報帶走 ——
    呼叫端的 try 只蓋到橫向查詢的產生,蓋不到這裡的 import。"""
    import sys as _sys
    import morning_report as mr
    import story_ledger as sl
    seen = []
    monkeypatch.setattr(mr, "RSS_FEEDS", {})
    monkeypatch.setattr(mr, "GOOGLE_NEWS_COMPANIES", [])
    monkeypatch.setattr(mr, "_process_feed_item",
                        lambda w, cutoff: (seen.append(w), [])[1])
    monkeypatch.setitem(_sys.modules, "sector_map", None)  # 模擬載入失敗
    vert = [_fu(f"k{i}", f"23{i}0", f"直向{i}")
            for i in range(sl.FOLLOWUP_MAX_QUERIES)]
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    mr.fetch_news(vert)                    # 不得拋出
    assert len(seen) == len(vert)
    assert "橫向模組載入失敗" in capsys.readouterr().err
    # 降級要**登錄**不是只印(外審 r2):`_DEGRADED_STEPS` 進 run
    # manifest,stderr 不進 —— 不登錄的話執行紀錄把這天讀成正常成功。
    assert "sector_map_unavailable" in mr._DEGRADED_STEPS

