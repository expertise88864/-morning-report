# -*- coding: utf-8 -*-
"""**Luna JSON → 晨報 Markdown 的契約**。

驗收條件刻意不是「我覺得格式對」,而是**既有的截斷偵測器**
`morning_report._analysis_complete_enough` —— 它是「這份 Markdown 真的能用」
在這個 repo 裡唯一的定義:少了立場或總結、或立場解析不出來,
頂部 KPI 與結論卡會變成「—」。
"""
import analysis_render as ar
import morning_report as mr
from llm_postprocess import _extract_stance, _extract_summary


def _obj(**over):
    base = {
        "executive_summary": "美股走弱但台股籌碼穩,今日偏中性、留意台積電法說。",
        "market_regime": {"label": "震盪", "evidence_ids": []},
        "stance": {"label": "中性", "score": 1, "confidence": 0.6,
                   "time_horizon": "1-5d", "rationale": "多空訊號互抵。"},
        "key_drivers": [
            {"statement": "費半下跌拖累台股開盤", "claim_type": "inference",
             "direction": "bearish", "materiality": "high", "confidence": 0.7,
             "horizon": "intraday", "evidence_ids": ["n1"],
             "counterevidence_ids": [], "falsification_trigger": "夜盤翻紅"},
            {"statement": "外資期貨轉多", "claim_type": "fact",
             "direction": "bullish", "materiality": "medium", "confidence": 0.9,
             "horizon": "1-5d", "evidence_ids": ["n2"],
             "counterevidence_ids": [], "falsification_trigger": "留倉轉空"},
        ],
        "scenario_tree": {
            "base": {"narrative": "區間震盪", "probability": 0.6, "triggers": []},
            "bull": {"narrative": "站回月線", "probability": 0.25, "triggers": []},
            "bear": {"narrative": "跌破季線", "probability": 0.15, "triggers": []},
            "invalidation_triggers": ["台積電法說釋出降價訊號"],
        },
        "priced_in": {"already_reflected": ["費半跌幅"], "not_yet_reflected": []},
        "taiwan_market": {"summary": "台股量能偏低。", "taiex_view": "區間",
                          "tsmc_view": "守月線", "evidence_ids": []},
        "global_market": {"summary": "美股收黑。",
                          "us_to_tw_linkage": "費半 → 台積電 ADR → 2330",
                          "evidence_ids": []},
        "portfolio_implications": {"summary": "維持核心部位。",
                                   "actions_to_consider": [], "risks": ["法說不如預期"]},
        "top_news_analysis": [{"source_item_id": "n1",
                               "why_it_matters": "費半權重股財測下修",
                               "direction": "bearish", "materiality": "high",
                               "persistence": "延續"}],
        "contradictions": [{"topic": "外資方向", "supporting_ids": [],
                            "opposing_ids": [], "resolution": "期貨轉多但現貨賣超,以現貨為準"}],
        "data_gaps": [{"what_is_missing": "當日融資餘額",
                       "impact_on_conclusions": "散戶情緒判斷保守"}],
        "watch_triggers": [{"trigger": "台積電法說", "why": "指引決定季線方向",
                            "horizon": "1-5d"}],
        "claim_audit": [],
    }
    base.update(over)
    return base


def test_the_rendered_report_passes_the_existing_truncation_detector():
    """**本檔最重要的一條。**

    `_analysis_complete_enough` 是既有的驗收器。渲染出來的東西過不了它,
    生產就會判定「輸出截斷」而重試 → 重試也一樣 → 走降級文字。
    也就是說 Luna 跑得再好,信裡看到的仍是備援版。
    """
    md = ar.render(_obj())
    assert md, "渲染回了空字串"
    assert mr._analysis_complete_enough(md), (
        "渲染結果過不了既有的截斷偵測器 —— 生產會判定截斷並走降級文字")


def test_the_stance_and_summary_are_parseable_by_the_existing_extractors():
    """頂部 KPI 條與結論卡靠這兩個解析器;解析不出來就變「—」。"""
    md = ar.render(_obj())
    st = _extract_stance(md)
    assert st["label"] == "中性", st
    assert st["score"] == 1, st
    assert _extract_summary(md).startswith("美股走弱"), _extract_summary(md)


def test_section_titles_match_the_constants_the_pipeline_uses():
    """標題自創不會有錯誤訊息,只會讓那些段落在信裡消失。"""
    assert ar.SECTION_TECH == mr._SECTION_TECH
    assert ar.SECTION_OTHER == mr._SECTION_OTHER
    assert ar.SECTION_WORLD == mr._SECTION_WORLD
    assert ar.SECTION_TOP3 == mr._SECTION_TOP3
    md = ar.render(_obj())
    for title in (ar.SECTION_TOP3, ar.SECTION_WORLD, ar.SECTION_TECH,
                  ar.SECTION_OTHER, ar.SECTION_STANCE, ar.SECTION_SUMMARY):
        assert f"## {title}" in md, f"渲染結果缺少段落:{title}"


def test_a_report_without_a_stance_renders_to_nothing_not_to_half():
    """**回半份比不回更糟** —— 信寄出去了但少了一半,而且沒有任何錯誤。

    呼叫端靠空字串決定要不要走降級路徑。
    """
    assert ar.render(_obj(stance={"label": "", "score": 1})) == ""
    assert ar.render(_obj(executive_summary="")) == ""
    assert ar.render(None) == ""
    assert ar.render("不是 dict") == ""
    assert ar.render({}) == ""


def test_claims_carry_their_type_and_confidence_into_the_text():
    """**推論不得被寫成事實。**

    這是 Luna 特化相對於既有散文的實質增量:讀的人看得出哪一句是推論、
    信心多少。把它們渲染掉等於把那個增量丟掉。
    """
    md = ar.render(_obj())
    assert "推論" in md, "claim_type 沒有進到信裡"
    assert "信心 70%" in md, "confidence 沒有進到信裡"


def test_data_gaps_and_contradictions_reach_the_email():
    """只記在 manifest 等於沒有揭露 —— 收件人看到的是一份看起來完整的報告。"""
    md = ar.render(_obj())
    assert "資料缺口" in md and "當日融資餘額" in md
    assert "證據衝突與調和" in md and "以現貨為準" in md
    assert "失效條件" in md and "台積電法說釋出降價訊號" in md


def test_scenarios_carry_their_probabilities():
    """情境沒有機率就只是三段散文,判讀不出模型到底偏哪邊。"""
    md = ar.render(_obj())
    # r1(Codex,#5):機率是**模型主觀估計**,渲染必須標明出處 ——
    # 一封財經信裡的「基準 60%」讀起來就像 Python 算的。
    assert "基準（模型主觀機率 60%）" in md, md
    assert "偏空（模型主觀機率 15%）" in md, md


def test_rendering_is_deterministic():
    """同樣的 JSON 必須渲染成同樣的字。

    不確定的渲染會讓「兩天的差異」混進排版噪音,而十天實驗要比的是內容。
    """
    obj = _obj()
    assert len({ar.render(obj) for _ in range(5)}) == 1


def test_rendering_survives_partial_objects():
    """欄位缺一塊不得整份渲染失敗 —— strict schema 保證形狀,
    但 repair 之後的物件、或未來的 schema 版本可能少東西。"""
    thin = {"executive_summary": "今日中性。",
            "stance": {"label": "中性", "score": 0}}
    md = ar.render(thin)
    assert md and mr._analysis_complete_enough(md)
    for junk in ({"executive_summary": "x", "stance": {"label": "中性"},
                  "key_drivers": "不是清單"},
                 {"executive_summary": "x", "stance": {"label": "中性"},
                  "top_news_analysis": [None, 3]},
                 {"executive_summary": "x", "stance": {"label": "中性"},
                  "scenario_tree": "不是物件"}):
        assert isinstance(ar.render(junk), str)
