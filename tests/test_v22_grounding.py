# -*- coding: utf-8 -*-
"""repo-wide 外審 2026-08-19 P1-B:v22 —— 敘事變化與總經接上證據契約。

裸字串時代:「昨日判斷 Fed 已準備大幅降息 → 強化」「美伊已達成永久和平
協議」可以整條虛構、一路進信;schema/validator/grounding 全擋不住,
而測試 fixture 還把這個漏洞釘成合法形狀。
"""
import analysis_depth as ad
import analysis_schema as sch

import fixtures_analysis as fx

_PK = {"news": [{"source_item_id": "n1", "title": "油價大漲", "summary": "",
                 "importance": "high"}],
       "market": {"ANALYSIS_RECAP": {"items": [
           {"id": "pv1", "statement": "美伊戰局逼近十字路口"}]}}}


def _obj(**delta):
    o = fx.valid_analysis()
    o["narrative_delta"] = [dict({"prior_view_id": "pv1",
                                  "prior_view": "美伊戰局逼近十字路口",
                                  "change": "升溫",
                                  "evidence_today": "油價單日 +3.79%",
                                  "evidence_ids": ["n1"]}, **delta)]
    return o


def test_narrative_delta_cannot_invent_prior_view_without_recap_reference():
    """prior_view_id 必須是 packet 裡 ANALYSIS_RECAP 真的有的 id ——
    昨日觀點不可虛構(欄位名稱讓讀者以為那是系統記得的觀點)。"""
    ok = [p for p in sch.validate(_obj(), _PK) if "narrative_delta" in p]
    assert not ok, ok
    bad = [p for p in sch.validate(_obj(prior_view_id="pv9"), _PK)
           if "narrative_delta" in p]
    assert bad, "虛構的 prior_view_id 通過了驗證"
    missing = [p for p in sch.validate(_obj(prior_view_id=""), _PK)
               if "narrative_delta" in p]
    assert missing, "沒帶 prior_view_id 也通過了"


def test_narrative_delta_requires_current_evidence():
    """沒有今天的證據就談不上強化或反轉;捏造的 ID 一樣擋。"""
    assert [p for p in sch.validate(_obj(evidence_ids=[]), _PK)
            if "narrative_delta" in p]
    assert [p for p in sch.validate(_obj(evidence_ids=["捏造"]), _PK)
            if "narrative_delta" in p]


def _macro_obj(sec):
    o = fx.valid_analysis()
    o["macro_environment"] = {
        "us_rates_fx_vix": {"analysis": "", "evidence_ids": []},
        "fed_policy": {"analysis": "", "evidence_ids": []},
        "geopolitics": {"analysis": "", "evidence_ids": []}}
    o["macro_environment"].update(sec)
    return o


def test_macro_environment_with_content_requires_evidence():
    o = _macro_obj({"fed_policy": {"analysis": "Fed 今日明確轉向寬鬆",
                                   "evidence_ids": []}})
    assert [p for p in sch.validate(o, _PK) if "macro_environment" in p]
    ok = _macro_obj({"fed_policy": {"analysis": "Warsh 鷹派發酵",
                                    "evidence_ids": ["n1"]}})
    assert not [p for p in sch.validate(ok, _PK) if "macro_environment" in p]


def test_fabricated_macro_free_text_does_not_pass_grounding():
    """外審的反例逐字:三格全虛構、不帶數字 —— 必須被驗證擋下。"""
    o = _macro_obj({
        "us_rates_fx_vix": {"analysis": "美債壓力已全面消退,成長股估值壓力解除",
                            "evidence_ids": []},
        "fed_policy": {"analysis": "Fed 今日明確轉向寬鬆", "evidence_ids": []},
        "geopolitics": {"analysis": "美伊已達成永久和平協議",
                        "evidence_ids": []}})
    probs = [p for p in sch.validate(o, _PK) if "macro_environment" in p]
    assert len(probs) >= 3, probs


def _full_macro():
    o = fx.valid_analysis()
    o["macro_environment"] = {
        "us_rates_fx_vix": {"analysis": "10Y 4.657% 高檔", "evidence_ids": ["n1"]},
        "fed_policy": {"analysis": "Warsh 鷹派發酵", "evidence_ids": ["n1"]},
        "geopolitics": {"analysis": "三線地緣升溫", "evidence_ids": ["n1"]}}
    return o


def test_deepened_response_cannot_drop_macro_environment():
    """加深不得清空總經三格 —— identity 沒有它時,第二版清空照樣勝出,
    而 renderer 用的就是第二版(外審 P2)。"""
    full = _full_macro()
    blank = _full_macro()
    blank["macro_environment"]["fed_policy"] = {"analysis": "",
                                                "evidence_ids": []}
    assert ad._identity(full)["總經"] - ad._identity(blank)["總經"], \
        "清空 (B) 沒被身分看見"


def test_deepened_response_cannot_replace_macro_evidence():
    """換證據=換判斷的根據,也要被看見。"""
    full = _full_macro()
    swapped = _full_macro()
    swapped["macro_environment"]["geopolitics"]["evidence_ids"] = ["n2"]
    assert ad._identity(full)["總經"] - ad._identity(swapped)["總經"], \
        "換掉 (C) 的證據沒被身分看見"


def test_recap_items_get_python_assigned_ids():
    """接線:昨日觀點在進 packet 時由 Python 蓋上 pv id ——
    prior_view_id 才有可驗的對象(state 檔本身不動)。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                  encoding="utf-8").read()
    i = src.index('quotes["ANALYSIS_RECAP"] = dict(')
    seg = src[i:i + 300]
    assert 'id=f"pv{_i + 1}"' in seg, seg


# ------------------------------------- 外審 P2:主體信任層級(Commit C)


def test_invented_llm_labels_never_become_entity_keys():
    """「US-Iran War」是模型自己取的名字 —— 沒有逐字出現在來源文字裡,
    不得成為持久化的 entity key;Pentagon 逐字在標題裡則可以。"""
    import news_events as ne
    kn = {"2330": ("台積電",)}
    assert ne.resolve_subject("黃金價格創新高,避險資產走強",
                              ["US-Iran War"], kn) == ("", "")
    assert ne.resolve_subject("Pentagon confirms new arms package",
                              ["Pentagon"], kn) == ("Pentagon", "literal")
    # 逐字比對沿用別名規則:拉丁詞要詞邊界 —— "Arm" 不得命中 "pharmaceutical"
    assert ne.resolve_subject("pharmaceutical stocks rally",
                              ["Arm"], kn) == ("", "")


# --------------------------------------- 同批外審 r2:三個 finding 的回歸


def test_renderer_uses_the_python_statement_not_the_model_copy():
    """合法 pv1 + 改寫過的「昨日觀點」:validator 只驗 ID 存在,信裡不得
    出現偽造的昨日 —— 渲染依 ID 取回 Python 保存的 statement。"""
    import analysis_render as ar
    o = fx.valid_analysis()
    o["narrative_delta"] = [{"prior_view_id": "pv1",
                             "prior_view": "Fed 已承諾大幅降息(偽造)",
                             "change": "強化", "evidence_today": "今日紀要",
                             "evidence_ids": ["n1"]}]
    pk = {"market": {"ANALYSIS_RECAP": {"items": [
        {"id": "pv1", "statement": "美伊戰局逼近十字路口"}]}}, "news": []}
    out = ar.render(o, pk)
    assert "美伊戰局逼近十字路口" in out
    assert "偽造" not in out, "模型抄本蓋過了 Python 原文"


def test_only_usable_recap_items_get_pv_ids():
    """接線:pv id 只派給 `usable()` 過濾後的觀點 —— 同日重跑時 state 是
    今天剛存的,無條件派 ID 等於繞過同日防線(拿今天比今天)。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                  encoding="utf-8").read()
    i = src.index('quotes["ANALYSIS_RECAP"] = dict(')
    seg = src[i:i + 400]
    assert "_arc.usable(_recap_state, target_session_date)" in seg, seg


def test_numeric_and_period_tokens_are_not_literal_subjects():
    """「成交量 3231 張」的 3231 是張數不是矽創;Q2/FY25 是期間詞 ——
    逐字出現也不得成為持久化 entity key(嚴格路徑本來就拒裸數字,
    literal fallback 不得重開那條路)。"""
    import news_events as ne
    kn = {"2330": ("台積電",)}
    assert ne.resolve_subject("成交量 3231 張創天量", ["3231"], kn) == ("", "")
    # r3:期間詞判準與 analysis_validate 共用單一份(news_rules.PERIOD_TOKEN)
    # —— 兩份各養會漂移(1Q/1H/CY25/2Q26 曾一邊擋、一邊放)。
    for cand, text in (("Q2", "Q2 earnings beat"), ("1Q", "1Q beat"),
                       ("H1", "H1 results"), ("1H", "1H earnings beat"),
                       ("FY25", "FY25 guidance raised"),
                       ("CY25", "CY25 outlook"), ("2Q26", "2Q26 guide"),
                       ("1H26", "1H26 results"), ("2026Q3", "2026Q3 outlook"),
                       ("2026H1", "2026H1 recap")):
        assert ne.resolve_subject(text, [cand], kn) == ("", ""), cand
    # 單一判準是接線性質:兩個消費端要用同一個編譯物件
    import analysis_validate as av
    import news_rules as nr
    assert av._PERIOD_TOKEN is nr.PERIOD_TOKEN, "期間詞判準又分裂成兩份"
