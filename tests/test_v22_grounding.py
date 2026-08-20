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
    # Pentagon 已進宣告的語意實體表(第三輪 P2-1)→ 依據升級為 alias
    assert ne.resolve_subject("Pentagon confirms new arms package",
                              ["Pentagon"], kn) == ("五角大廈", "alias")
    # 表外的名字仍走 literal(逐字出現才收)
    assert ne.resolve_subject("SpaceX wins new launch contract",
                              ["SpaceX"], kn) == ("SpaceX", "literal")
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


# ---------------------------- repo-wide 外審第三輪 P1-1:fatal 要真的 fatal


def test_invalid_primary_provider_never_dispatches_to_gemini(monkeypatch):
    """`LLM_PROVIDER=deepseke` 這一個 typo 先前會讓 Gemini 寫整份主分析
    —— 正好繞過「Gemini 只留抽取器備援」的政策。路由 fail-closed:
    未知 provider 拋 InvalidLLMConfig,任何一家的呼叫函式都不得被叫到。"""
    import morning_report as mr
    import pytest
    called = []
    monkeypatch.setattr(mr, "_PROVIDERS", {
        k: (lambda p, _k=k: called.append(_k) or "x", lambda _k=k: _k)
        for k in mr._PROVIDERS})
    for bad in ("deepseke", "", "  ", "gemini2"):
        monkeypatch.setattr(mr, "LLM_PROVIDER", bad)
        with pytest.raises(mr.InvalidLLMConfig):
            mr._call_llm_text("p")
    assert not called, f"設定壞掉仍呼叫了 {called}"


def test_invalid_provider_reaches_the_deterministic_fallback(monkeypatch):
    """端到端:設定壞掉 → 主分析走確定性緊急備援文字(晨報不可斷),
    而不是換一家模型代打。"""
    import morning_report as mr
    called = []
    monkeypatch.setattr(mr, "_call_gemini",
                        lambda *a, **k: called.append("gemini") or "x")
    monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseke")
    out = mr.call_llm_analysis({}, {}, {}, [])
    assert out, "應退回確定性備援文字而非空字串"
    assert "LLM 服務暫時不可用" in out or "備援" in out or out
    assert not called, "Gemini 被叫來代打主分析"


def test_fatal_config_issue_is_consumed_at_startup():
    """接線:`is_fatal` 在生產有真正的消費端(manifest config_invalid +
    降級標記)—— 先前 validate 標了 fatal,runtime 只印警告就繼續跑。"""
    import io as _io
    from pathlib import Path
    src = _io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                   encoding="utf-8").read()
    i = src.index("_fatal_cfg = [str(x) for x in _cfg if _lc.is_fatal(x)]")
    seg = src[i:i + 400]
    assert 'config_invalid' in seg and "llm:config_invalid" in seg


# ------------------- repo-wide 外審第三輪 P1-2/P2-1:legacy 信任 + 跨語言


def test_legacy_unverified_invented_label_is_purged():
    """生產反例逐字:`US-Iran War` + subject_basis=unverified 在帳本與
    時間軸都永久存活 —— 新 producer 拒收它,migration 卻把任何非空
    basis 都當可信。unverified 要用**新的**信任規則重驗,查證不出來就清。"""
    import state_migrations as sm
    kn = {"2330": ("台積電",)}
    rows = [{"key": "e:usiranwar|l:geopolitical|202608",
             "entity": "US-Iran War", "subject_basis": "unverified",
             "headline": "美伊戰爭/60天談判期限到 專家析局勢", "timeline": []}]
    keep, dropped = sm.purge_misattributed_stories(rows, kn)
    assert not keep and len(dropped) == 1, (keep, dropped)
    tl = {"geopolitical:US-Iran War:2026-08": {
        "entity": "US-Iran War", "subject_basis": "unverified",
        "latest_title": "美伊戰爭/60天談判期限到"}}
    k2, d2 = sm.purge_misattributed_timeline(tl, kn)
    assert d2 == ["geopolitical:US-Iran War:2026-08"], (k2, d2)


def test_legacy_unverified_cross_language_subject_is_upgraded():
    """Pentagon + 中文「五角大廈」是**合法**主體 —— 重驗要靠宣告過的
    跨語言別名升級,不得誤刪(鍵不動、依據升級)。"""
    import state_migrations as sm
    kn = {"2330": ("台積電",)}
    rows = [{"key": "e:pentagon|l:geopolitical|202608", "entity": "Pentagon",
             "subject_basis": "unverified",
             "headline": "伊朗戰爭衝擊五角大廈重新評估中東駐軍", "timeline": []},
            {"key": "e:russia|l:geopolitical|202608", "entity": "俄羅斯",
             "subject_basis": "unverified",
             "headline": "Russia says its economy is strong", "timeline": []}]
    keep, dropped = sm.purge_misattributed_stories(rows, kn)
    assert not dropped, dropped
    assert [r["subject_basis"] for r in keep] == ["alias", "alias"], keep


def test_cross_language_semantic_subject_resolves():
    """producer 側(P2-1):candidate 語言 ≠ 標題語言時,宣告過的語意實體
    仍要接得上;canonical 採**中文顯示名**(2026-08-20 P1-2:對齊
    event_actions 法域權威與既有 state —— 英文 canonical 會讓續報接不回
    `geopolitical:俄羅斯:…` 的舊鍵);模型自造的名字不因語意相似而接受。"""
    import news_events as ne
    kn = {"2330": ("台積電",)}
    assert ne.resolve_subject("伊朗戰爭衝擊五角大廈重新評估中東駐軍",
                              ["Pentagon"], kn) == ("五角大廈", "alias")
    assert ne.resolve_subject("Russia says its economy is strong",
                              ["俄羅斯"], kn) == ("俄羅斯", "alias")
    assert ne.resolve_subject("黃金與美元同步走強",
                              ["US Dollar"], kn) == ("美元", "alias")
    assert ne.resolve_subject("美伊戰爭情勢升溫",
                              ["US-Iran War"], kn) == ("", "")


# ----------------------- repo-wide 外審第三輪 P2-2/P2-3:v22 helper 正確性


_EMPTY_MACRO = {"us_rates_fx_vix": {"analysis": "", "evidence_ids": []},
                "fed_policy": {"analysis": "", "evidence_ids": []},
                "geopolitics": {"analysis": "", "evidence_ids": []}}


def test_empty_v22_macro_is_not_rendered_content():
    """v22 的合法空 macro 是三個空巢狀物件 —— 內層 dict truthy,淺判會把
    「完全空」當有內容 → 稀薄日 claim_audit 空被誤退(P2-2)。"""
    import copy

    import analysis_grounding as g
    assert not g.has_content(copy.deepcopy(_EMPTY_MACRO))
    assert g.has_content({"fed_policy": {"analysis": "有增量",
                                         "evidence_ids": []}})
    # 只有證據沒有文字也不算「有話要說」
    assert not g.has_content({"fed_policy": {"analysis": "",
                                             "evidence_ids": ["n1"]}})


def test_deepen_can_fill_previously_empty_macro_section():
    """加深的目的就是補不足 —— 空 → 補出內容不得被身分保全誤擋(P2-3);
    有 → 空 / 換證據仍要被看見(前輪的保護不變)。"""
    import copy
    blank = {"macro_environment": copy.deepcopy(_EMPTY_MACRO)}
    filled = {"macro_environment": dict(copy.deepcopy(_EMPTY_MACRO),
              fed_policy={"analysis": "Fed 今日增量", "evidence_ids": ["n7"]})}
    ib, ia = ad._identity(blank)["總經"], ad._identity(filled)["總經"]
    assert not (ib - ia), f"空→有被誤擋:{ib - ia}"
    assert ad._identity(filled)["總經"] - ad._identity(blank)["總經"], \
        "有→空沒被看見"


# ----------------------------------------- 同批外審 r4:三個 finding 的回歸


def test_company_alias_beats_semantic_entity():
    """「中國」是中國信託的前綴(r4 F3)—— 解法是**內嵌讓位**而不是
    重排候選(r5:順序是呼叫端的信任編碼):語意別名的命中內嵌在更長的
    公司別名裡、那個公司別名就在文字裡 → 這個候選讓位。"""
    import news_events as ne
    kn = {"2891": ("中國信託", "中信金"), "2330": ("台積電",)}
    assert ne.resolve_subject("中國信託獲利創高",
                              ["China", "2891"], kn) == ("2891", "alias")
    # 只有 China 候選時也不得誤認 —— 讓位後誠實回「沒有主體」
    assert ne.resolve_subject("中國信託獲利創高", ["China"], kn) == ("", "")
    # 真正的中國新聞不受影響
    assert ne.resolve_subject("中國出口管制升級",
                              ["China"], kn) == ("中國", "alias")
    # r6:**比出現位置不是存在性** —— 同一段文字同時有獨立的「中國」與
    # 「中國信託」時,合法的國家主體不得被公司別名的存在一併壓掉。
    assert ne.resolve_subject("中國宣布新政策,中國信託獲利創高",
                              ["China"], kn) == ("中國", "alias")
    # r5:**候選順序保留** —— 模型宣告的主體(Pentagon)在前就先贏,
    # 不因後面有公司候選而被整體重排。
    assert ne.resolve_subject("Pentagon 與台積電同日成為焦點",
                              ["Pentagon", "2330"], kn) == ("五角大廈", "alias")


def test_evidence_only_macro_section_is_not_protected_identity():
    """analysis 空、只有 evidence_ids 的切面 = 空(與 has_content 一致)
    —— 進身分的話,加深補文字換證據會被誤擋(r4 F1)。"""
    eo = {"macro_environment": {
        "us_rates_fx_vix": {"analysis": "", "evidence_ids": ["n1"]},
        "fed_policy": {"analysis": "", "evidence_ids": []},
        "geopolitics": {"analysis": "", "evidence_ids": []}}}
    filled = {"macro_environment": {
        "us_rates_fx_vix": {"analysis": "10Y 增量", "evidence_ids": ["n2"]},
        "fed_policy": {"analysis": "", "evidence_ids": []},
        "geopolitics": {"analysis": "", "evidence_ids": []}}}
    assert ad._identity(eo)["總經"] == set()
    assert not (ad._identity(eo)["總經"] - ad._identity(filled)["總經"])


def test_canary_mirrors_provider_specific_model_keys():
    """CLAUDE_MODEL 沒有 LLM_/DEEPSEEK_ 前綴 —— 前綴過濾會靜默漏掉它,
    生產切 anthropic 那天 canary 跑的是 Python 預設模型(r4 F2)。"""
    import yaml
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    ci = yaml.safe_load((root / "ci.yml").read_text(encoding="utf-8"))
    for step in ci["jobs"]["dry-run-preview"]["steps"]:
        env = step.get("env") or {}
        if "DEEPSEEK_MODEL" in env:
            assert "CLAUDE_MODEL" in env, "canary 少了 CLAUDE_MODEL"
            break
    else:
        raise AssertionError("找不到 dry-run 步驟")
