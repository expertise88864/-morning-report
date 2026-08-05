# -*- coding: utf-8 -*-
"""**第二十輪 P1-4/P1-5/P2-1/P2-2/P2-5/P2-7。**

這一批的共同形狀是**清單漂移**:同一件事寫在好幾個地方,而其中一份改了、
別的沒跟上。

  * 段落→主張的對照表有四個消費者(驗證器、飽和率、加深保存、渲染),
    schema 加了情境與觀察點的回指之後**只有驗證器知道**;
  * 泛稱判準在 validator 與 metric 各一份;
  * 命名空間的說明在 prompt、schema、Python advisory 各一份,而且互相矛盾。

**清單要從一個地方長出來** —— 這個 repo 已經栽過同型的三次。
"""
import analysis_depth as ad
import analysis_schema as sch
import claim_map as cm
import evidence_namespaces as ns
import evidence_packet as ep
import fixtures_analysis as fx
import news_clusters as nc
import news_facts as nf
import quality_metrics as qm

_IDS = fx.ids()


# ---------------------------------------------------------------- P2-5 對照表

def test_the_mapping_covers_every_section_that_can_cite_a_claim():
    """**寫死的清單漂移一次就再也對不回來。**"""
    got = set(cm.section_claim_mappings(fx.valid_analysis()))
    assert {"executive_summary", "stance", "priced_in",
            "portfolio_implications"} <= got
    assert any(k.startswith("scenario_tree.") for k in got), "情境不在對照表裡"
    assert any(k.startswith("key_drivers[") for k in got), \
        "「昨夜三大重點」不在對照表裡 —— 那是 Email 的第一段"


def test_a_key_driver_must_cite_a_claim():
    """**P1-5**:讀者最先看到的三條先前完全在 claim 圖之外 ——
    它們可以與正式稽核互相矛盾而沒有任何東西會紅。"""
    obj = fx.valid_analysis()
    obj["key_drivers"][0]["claim_ids"] = []
    assert [p for p in sch.validate(obj, _IDS)
            if "key_drivers[0] 沒有回指" in p]
    obj["key_drivers"][0]["claim_ids"] = ["c99"]
    assert [p for p in sch.validate(obj, _IDS)
            if "key_drivers[0] 的 claim_ids 指向不存在" in p]


def test_a_scenario_is_part_of_the_same_mapping():
    obj = fx.valid_analysis()
    obj["scenario_tree"]["bull"]["claim_ids"] = []
    assert [p for p in sch.validate(obj, _IDS)
            if "scenario_tree.bull 沒有回指" in p]


def test_one_claim_filling_the_scenarios_shows_up_in_saturation():
    """**先前飽和率只看四段** —— 一條主張可以填滿三個情境而指標顯示分散。"""
    obj = fx.valid_analysis()
    for key in ("base", "bull", "bear"):
        obj["scenario_tree"][key]["claim_ids"] = ["c1"]
    for d in obj["key_drivers"]:
        d["claim_ids"] = ["c1"]
    obj["executive_summary_claim_ids"] = ["c1"]
    for sec in ("stance", "priced_in", "portfolio_implications"):
        obj[sec]["claim_ids"] = ["c1"]
    assert qm.claim_graph_saturation(obj)["saturation_rate"] == 1.0


# ---------------------------------------------------------------- P1-5 尺度

def test_a_longer_claim_can_support_a_shorter_section():
    """**方向不能搞反,但「更長」也不是無限相容。**

    第二十二輪 P1-5:上一版寫 `horizon_covers("intraday", "1-4w") is True`
    —— 那是把 `got >= want` 這條算式的副作用釘成通過條件。1-4 週的
    結構性主張撐不起一個「今天」的段落(「這個月看多」推不出
    「今天會漲」)。現在是宣告式矩陣,**相鄰一階以內**才相容。
    """
    assert cm.horizon_covers("intraday", "1-5d") is True    # 相鄰:相容
    assert cm.horizon_covers("intraday", "1-4w") is False   # 差兩階:不相容
    assert cm.horizon_covers("1-4w", "intraday") is False
    assert cm.horizon_covers("1-5d", "1-5d") is True
    # 不認得的尺度不做判斷 —— 誤擋比漏擋難察覺
    assert cm.horizon_covers("1-5d", "某個新尺度") is True


# ---------------------------------------------------------------- P2-7 佐證

def _packet_single_source() -> dict:
    return ep.build({}, {}, {}, fx.news(), [], {}, as_of="2026-08-05T06:00",
                    target_session_date="2026-08-05", sanitize=str)


def test_the_model_cannot_claim_more_corroboration_than_the_data_has():
    """**把 single_source 寫成 multi_source 是讓讀者高估可信度。**"""
    pk = _packet_single_source()
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["corroboration_assessment"] = "multi_source"
    hits = [p for p in sch.validate(obj, pk) if "不得往上寫" in p]
    assert hits and "single_source" in hits[0], hits


def test_writing_it_weaker_than_the_data_is_allowed():
    """反向:**實際多方證實而保守寫成單一來源,只是更謹慎** ——
    擋它沒有保護到任何人。"""
    news = [dict(n, source=f"媒體{i}") for i, n in enumerate(fx.news())]
    news.append(dict(news[0], source_item_id="n3", source="第三家"))
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    obj = fx.valid_analysis()
    assert not [p for p in sch.validate(obj, pk) if "不得往上寫" in p]


def test_a_single_source_event_must_say_what_to_hold_back():
    """**「無」等於沒有揭露。**"""
    pk = _packet_single_source()
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["source_caveat"] = "無"
    assert [p for p in sch.validate(obj, pk) if "沒有寫 source_caveat" in p]
    obj["top_news_analysis"][0]["source_caveat"] = ""
    assert [p for p in sch.validate(obj, pk) if "沒有寫 source_caveat" in p]


def test_the_caveat_reaches_the_letter():
    """**「沒發生」與「只有一家說」在信裡先前長得一樣。**"""
    import analysis_render as ar
    text = ar.render(fx.valid_analysis())
    assert "僅單一來源,未經其他媒體證實" in text, text[:200]
    # 多方證實時不印 —— 每則都掛一句等於沒有揭露
    obj = fx.valid_analysis()
    for n in obj["top_news_analysis"]:
        n["corroboration_assessment"] = "multi_source"
        n["source_caveat"] = "無"
    assert "僅單一來源" not in ar.render(obj)


# ---------------------------------------------------------------- P2-1 代表

def test_a_vague_short_title_does_not_split_the_event():
    """**最小 ID 當代表會確定性 over-split。**

    「A 短而模糊、ID 最小;B、C 各自詳細描述同一事件」時,
    B≈C 而 A≉B —— 先前是 A 一群、B+C 一群,同一事件被分析兩次。
    代表改成官方優先、其次資訊量高的那則。
    """
    items = [
        {"source_item_id": "a", "title": "台積電恢復", "entities": ["台積電"],
         "source": "甲報"},
        {"source_item_id": "b", "title": "台積電熊本廠恢復地震前產出水準",
         "entities": ["台積電"], "source": "乙報"},
        {"source_item_id": "c", "title": "台積電熊本廠已恢復地震前的產出水準",
         "entities": ["台積電"], "source": "丙報"},
    ]
    groups = nc.clusters(items)
    assert len(groups) == 1, [g["member_source_ids"] for g in groups]
    # 順序無關仍然成立
    assert nc.clusters(items) == nc.clusters(list(reversed(items)))


def test_an_official_item_is_preferred_as_the_representative():
    items = [{"source_item_id": "z", "title": "台積電熊本廠恢復地震前產出水準",
              "entities": ["台積電"], "source": "公司公告", "official": True},
             {"source_item_id": "a", "title": "台積電熊本廠恢復地震前產出",
              "entities": ["台積電"], "source": "甲報"}]
    g = nc.clusters(items)[0]
    assert g["official"] is True
    assert set(g["member_source_ids"]) == {"a", "z"}


# ---------------------------------------------------------------- P2-2 事實

def test_the_same_number_in_two_different_roles_gets_two_facts():
    """**「營收 80 億美元」與「資本支出 80 億美元」是兩個數字。**

    先前去重鍵只有 (值, 單位) —— 第二個沒有自己的 ID,模型引用不到。
    """
    out = nf.extract("營收 80 億美元,資本支出 80 億美元")
    assert len(out) == 2, out
    assert {f["value"] for f in out} == {80.0}


def test_a_genuine_repeat_is_still_deduped():
    """反向:語境相同的重複仍然只算一次 —— 否則去重等於沒做。"""
    out = nf.extract("3 億元、3 億元、5 億元")
    assert [(f["value"], f["unit"]) for f in out] == [(3.0, "億元"),
                                                      (5.0, "億元")]


# ---------------------------------------------------------------- P1-4 身分

def _shallow():
    o = fx.valid_analysis()
    o["top_news_analysis"][0]["mechanism_steps"] = \
        o["top_news_analysis"][0]["mechanism_steps"][:1]
    return o


def test_deepen_cannot_flip_a_claim_direction():
    """**ID 不變而 direction 翻面,是換一份報告。**

    key_driver 的方向相容檢查(第二十一輪 P1-5)會先擋住單獨翻 claim
    的版本 —— 反例要**整組同翻**保持合法,身分保存才是被測的那一條。
    """
    deep = fx.valid_analysis()
    deep["claim_audit"][0]["direction"] = "bearish"
    deep["key_drivers"][0]["direction"] = "bearish"
    assert sch.validate(deep, _IDS) == [], "第二版本身要是合法的"
    ok, why = ad.deepen_is_an_improvement(_shallow(), deep, evidence_ids=_IDS)
    assert not ok and "稽核過的主張" in why, why


def test_deepen_cannot_swap_a_claims_evidence():
    """反例要**只**違反身分保存 —— 換掉 c1 的證據會讓 key_driver 的
    證據交集檢查先紅(第二十一輪 P1-5 加的),所以 key_driver 同步換,
    讓第二版完全合法。"""
    deep = fx.valid_analysis()
    deep["claim_audit"][0]["evidence_ids"] = ["n2"]
    deep["key_drivers"][0]["evidence_ids"] = ["n2"]
    assert sch.validate(deep, _IDS) == [], "第二版本身要是合法的"
    ok, why = ad.deepen_is_an_improvement(_shallow(), deep, evidence_ids=_IDS)
    assert not ok and "稽核過的主張" in why, why


def test_deepen_cannot_move_counterevidence_between_claims():
    """**先前反證是全域集合** —— 在 claim A、B 之間互換時集合完全相同。"""
    before = _shallow()
    before["claim_audit"][0]["counterevidence_ids"] = ["n2"]
    deep = fx.valid_analysis()
    deep["claim_audit"][0]["counterevidence_ids"] = []
    deep["claim_audit"][1]["counterevidence_ids"] = ["n2"]
    ok, why = ad.deepen_is_an_improvement(before, deep, evidence_ids=_IDS)
    assert not ok and "反面證據" in why, why


def test_deepen_cannot_rewrite_a_tension_resolution():
    before = _shallow()
    before["cross_market_synthesis"]["tension_resolutions"] = [
        {"tension_id": "tension:t_x", "resolution": "原本的調和",
         "dominant_side": "left", "why": "原因", "decision_rule": "判準",
         "evidence_ids": []}]
    deep = fx.valid_analysis()
    deep["cross_market_synthesis"]["tension_resolutions"] = [
        {"tension_id": "tension:t_x", "resolution": "換一種說法",
         "dominant_side": "right", "why": "原因", "decision_rule": "判準",
         "evidence_ids": []}]
    ok, why = ad.deepen_is_an_improvement(before, deep, evidence_ids=_IDS)
    assert not ok and "調和過的張力" in why, why


# ---------------------------------------------------------------- P2-6 宣告

def test_the_namespaces_have_exactly_one_declaration():
    """**prompt、schema 說明、Python advisory 先前三邊各說各話。**"""
    import prompt_profiles as pp
    dev = pp.LUNA_DEVELOPER_INSTRUCTIONS
    for prefix, _, _ in ns.NAMESPACES:
        assert f"`{prefix}`" in dev, f"{prefix} 沒有出現在 prompt"
        assert f"`{prefix}`" in sch._EVIDENCE_IDS["description"], \
            f"{prefix} 沒有出現在 schema 說明"
    # 量化錨點的清單也是同一份宣告
    import analysis_stages as ast_
    assert set(ns.ANCHOR_PREFIXES) == set(ast_._ANCHOR_NAMESPACES)
    for prefix in ns.ANCHOR_PREFIXES:
        assert f"`{prefix}`" in dev
