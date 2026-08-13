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


def test_section_titles_are_honest_and_all_appear():
    """**這條測試原本要求的是錯的事**(第十五輪 P1-3)。

    它斷言 `ar.SECTION_TECH == mr._SECTION_TECH` 等等,也就是要求渲染層
    沿用 legacy prompt 的段落名。用意是「標題自創不會有錯誤訊息,只會讓
    那些段落在信裡消失」——但**跟一個錯的名字一致不是優點**:

      * `global_market`(美股→台股連動)頂著「世界大事速覽」,
        而那一段的定義是**股市之外的世界**;
      * `taiwan_market.tsmc_view` 頂著「**其他**類股資訊」。

    Luna 的 schema 沒有那些欄位對應的概念,所以正確的做法是**改名說實話**,
    而不是為了與 legacy 一致而繼續掛錯招牌。判準因此改成兩條:
    (a) 渲染層宣告的每個段落都要真的出現在輸出裡(原本的用意保留);
    (b) **不得**再使用那幾個語意對不上的 legacy 名字。
    """
    md = ar.render(_obj())
    assert ar.SECTION_TOP3 == mr._SECTION_TOP3, "『昨夜三大重點』的語意兩邊相同,應保持一致"
    for title in (ar.SECTION_TOP3, ar.SECTION_GLOBAL, ar.SECTION_NEWS,
                  ar.SECTION_TW, ar.SECTION_STANCE, ar.SECTION_SUMMARY):
        assert f"## {title}" in md, f"渲染結果缺少段落:{title}"
    for wrong in (mr._SECTION_WORLD, mr._SECTION_TECH, mr._SECTION_OTHER):
        assert wrong not in md, (
            f"渲染層又掛回語意對不上的 legacy 段落名:{wrong}")


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


def test_scenario_narratives_reach_the_email_but_model_probabilities_do_not():
    """情境敘述要進信;**模型自訂的機率不進**(數字權威屬於 Python)。"""
    md = ar.render(_obj())
    # r2(Codex,#5):**機率不進信件。** 信裡出現的數字必須是 Python 算的,
    # 而情境機率沒有任何 Python 來源;標明「模型主觀」仍不滿足那個不變式。
    # 情境**敘述**要在(那是判讀的內容),數字留在 JSON 供指標使用。
    assert "基準" in md and "區間震盪" in md
    assert "偏空" in md and "跌破季線" in md
    assert "60%" not in md and "15%" not in md, f"模型自訂的機率進了信件:{md}"


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


# ------------------------------------------- 第三十二輪 P1-3(選項 B)

def test_a_universe_only_asset_is_labelled_speculative():
    """只靠當日 universe 放行的標的要標〔推測性傳導〕——
    universe 證明它是真股票,證明不了這件事會傳導到它。"""
    import fixtures_analysis as fx
    import analysis_render as ar
    import evidence_packet as ep
    pk = ep.build({"QQQ": {"close": 500.0, "change_pct": 1.0}}, {}, {},
                  fx.news(), [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    pk["tw_universe"] = [{"code": "3661", "name": "世芯-KY"}]
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"] = [
        dict(obj["top_news_analysis"][0]["affected_assets"][0],
             asset_id="3661"),
        dict(obj["top_news_analysis"][0]["affected_assets"][0],
             asset_id="2330")]
    text = ar.render(obj, pk)
    lines = [x for x in text.splitlines() if "3661" in x]
    assert lines and "推測性傳導" in lines[0], lines
    core = [x for x in text.splitlines() if "2330:" in x]
    assert core and "推測性傳導" not in core[0], core


def test_a_named_or_core_asset_is_not_labelled():
    """新聞主角與核心標的不標 —— 標籤只給「真的但沒宣告」那一層。"""
    import fixtures_analysis as fx
    import analysis_render as ar
    import evidence_packet as ep
    pk = ep.build({"QQQ": {"close": 500.0, "change_pct": 1.0}}, {}, {},
                  fx.news(), [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    obj = fx.valid_analysis()
    text = ar.render(obj, pk)
    assert "推測性傳導" not in text, "基準 fixture 不該有推測層標的"
    # **主角不標**(只靠這一條分勝負的反例):3661 在 universe 裡、
    # 不是核心也不是宣告邊,但它就是這則新聞點名的主角 ——
    # 名字在新聞裡的不是推測。
    pk2 = ep.build({"QQQ": {"close": 500.0, "change_pct": 1.0}}, {}, {},
                   [{"source_item_id": "n1",
                     "title": "世芯-KY 法說會釋出樂觀展望",
                     "entities": ["世芯-KY", "3661"], "source": "X",
                     "source_name": "X"}],
                   [], {}, as_of="x", target_session_date="y", sanitize=str)
    pk2["tw_universe"] = [{"code": "3661", "name": "世芯-KY"}]
    obj2 = fx.valid_analysis()
    obj2["top_news_analysis"] = [dict(obj2["top_news_analysis"][0],
                                      source_item_id="n1")]
    obj2["top_news_analysis"][0]["affected_assets"] = [
        dict(obj2["top_news_analysis"][0]["affected_assets"][0],
             asset_id="3661")]
    t2 = ar.render(obj2, pk2)
    named = [x for x in t2.splitlines() if "3661" in x]
    assert named and "推測性傳導" not in named[0], named


def test_a_broken_declared_layer_is_not_silent(monkeypatch, capsys):
    """宣告層炸掉時 universe fallback 仍在,但要留下 ::warning:: ——
    宣告過的台股被錯標成推測層,沒有痕跡就查不到為什麼(外審 r1)。"""
    import analysis_validate as av
    import sector_map
    monkeypatch.setattr(sector_map, "declared_neighbours",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("宣告層炸了")))
    pk = {"tw_universe": [{"code": "3661", "name": "世芯-KY"}],
          "news": [{"source_item_id": "n1", "entities": ["WTI"],
                    "title": "油價新聞"}]}
    item = {"source_item_id": "n1", "entities": ["WTI"], "title": "油價新聞"}
    tier = av.transmission_tier("3661", item, pk)
    assert tier == "universe", tier          # fallback 仍在
    err = capsys.readouterr().err
    assert "::warning::" in err and "宣告層失效" in err, err
