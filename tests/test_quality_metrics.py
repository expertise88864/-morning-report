# -*- coding: utf-8 -*-
"""**指標量的是「有沒有真的做到」,不是「有沒有填欄位」**(第十九輪 P2-3)。

外審點出七種 false green,共同形狀是:

    dashboard 顯示 coverage / grounding / depth 接近 100%,
    而信裡仍然只有「台積電偏多、情緒改善」。

這個檔逐條釘住「舊指標會給高分、新指標不會」的那些輸入 ——
**指標自己也會說謊,而說謊的指標比沒有指標更糟。**
"""
import analysis_schema as sch
import evidence_packet as ep
import fixtures_analysis as fx
import quality_metrics as qm
import tension_refs as tr

_NEWS = [
    {"source_item_id": "n1", "title": "央行理監事會決議升息半碼",
     "entities": ["央行"], "source": "中央銀行", "official": True},
    {"source_item_id": "n2", "title": "台積電熊本廠恢復地震前產出水準",
     "entities": ["台積電"], "source": "公司公告", "official": True},
]


def _packet(news=None) -> dict:
    return ep.build({"QQQ": {"change_pct": 1.76},
                     "TAIFEX_OI": {"foreign_oi_net": 90038}},
                    {}, {}, news if news is not None else _NEWS, [], {},
                    as_of="2026-08-05T06:00", target_session_date="2026-08-05",
                    sanitize=str)


# ---------------------------------------------------------------- 覆蓋率

def test_a_dismissed_event_is_not_counted_as_covered():
    """**「六件必分析事件全部駁回」與「六件全部分析」不該是同一個數字。**

    驗證器接受「分析了或說明為什麼不談」是對的(那是**合格**的判準),
    但指標把兩者都算進分子就會讓 dashboard 說謊。
    """
    pk = _packet()
    need = pk["news_clusters"]["required_cluster_ids"]
    assert len(need) == 2, need
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = []
    obj["dismissed_events"] = [
        {"cluster_id": c, "why_not_material": "本次決議與上次一致,利率路徑未變",
         "supporting_evidence_ids": ["n1"],
         "revisit_trigger": "官方後續公告改變原判斷"}
        for c in need]
    m = qm.required_event_coverage(obj, pk)
    assert m["true_coverage_rate"] == 0.0, m
    assert m["dismissed"] == 2 and m["analysed"] == 0


def test_one_entity_cannot_cover_the_whole_day():
    """**一個實體撐起整份覆蓋率。** 舊判準用「實體出現在文字裡」,
    於是提一次「台積電」,當天所有相關新聞都算談過了。"""
    pk = _packet()
    text = "台積電今日走勢偏多,台積電法說在即,台積電供應鏈同步受惠。"
    m = qm.event_fingerprint_coverage(text, pk)
    assert m["clusters"] == 2 and m["covered"] == 0, m
    # 真的談到那一群的標題才算
    real = "央行理監事會決議升息半碼,對折現率假設的影響如下。"
    assert qm.event_fingerprint_coverage(real, pk)["covered"] == 1


# ---------------------------------------------------------------- 相關性

def test_a_legal_but_irrelevant_id_is_not_grounded():
    """**合法 ≠ 相關。** 驗證器已經擋了,指標也不能算它 grounded。"""
    pk = _packet()
    need = sorted(tr.required_alignment_ids(pk["signal_tensions"]))
    assert need, "這份行情本來就該產生同向訊號"
    base = {"interpretation": "同方向", "marginal_information": "確認",
            "double_count_risk": "同一批權值股"}
    obj = fx.valid_analysis()
    obj["cross_market_synthesis"]["alignment_readings"] = [
        dict(base, alignment_id=a, evidence_ids=["n1"]) for a in need]
    assert qm.alignment_grounding(obj, pk)["side_grounded_rate"] == 0.0
    obj["cross_market_synthesis"]["alignment_readings"] = [
        dict(base, alignment_id=a, evidence_ids=[a]) for a in need]
    assert qm.alignment_grounding(obj, pk)["side_grounded_rate"] == 1.0


def test_a_generic_asset_shows_up_in_the_metric():
    """`asset_id="市場"` 在舊指標裡算「有做逐標的分析」。"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "市場"
    m = qm.asset_breakdown_quality(obj)
    assert m["generic"] == 1 and m["generic_rate"] == 0.5, m
    assert qm.asset_breakdown_quality(fx.valid_analysis())["generic_rate"] == 0.0


def test_an_out_of_order_chain_does_not_count_as_complete():
    """**把股價當原因再倒推營運**,兩層都出現而順序不成立。"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["mechanism_steps"] = [
        {"from_what": "起點", "to_what": "股價上漲", "channel": "c",
         "stage": "price", "step_type": "inference", "evidence_ids": []},
        {"from_what": "股價上漲", "to_what": "稼動率提升", "channel": "c",
         "stage": "operations", "step_type": "inference", "evidence_ids": []}]
    m = qm.ordered_chain_completion(obj)
    assert m["ordered_completion_rate"] == 0.0 and m["out_of_order"] == 1
    assert qm.ordered_chain_completion(
        fx.valid_analysis())["ordered_completion_rate"] == 1.0


# ---------------------------------------------------------------- 飽和與保存

def test_one_claim_filling_the_whole_letter_is_visible():
    """四段都靠同一條主張時,claim graph 的覆蓋率是 100% ——
    **而整封信只有一個根據**。這是觀測不是門檻:某些日子確實由單一
    驅動主導,做成硬性失敗只會逼出湊數的主張。"""
    # 第二十輪 P2-5:段落清單從 `claim_map` 長出來,已含情境、觀察點與
    # 「昨夜三大重點」—— **一條主張填滿三個情境**先前完全看不到。
    import claim_map as cm
    obj = fx.valid_analysis()
    obj["executive_summary_claim_ids"] = ["c1"]
    for sec in ("stance", "priced_in", "portfolio_implications"):
        obj[sec]["claim_ids"] = ["c1"]
    for key in ("base", "bull", "bear"):
        obj["scenario_tree"][key]["claim_ids"] = ["c1"]
    for d in obj["key_drivers"]:
        d["claim_ids"] = ["c1"]
    n = len(cm.section_claim_mappings(obj))
    assert qm.claim_graph_saturation(obj)["saturation_rate"] == 1.0, n
    # 分散開來就降下來
    obj["priced_in"]["claim_ids"] = ["c2"]
    assert qm.claim_graph_saturation(obj)["saturation_rate"] == round((n - 1) / n, 3)
    # **它不擋任何東西**
    obj2 = fx.valid_analysis()
    for sec in ("stance", "priced_in", "portfolio_implications"):
        obj2[sec]["claim_ids"] = ["c1", "c2"]
    assert sch.validate(obj2, fx.ids()) == []


def test_deepen_preservation_says_what_was_lost():
    """加深弄丟東西時,指標要說得出**弄丟了哪一類**。"""
    before = fx.valid_analysis()
    after = fx.valid_analysis()
    after["cross_market_synthesis"]["alignment_readings"] = []
    before["cross_market_synthesis"]["alignment_readings"] = [
        {"alignment_id": "tension:t_x", "interpretation": "i",
         "marginal_information": "m", "double_count_risk": "d",
         "evidence_ids": []}]
    m = qm.deepen_preservation(before, after)
    assert m["preservation_rate"] < 1.0
    assert "解讀過的同向訊號" in m["lost"], m["lost"]
    assert qm.deepen_preservation(before, before)["preservation_rate"] == 1.0


# ---------------------------------------------------------------- 生產接線

def test_the_quality_block_reaches_the_durable_ledger():
    """**只在測試裡算得出來等於沒有** —— 這個 repo 反覆栽在這裡。"""
    import analysis_metrics as am
    out = am.structured_metrics(fx.valid_analysis(), _packet())
    assert "quality" in out, "指標沒有接進 structured_metrics"
    assert out["quality"]["ordered_chain"]["ordered_completion_rate"] == 1.0
    assert am.METRICS_SCHEMA_VERSION >= 5, "加了指標卻沒升 schema 版本"


def test_nothing_here_blocks_anything():
    """**有已知誤判的指標當門檻,比沒有指標更糟。**

    空輸入不得拋、不得回半個 dict 讓呼叫端自己猜。
    """
    for junk in (None, {}, {"top_news_analysis": []}):
        out = qm.quality_metrics(junk, None, "")
        assert set(out) >= {"required_event_coverage", "asset_breakdown",
                            "ordered_chain", "claim_graph"}
        assert out["asset_breakdown"]["assets"] == 0
