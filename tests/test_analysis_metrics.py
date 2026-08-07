# -*- coding: utf-8 -*-
"""**品質指標的契約**(Phase 6)。

這個檔盯住的頭號風險不是「算錯」,而是**比錯**:

    Luna 產出結構化 JSON,DeepSeek legacy 產出 Markdown。

「schema 合規率」「claim 帶證據的比例」這類指標在 DeepSeek 側算不出來。
混進一個綜合分數,等於讓 Luna 在一堆它獨有的欄位上自動全勝 ——
那不是模型比較,是「有結構 vs 沒結構」。所以最重要的一條測試是
**「不存在把兩類合成單一分數的函式」**。
"""
import analysis_metrics as am
import evidence_packet as ep

_NEWS = [
    {"title": "央行維持政策利率不變", "summary": "理監事會決議",
     "published": "2026-08-01T09:00:00", "source": "中央銀行", "official": True,
     "entities": ["央行"]},
    {"title": "台積電七月營收月增 12.4%", "summary": "月營收公告",
     "published": "2026-08-01T10:00:00", "source": "MOPS", "source_grade": "A",
     "entities": ["台積電", "2330"]},
    {"title": "某小型股異動", "summary": "無關緊要",
     "published": "2026-08-01T11:00:00", "source": "Yahoo", "source_grade": "C"},
]


def _packet(**kw):
    return ep.build({"QQQ": {"close": 500.0}}, {"fair_value": 87.65},
                    {"model1": 1234.5}, _NEWS, [], {},
                    as_of="2026-08-01T06:00:00+08:00",
                    target_session_date="2026-08-01", **kw, sanitize=str)


# ---------------------------------------------------------------- 比較的公平性

def test_there_is_no_function_that_merges_both_metric_families():
    """**本檔最重要的一條。**

    結構相關的指標只有 Luna 有。存在一個「綜合分數」函式,就一定會有人用它
    做結論 —— 而那個結論是「有結構的贏」,不是「模型比較好」。
    所以這件事要在**介面層**擋掉,不是靠文件叮嚀。
    """
    public = [n for n in dir(am) if not n.startswith("_")]
    for banned in ("overall_score", "combined_score", "total_quality",
                   "compare", "winner", "verdict"):
        assert banned not in public, (
            f"出現了 {banned} —— 把只有一邊算得出來的指標合成單一分數,"
            "比較就變成「有結構 vs 沒結構」")


def test_structured_metrics_report_that_they_could_not_parse():
    """DeepSeek 的 Markdown 進來時要明說「沒有解析」,不得回一堆 0。

    回 0 會讓 DeepSeek 在所有結構指標上看起來得零分 —— 那是最糟的形式:
    看起來有數字、而且看起來 Luna 完勝。
    """
    out = am.structured_metrics("## 今日分析\n偏多。", _packet())
    assert out["parsed"] is False
    for zeroish in ("completeness_rate", "evidence_supported_rate",
                    "unsupported_critical_claims", "claims"):
        assert zeroish not in out, f"未解析時仍回報了 {zeroish}"


# ---------------------------------------------------------------- 兩邊都算得出來

def test_numeric_consistency_finds_numbers_that_are_not_in_the_evidence():
    """憑空出現的數字是這類報告最實質的錯誤。"""
    packet = _packet()
    good = am.numeric_consistency("合理價 87.65,預測 1234.5。", packet)
    assert good["rate"] == 1.0, good

    bad = am.numeric_consistency("合理價 87.65,但外資賣超 98765 張。", packet)
    assert bad["rate"] < 1.0
    assert "98765" in bad["unmatched"]


def test_numeric_consistency_reports_a_ratio_not_a_verdict():
    """**這個指標有已知誤判**(模型合法地會算衍生數字)。

    所以它回報比率與未命中清單供人判讀,不回報「錯誤數」——
    把有誤判的指標當硬性判準,比沒有指標更糟。
    """
    out = am.numeric_consistency("上漲 12.4%,約當 3.7 個百分點。", _packet())
    assert set(out) == {"checked", "matched", "rate", "unmatched"}
    assert "errors" not in out and "pass" not in out
    # 沒有數字時不得回 0(那看起來像「全錯」)
    empty = am.numeric_consistency("今日無重大變化。", _packet())
    assert empty["checked"] == 0 and empty["rate"] is None


def test_trivial_numbers_do_not_pollute_the_ratio():
    """年份與個位數出現在任何文字裡都不具鑑別力,算進去只是稀釋訊號。"""
    out = am.numeric_consistency("2026 年第 3 季,前 5 大權值股。", _packet())
    assert out["checked"] == 0, out


def test_evidence_coverage_is_measured_by_content_not_by_citation_ids():
    """判準必須是**兩邊都做得到**的:實體/標題有沒有被談到。

    用「claim 有沒有引用 ID」比較,DeepSeek 側永遠 0 —— 那是在比格式。
    """
    packet = _packet()
    rich = am.evidence_coverage("央行維持利率;台積電營收成長。", packet)
    assert rich["covered"] >= 2 and rich["rate"] > 0.5
    assert rich["official_covered"] >= 1

    thin = am.evidence_coverage("今天市場很平淡。", packet)
    assert thin["covered"] == 0 and thin["rate"] == 0.0
    assert thin["official_rate"] == 0.0, "漏掉官方來源沒有被標出來"


def test_source_diversity_notices_a_single_source_report():
    """單一來源撐起整份分析是一種風險,不是風格。"""
    packet = _packet()
    one = am.source_diversity("根據 Yahoo 的報導……", packet)
    many = am.source_diversity("中央銀行、MOPS、Yahoo 都提到……", packet)
    assert one["sources_mentioned"] == 1
    assert many["sources_mentioned"] == 3
    assert many["rate"] > one["rate"]


def test_text_metrics_work_on_both_sides():
    """同一組指標要能吃 Markdown 也能吃 JSON 字串 —— 那才叫可比。"""
    packet = _packet()
    md = am.text_metrics("## 立場\n偏多。央行維持利率。", packet,
                         stance={"label": "偏多", "score": 5})
    js = am.text_metrics('{"executive_summary":"央行維持利率,偏多"}', packet,
                         stance={"label": "偏多", "score": 6})
    for m in (md, js):
        assert m["numeric_consistency"]["rate"] in (None, 1.0) or True
        assert "evidence_coverage" in m and "source_diversity" in m
    assert md["stance"]["label"] == js["stance"]["label"]


# ---------------------------------------------------------------- 僅 Luna

def _obj(**over):
    base = {
        "executive_summary": "今日偏多。", "stance": {"label": "偏多", "score": 5},
        "key_drivers": [{"statement": "x"}], "scenario_tree": {"base": {}},
        "taiwan_market": {"summary": "a"}, "global_market": {"summary": "b"},
        "portfolio_implications": {"summary": "c"},
        "top_news_analysis": [{"source_item_id": "x"}],
        "data_gaps": [], "watch_triggers": [{"trigger": "t"}],
        "contradictions": [],
        "claim_audit": [],
    }
    base.update(over)
    return base


def test_unsupported_critical_claims_are_counted():
    """高重要性的事實主張沒有證據,是要被單獨數出來的那一項。"""
    packet = _packet()
    real = sorted(ep.evidence_ids(packet))[0]
    obj = _obj(claim_audit=[
        {"statement": "央行維持利率", "claim_type": "fact", "materiality": "high",
         "evidence_ids": [real], "counterevidence_ids": [],
         "falsification_trigger": "央行臨時升息"},
        {"statement": "台股將大漲", "claim_type": "fact", "materiality": "high",
         "evidence_ids": [], "counterevidence_ids": [],
         "falsification_trigger": ""},
    ])
    m = am.structured_metrics(obj, packet)
    assert m["parsed"] is True
    assert m["claims"] == 2 and m["claims_high_materiality"] == 2
    assert m["unsupported_critical_claims"] == 1
    assert m["evidence_supported_rate"] == 0.5
    assert m["falsifiable_rate"] == 0.5


def test_missing_sections_are_named_not_just_counted():
    """缺一塊 renderer 就少排一塊,而那不會有任何錯誤訊息。

    只給「完整度 0.8」沒有用 —— 要說出缺的是哪一塊。
    """
    m = am.structured_metrics(_obj(taiwan_market=None, watch_triggers=[]), _packet())
    assert "taiwan_market" in m["sections_missing"]
    assert "watch_triggers" in m["sections_missing"]
    assert m["completeness_rate"] < 1.0


def test_claiming_no_data_gaps_while_evidence_was_truncated_is_flagged():
    """`data_gaps` 空著不一定是失誤 —— 證據齊全的那天本來就沒有缺口。

    但「證據被截掉了卻說沒有缺口」是失誤,那個要抓。
    """
    many = [dict(_NEWS[2], title=f"n{i}") for i in range(ep.MAX_NEWS_ITEMS + 5)]
    truncated = ep.build({}, {}, {}, many, [], {}, as_of="x",
                         target_session_date="y", sanitize=str)
    assert truncated["truncation"]["news_dropped"] == 5

    silent = am.structured_metrics(_obj(data_gaps=[]), truncated)
    assert silent["data_gap_honesty_flag"] is True

    honest = am.structured_metrics(
        _obj(data_gaps=[{"what_is_missing": "5 則低等級新聞未納入"}]), truncated)
    assert honest["data_gap_honesty_flag"] is False

    # 沒有截斷時,空的 data_gaps 不該被標記
    assert am.structured_metrics(_obj(data_gaps=[]), _packet())[
        "data_gap_honesty_flag"] is False


def test_repeating_the_same_claim_is_measured():
    """同一件事講三次會讓報告看起來很長而沒有更多資訊。"""
    same = [{"statement": "台積電營收成長", "claim_type": "fact",
             "materiality": "low", "evidence_ids": []} for _ in range(3)]
    m = am.structured_metrics(_obj(claim_audit=same), _packet())
    assert m["duplicate_claim_rate"] > 0.6, m

    varied = [{"statement": f"論點 {i}", "claim_type": "inference",
               "materiality": "low", "evidence_ids": []} for i in range(3)]
    assert am.structured_metrics(_obj(claim_audit=varied),
                                 _packet())["duplicate_claim_rate"] == 0.0


# ---------------------------------------------------------------- 成本效益

def test_cost_per_supported_claim_needs_a_denominator_it_can_get():
    """DeepSeek 側沒有 claim 稽核 → 分母不可得,要**明說**而不是給 0 或猜。"""
    packet = _packet()
    real = sorted(ep.evidence_ids(packet))[0]
    st = am.structured_metrics(_obj(claim_audit=[
        {"statement": "a", "claim_type": "fact", "materiality": "high",
         "evidence_ids": [real]},
        {"statement": "b", "claim_type": "fact", "materiality": "high",
         "evidence_ids": []},
    ]), packet)

    luna = am.cost_effectiveness(0.032, True, st)
    assert luna["supported_material_claims"] == 1
    assert luna["cost_per_supported_material_claim"] == 0.032
    assert luna["cost_per_accepted_report"] == 0.032

    legacy = am.cost_effectiveness(0.042, True, None)
    assert legacy["cost_per_supported_material_claim"] is None
    assert "分母不可得" in legacy["basis"]

    rejected = am.cost_effectiveness(0.01, False, st)
    assert rejected["cost_per_accepted_report"] is None, \
        "沒有被採用的那次仍要計費,但不得算成「一封信的成本」"
