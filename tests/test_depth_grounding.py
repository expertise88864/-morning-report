# -*- coding: utf-8 -*-
"""**第十八輪:引用要相關、覆蓋率不得虛胖、深度不得靠重新分類繞過。**

這一輪的共同形狀不是「守衛沒寫」,是**守衛量錯了東西**:

  * 引用檢查證明「這個 ID 存在」,而不是「這個 ID 跟你在調和的張力有關」;
  * 覆蓋率數 `len(resolutions)`,而同一筆填三次會變成 300%;
  * 完整度用 `not obj.get(s)`,於是**沒有缺口的好報告**被算成少一段;
  * 選優比新聞 ID 的集合,而把 n1 從 high 降成 medium **集合完全不變**。

最後一條是我自己的:主閘門在生產吃的是 ID 集合,不是 packet ——
上一批把選優與指標接上了 packet,唯獨漏掉那個真正會擋下輸出的地方。
**守衛接錯線與守衛不存在,對收件人是同一件事。**
"""
from pathlib import Path

import analysis_depth as ad
import analysis_metrics as am
import analysis_schema as sch
import analysis_stages as ast_
import evidence_packet as ep
import fixtures_analysis as fx
import tension_refs as tr

_IDS = {"n1", "n2"}


def _packet() -> dict:
    return ep.build({"QQQ": {"change_pct": 1.76},
                     "TAIFEX_OI": {"foreign_oi_net": -90038}},
                    {}, {}, fx.news(), [], {},
                    as_of="2026-08-05T06:00", target_session_date="2026-08-05",
                    sanitize=str)


def _resolved(pk, **over) -> dict:
    """一份**把今天每筆張力都處理過**的分析。"""
    obj = fx.valid_analysis()
    obj["data_gaps"] = [{"what_is_missing": "產業廣度",
                         "impact_on_conclusions": "分歧判斷不了"}]
    obj["cross_market_synthesis"]["tension_resolutions"] = [
        dict({"tension_id": t, "resolution": "外部定價先反映在權值開盤",
              "dominant_side": "left", "why": "開盤前只有美股已定價",
              "decision_rule": "現貨量能與期貨空單是否回補",
              "evidence_ids": [t]}, **over)
        for t in sorted(tr.required_tension_ids(pk.get("signal_tensions")))]
    return obj


# ------------------------------------------------ 引用存在 ≠ 引用相關

def test_a_resolution_must_cite_the_tension_it_claims_to_settle():
    """**P1-5**:拿一則不相干的新聞去調和「QQQ vs 外資期貨」形式上完全合法。

    先前的測試 fixture 自己就在示範那個寫法(`evidence_ids: ["n1"]`)——
    **參考答案示範了它要防的東西**,而那份 fixture 決定了模型看到什麼。
    """
    pk = _packet()
    obj = _resolved(pk, evidence_ids=["n1"])
    hits = [p for p in sch.validate(obj, pk) if "沒有涵蓋這筆張力" in p]
    assert hits, "引用一則不相干的新聞就通過了"
    # 引用該張力本身可以
    assert not [p for p in sch.validate(_resolved(pk), pk)
                if "沒有涵蓋這筆張力" in p]


def test_citing_both_sides_also_counts():
    """兩側各引用到至少一個 —— 那比引用張力 ID 更精確,當然要通過。"""
    pk = _packet()
    sides = tr.sides_evidence(pk.get("signal_tensions"))
    tid = sorted(tr.required_tension_ids(pk.get("signal_tensions")))[0]
    left, right = sides[tid]
    obj = _resolved(pk, evidence_ids=[sorted(left)[0], sorted(right)[0]])
    assert not [p for p in sch.validate(obj, pk) if "沒有涵蓋這筆張力" in p]


def test_citing_only_one_side_is_not_enough():
    """**只引用一側等於只看一半。** 調和的定義就是把兩邊放在一起。"""
    pk = _packet()
    tid = sorted(tr.required_tension_ids(pk.get("signal_tensions")))[0]
    left, _ = tr.sides_evidence(pk.get("signal_tensions"))[tid]
    obj = _resolved(pk, evidence_ids=[sorted(left)[0]])
    assert [p for p in sch.validate(obj, pk) if "沒有涵蓋這筆張力" in p]


# ------------------------------------------------ 覆蓋率不得虛胖

def test_a_duplicated_resolution_is_rejected():
    """**P1-6**:`got` 是集合,所以同一筆填三次照樣滿足 required ——
    而指標數的是 `len(res)`,於是「處理了 3 筆 / 需要 1 筆」。"""
    pk = _packet()
    obj = _resolved(pk)
    obj["cross_market_synthesis"]["tension_resolutions"] *= 3
    assert [p for p in sch.validate(obj, pk) if "重複" in p]


def test_coverage_counts_unique_required_tensions_only():
    """指標要回**覆蓋率**,不是「填了幾筆」—— 後者可以大於 100%。"""
    pk = _packet()
    obj = _resolved(pk)
    obj["cross_market_synthesis"]["tension_resolutions"] *= 3
    m = am.structured_metrics(obj, pk)["depth"]
    assert m["tensions_resolved"] == m["tensions_required"]
    assert m["tension_coverage_rate"] == 1.0
    assert m["duplicate_resolutions"] == 2 * m["tensions_required"]
    assert m["resolutions_grounded_both_sides"] >= 1


# ------------------------------------------------ 完整度的兩個反方向

def test_a_day_with_no_data_gaps_is_not_penalised():
    """`data_gaps=[]` 在證據完整的日子是**合法**的 ——
    先前它被算成少一段,於是好報告的完整度反而比較低。"""
    pk = _packet()
    obj = _resolved(pk)
    obj["data_gaps"] = []
    assert "data_gaps" not in am.structured_metrics(obj, pk)["sections_missing"]


def test_an_empty_object_section_is_not_counted_as_present():
    """反方向:`priced_in={}` 內部全空,而 dict 本身是 truthy ——
    **空報告被放行**,那比好報告被扣分更危險。"""
    pk = _packet()
    obj = _resolved(pk)
    obj["priced_in"] = {"already_reflected": [], "not_yet_reflected": [],
                        "evidence_ids": []}
    obj["cross_market_synthesis"] = {"dominant_driver": "",
                                     "tension_resolutions": []}
    missing = am.structured_metrics(obj, pk)["sections_missing"]
    assert "priced_in" in missing and "cross_market_synthesis" in missing


# ------------------------------------------------ 深度不得用重新分類繞過

def _shallow_and_deep():
    shallow = fx.valid_analysis()
    shallow["top_news_analysis"][0]["mechanism_steps"] = \
        shallow["top_news_analysis"][0]["mechanism_steps"][:1]
    return shallow, fx.valid_analysis()


def test_downgrading_a_news_item_is_not_an_improvement():
    """**P1-11**:把 n1 從 high 降成 medium,新聞 ID 集合不變、
    high 的數量不變、深度提示還會變少 —— 真正該加深的那則靠降級逃掉。"""
    shallow, deep = _shallow_and_deep()
    deep["top_news_analysis"][0]["materiality"] = "medium"
    ok, why = ad.deepen_is_an_improvement(shallow, deep, evidence_ids=_IDS)
    assert not ok and "降級" in why, why
    assert "n1" in why, "要說得出是哪一則"


def test_losing_an_invalidation_signal_is_not_an_improvement():
    """說得出「什麼會推翻它」的東西,不得在加深後說不出來 ——
    少了但書的報告**看起來更乾淨**,那是最難察覺的退步。"""
    shallow, deep = _shallow_and_deep()
    deep["top_news_analysis"][0]["invalidation_signal"] = ""
    ok, why = ad.deepen_is_an_improvement(shallow, deep, evidence_ids=_IDS)
    assert not ok and "invalidation_signal" in why, why


def test_a_large_confidence_jump_is_not_an_improvement():
    """0.35 → 0.95 不可能是「補了幾條因果鏈」帶來的。"""
    shallow, deep = _shallow_and_deep()
    shallow["stance"]["confidence"] = 0.35
    deep["stance"]["confidence"] = 0.95
    ok, why = ad.deepen_is_an_improvement(shallow, deep, evidence_ids=_IDS)
    assert not ok and "信心漂移" in why, why


def test_a_small_confidence_change_is_still_allowed():
    """反向:加深本來就可能讓信心小幅移動,**不得因此擋掉真正的改善**。"""
    shallow, deep = _shallow_and_deep()
    shallow["stance"]["confidence"] = 0.55
    deep["stance"]["confidence"] = 0.65
    ok, why = ad.deepen_is_an_improvement(shallow, deep, evidence_ids=_IDS)
    assert ok, why


# ------------------------------------------------ 階段:順序與完整

def _chain(*stages) -> dict:
    obj = fx.valid_analysis()
    steps, prev = [], "起點"
    for st in stages:
        nxt = f"{st}結果"
        steps.append({"from_what": prev, "to_what": nxt, "channel": "傳導",
                      "stage": st, "step_type": "inference", "evidence_ids": []})
        prev = nxt
    obj["top_news_analysis"][0]["mechanism_steps"] = steps
    obj["top_news_analysis"][0]["materiality"] = "high"
    return obj


def test_a_backwards_chain_does_not_count_as_reaching_both_layers():
    """**P1-10**:「事件 → 股價上漲」接「股價上漲 → 稼動率提升」——
    stage 集合裡 price 與 operations 都在,而因果是倒著走的。"""
    back = _chain("event", "price", "operations")
    m = ast_.depth_metrics(back, None)
    assert m["reaches_financial"] == 1, "確實碰到了財務層"
    assert m["operations_then_financial"] == 0, "倒著走不算把傳導講完"
    assert m["chains_out_of_order"] == 1


def test_an_ordered_chain_counts():
    """正向:事件 → 營運 → 營收,順序對了才算。"""
    m = ast_.depth_metrics(_chain("event", "operations", "revenue"), None)
    assert m["operations_then_financial"] == 1
    assert m["chains_out_of_order"] == 0
    assert m["operations_then_financial_rate"] == 1.0


def test_rates_are_reported_not_just_counts():
    """**1/1 與 1/5 的計數相同而品質天差地遠** —— 跨日比較要用比例。"""
    obj = _chain("event", "operations", "revenue")
    obj["top_news_analysis"][1]["materiality"] = "high"
    obj["top_news_analysis"][1]["mechanism_steps"] = [
        {"from_what": "起點", "to_what": "情緒改善", "channel": "情緒",
         "stage": "sentiment", "step_type": "inference", "evidence_ids": []}]
    m = ast_.depth_metrics(obj, None)
    assert m["high_materiality"] == 2 and m["reaches_financial"] == 1
    assert m["reaches_financial_rate"] == 0.5


def test_an_incomplete_chain_is_disclosed_in_the_letter():
    """**P1-9**:走到財務層是 advisory,加深失敗就照原樣寄出 ——
    那對「晨報不可斷」合理,對收件人是隱瞞。不擋,但要說出來。"""
    import analysis_render as ar
    stub = _chain("event", "sentiment")
    assert ast_.incomplete_chains(stub), "沒認出這條鏈停在情緒"
    assert "傳導未完成" in ar.render(stub), "信裡看不到這件事"
    # 完整的鏈不得留下這句話 —— 每天都出現的揭露等於沒有揭露
    assert "傳導未完成" not in ar.render(_chain("event", "operations", "revenue"))


# ------------------------------------------------ 生產真的接上了嗎

def test_the_production_gate_validates_against_the_packet():
    """**這一輪最嚴重的一條,而且是我自己上一批留下的。**

    上一批把選優(`deepen_is_an_improvement`)與指標(`structured_metrics`)
    都接上了 packet,唯獨主閘門仍然吃 `ids` —— 於是「有張力卻沒處理」
    「有新聞卻交空陣列」「有高重要性事件卻沒指出主導因子」這幾條規則
    **在生產從來沒有跑過**,而測試裡它們全是綠的。
    """
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    body = src[src.index("def _luna_analysis"):]
    body = body[:body.index("#: 盲評卡的落地目錄")]
    assert "_sch.validate(obj, packet)" in body, "主閘門沒有吃 packet"
    assert "_sch.validate(obj, ids)" not in body, "還留著吃 ID 集合的呼叫"
    assert "_ar.render(obj, packet)" in body, "renderer 拿不到 packet,張力抬頭印不出來"


def test_the_snapshot_probe_uses_the_production_call_shape():
    """**探針量不到的東西,版本升降只是在猜。**

    先前渲染探針餵 `render(obj)`、接受探針餵 ID 集合 —— 而生產兩邊
    都傳 packet。這個 repo 已經在 legacy prompt 上栽過同一形狀兩次。
    """
    src = (Path(__file__).resolve().parent / "test_contract_snapshots.py"
           ).read_text(encoding="utf-8")
    assert "ar.render(_render_case(pk), pk)" in src
    assert "sch.validate(o, pk)" in src
