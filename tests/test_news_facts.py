# -*- coding: utf-8 -*-
"""**新聞裡的數字要變成可引用、可核對的事實**(深度加強第二批)。

借自外部專案的兩個做法,取純規則做得到的部分:

  * 結構化事件抽取(Giveme5W1H 家族)→ 這裡抽**帶單位的數字**:
    「80 億美元訂單」先前在 registry 裡是 value=None ——
    模型抄成 8 億,檢查器只看得到「引用了 n3」。
  * 內容指紋去重(RSS 聚合器)→ 同一家來源、幾乎同一個標題
    = 改版重發,上游以 ID 去重擋不住(改版常拿到新 ID)。
"""
import analysis_depth as ad
import evidence_packet as ep
import evidence_registry as er
import fixtures_analysis as fx
import news_facts as nf
import quality_metrics as qm


# ---------------------------------------------------------------- 抽取本身

def test_numbers_with_units_are_extracted_with_context():
    out = nf.extract("Broadcom 獲 80 億美元訂單,相當於一季出貨的 3.5%")
    assert out[0]["value"] == 80.0 and out[0]["unit"] == "億美元"
    assert "訂單" in out[0]["quote"], "上下文要看得出這個數字在講什麼"
    assert out[1] == {"value": 3.5, "unit": "%",
                      "quote": out[1]["quote"]} and "%" in out[1]["quote"]


def test_a_number_without_a_unit_is_noise_not_a_fact():
    """「2026」「第 3 名」沒有單位 —— 噪音遠大於訊號。"""
    assert nf.extract("第 3 名,2026 年報告") == []


def test_thousands_separators_and_lots_are_handled():
    out = nf.extract("外資買超 1,234 億元,台指期淨空 90,038 口")
    assert {(f["value"], f["unit"]) for f in out} == {(1234.0, "億元"),
                                                      (90038.0, "口")}


def test_extraction_is_capped_and_deduped():
    """一則塞十幾個數字時,後面多半是背景 —— 全掛 ID 會讓
    「引用了 fact:」失去「引用了重點數字」的意思。"""
    # **重複要在封頂之前擋** —— 「3 億元」出現兩次時,先前的突變驗證
    # 抓到:去重拿掉後這條照樣綠,因為重複排在第七位、先被封頂吃掉了。
    # 反例要讓重複出現在最前面。
    out = nf.extract("3 億元、3 億元、5 億元")
    assert [(f["value"], f["unit"]) for f in out] == [(3.0, "億元"),
                                                      (5.0, "億元")]
    text = "、".join(f"{i} 億元" for i in range(1, 20))
    assert len(nf.extract(text)) == nf.MAX_FACTS_PER_ITEM


# ---------------------------------------------------------------- registry

def test_facts_reach_the_registry_with_value_and_unit():
    """**引用檢查從「引用了 n3」升級成「引用了 80 億美元那個數字」。**"""
    news = [{"source_item_id": "n9", "title": "Broadcom 獲 80 億美元訂單",
             "summary": "相當於一季出貨的 3.5%。", "source": "Reuters",
             "entities": ["Broadcom"], "published": "2026-08-05T05:00"}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    reg = er.registry(pk)
    assert reg["fact:n9.0"]["value"] == 80.0
    assert reg["fact:n9.0"]["unit"] == "億美元"
    assert reg["fact:n9.0"]["as_of_precision"] == "source"
    assert "fact:n9.1" in reg, "摘要裡的數字也要接在標題後面"
    # 引用得到(registry 是唯一真相來源)
    assert "fact:n9.0" in ep.evidence_ids(pk)


def test_a_fact_anchor_satisfies_the_vertical_advisory():
    """**新聞側的數字也是量化錨點** —— 縱向鏈錨在 fact: 上不再觸發加深。"""
    obj = fx.valid_analysis()
    for st in obj["top_news_analysis"][0]["mechanism_steps"]:
        st["evidence_ids"] = ["n1"]
        st["step_type"] = "inference"
    assert [a for a in ad.depth_advisories(obj) if "錨" in a], "沒有錨要提示"
    obj["top_news_analysis"][0]["mechanism_steps"][0]["evidence_ids"] = ["fact:n1.0"]
    assert not [a for a in ad.depth_advisories(obj) if "錨" in a]


# ---------------------------------------------------------------- 改版重發

def test_a_republished_title_from_the_same_outlet_is_dropped():
    """同源、同標題、不同 ID = 改版重發 —— 佔兩個名額還灌高 size。"""
    dup = [{"source_item_id": "a1", "title": "台積電熊本廠恢復至地震前水準",
            "entities": ["台積電"], "source": "經濟日報"},
           {"source_item_id": "a2", "title": "台積電熊本廠恢復至地震前水準",
            "entities": ["台積電"], "source": "經濟日報"},
           {"source_item_id": "a3", "title": "台積電熊本廠恢復至地震前水準",
            "entities": ["台積電"], "source": "工商時報"}]
    pk = ep.build({}, {}, {}, dup, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    kept = [n["source_item_id"] for n in pk["news"]]
    assert kept == ["a1", "a3"], kept
    assert pk["truncation"]["near_duplicates_dropped"] == 1
    # **跨來源永不去重** —— 兩家寫一樣的標題是常態,那是分群的工作。
    assert "a3" in kept


# ---------------------------------------------------------------- 佐證等級

def test_clusters_carry_their_corroboration_level():
    news = [
        {"source_item_id": "n1", "title": "央行決議升息", "entities": ["央行"],
         "source": "中央銀行", "official": True},
        {"source_item_id": "n2", "title": "台積電獨家消息曝光",
         "entities": ["台積電"], "source": "某周刊"},
        {"source_item_id": "n3", "title": "聯發科法說會下修展望",
         "entities": ["聯發科"], "source": "經濟日報"},
        {"source_item_id": "n4", "title": "聯發科法說會下修全年展望",
         "entities": ["聯發科"], "source": "工商時報"},
    ]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    lv = {c["cluster_id"]: c["corroboration"]
          for c in pk["news_clusters"]["clusters"]}
    assert lv["cluster:n1"] == "official"
    assert lv["cluster:n2"] == "single_source"
    assert lv["cluster:n3"] == "multi_source"


def test_single_source_exposure_is_measured():
    """**單一來源不是不能分析,問題是不標示** —— 這裡量整體曝險。"""
    news = [{"source_item_id": "n1", "title": "台積電獨家消息曝光",
             "entities": ["台積電"], "source": "某周刊"},
            {"source_item_id": "n2", "title": "台積電法說會下週登場",
             "entities": ["台積電"], "source": "經濟日報"}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    obj = fx.valid_analysis()          # n1 是 high、n2 是 medium
    m = qm.corroboration_exposure(obj, pk)
    assert m["high_analysed"] == 1 and m["from_single_source"] == 1
    assert m["single_source_rate"] == 1.0


def test_anchor_usage_is_measured_by_kind():
    obj = fx.valid_analysis()          # fixture 錨在 market: 上
    m = qm.fact_anchor_usage(obj)
    assert m["anchored_market"] == 1 and m["unanchored"] == 0
    assert m["anchored_rate"] == 1.0
    obj["top_news_analysis"][0]["mechanism_steps"][0]["evidence_ids"] = ["fact:n1.0"]
    assert qm.fact_anchor_usage(obj)["anchored_fact"] == 1


# ---------------------------------------------------------------- 生產接線

def test_the_prompt_teaches_both_rules():
    """規則不進 prompt,模型不會憑空做到。"""
    import prompt_profiles as pp
    pk = ep.build({}, {}, {}, fx.news(), [], {}, as_of="x",
                  target_session_date="y", sanitize=str)
    dev = pp.build_luna_bundle(pk)["developer_instructions"]
    assert "fact:" in dev and "numeric_facts" in dev
    assert "single_source" in dev and "未經其他媒體證實" in dev


def test_quality_metrics_carry_the_two_new_blocks():
    pk = ep.build({}, {}, {}, fx.news(), [], {}, as_of="x",
                  target_session_date="y", sanitize=str)
    out = qm.quality_metrics(fx.valid_analysis(), pk)
    assert "corroboration" in out and "fact_anchors" in out
