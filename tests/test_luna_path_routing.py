# -*- coding: utf-8 -*-
"""**Luna 路徑真的會被走到嗎**(執行期接線)。

這個 repo 有過「測試全綠、外審通過、生產零產出」的紀錄(LLM 抽取器連續兩班
0 事件、籌碼與敘事同型)。所以接線完成之後,最重要的不是「模組各自對不對」,
而是:

    設了變數之後,主分析**真的**走 Responses + Luna profile 了嗎?
    任一環節壞掉時,**真的**落回既有路徑而不是把信弄丟嗎?

這裡把 HTTP 那一層樁掉,其餘全部走真實程式碼 —— 樁在最外面一層,
是為了讓「路由決策、驗證、修補、渲染、降級」這五段都是真的在跑。
"""
import json

import pytest

import morning_report as mr


def _response(obj, *, effort="xhigh", usage=None):
    """一個形狀正確的 Responses 回應。"""
    return {
        "status": "completed",
        "reasoning": {"effort": effort},
        "output": [{"type": "message", "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text",
                                 "text": json.dumps(obj, ensure_ascii=False)}]}],
        "usage": usage or {"input_tokens": 90_000, "output_tokens": 20_000,
                           "input_tokens_details": {"cached_tokens": 80_000},
                           "output_tokens_details": {"reasoning_tokens": 15_000}},
    }


_GOOD = {
    "executive_summary": "今日偏多,留意台積電法說。",
    "stance": {"label": "偏多", "score": 6, "confidence": 0.7,
               "time_horizon": "1-5d", "rationale": "多數訊號同向。"},
    "key_drivers": [{"statement": "費半走強", "claim_type": "fact",
                     "direction": "bullish", "materiality": "high",
                     "confidence": 0.8, "horizon": "intraday",
                     "evidence_ids": ["n1"], "counterevidence_ids": [],
                     "falsification_trigger": "夜盤翻黑"}],
    "scenario_tree": {"base": {"narrative": "震盪走高", "probability": 0.6,
                               "triggers": []},
                      "bull": {"narrative": "突破", "probability": 0.2,
                               "triggers": []},
                      "bear": {"narrative": "回測", "probability": 0.2,
                               "triggers": []},
                      "invalidation_triggers": []},
    "taiwan_market": {"summary": "量能回升。", "taiex_view": "偏多",
                      "tsmc_view": "守月線", "evidence_ids": ["n2"]},
    "global_market": {"summary": "美股收紅。", "us_to_tw_linkage": "費半傳導",
                      "evidence_ids": ["n1"]},
    "portfolio_implications": {"summary": "維持核心。",
                               "actions_to_consider": [], "risks": []},
    "top_news_analysis": [{"source_item_id": "n1", "why_it_matters": "傳導台股",
                           "affected": ["台積電"]}],
    "contradictions": [], "data_gaps": [], "watch_triggers": [],
    "claim_audit": [{"claim_id": "c1", "statement": "費半走強",
                     "claim_type": "fact", "materiality": "high",
                     "evidence_ids": ["n1"], "counterevidence_ids": [],
                     "falsification_trigger": "夜盤翻黑"}],
    "market_regime": {"label": "偏多", "evidence_ids": ["n1"]},
    "priced_in": {"already_reflected": [], "not_yet_reflected": []},
}

#: 第十二輪 P1-3:**測試資料要有證據可引。**
#: 原本 news 是空清單,於是 packet 裡一個 evidence ID 都沒有 ——
#: 「合格輸出」因此只能長成「什麼都不引用」的樣子,而那正是缺陷本身。
_NEWS = [{"source_item_id": "n1", "title": "費城半導體指數收漲 2.1%",
          "summary": "SOX 收漲。", "source": "Reuters",
          "entities": ["費半"], "published_at": "2026-08-02T20:00:00+08:00"},
         {"source_item_id": "n2", "title": "台積電法說會下週登場",
          "summary": "市場關注資本支出。", "source": "經濟日報",
          "entities": ["台積電"], "published_at": "2026-08-02T18:00:00+08:00"}]

_ARGS = ({"QQQ": {"close": 500.0}}, {"fair_value": 100.0},
         {"model1": 1000.0}, _NEWS, [], "")


@pytest.fixture
def luna_on(monkeypatch):
    """把實驗設定打開(其餘保持真實程式碼)。"""
    monkeypatch.setattr(mr, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(mr, "OPENAI_API_MODE", "responses")
    monkeypatch.setattr(mr, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(mr, "OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(mr, "LLM_PRIMARY_PROMPT_PROFILE", "")
    monkeypatch.setattr(mr, "_PRIMARY_EFFORT", "xhigh")
    monkeypatch.setattr(mr, "LLM_SHADOW_PROVIDER", "")     # 影子分開測
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")


def test_the_luna_path_is_actually_taken_when_configured(luna_on, monkeypatch):
    """**本檔最重要的一條。** 設了變數就真的要走 Responses。

    走不到的症狀是「一切照舊」—— 信照樣寄出、內容由 DeepSeek 寫,
    而 manifest 顯示我們設了 Luna。這個 repo 已經有過這種紀錄。
    """
    sent = []

    def _fake(payload):
        sent.append(payload)
        return _response(_GOOD)

    monkeypatch.setattr(mr, "_call_openai_responses", _fake)
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: pytest.fail("走到了既有路徑,Luna 分支沒生效"))

    text = mr._call_llm_analysis_impl(*_ARGS)
    assert sent, "完全沒有送出 Responses 請求"
    assert sent[0]["reasoning"]["effort"] == "xhigh"
    assert sent[0]["text"]["format"]["strict"] is True
    assert sent[0]["instructions"].startswith("你是一位台股與美股的晨報分析師")
    assert "我的明確立場" in text and "一句話總結" in text
    assert mr._analysis_complete_enough(text), "產出過不了既有的截斷偵測器"


def test_the_default_configuration_does_not_take_the_luna_path(monkeypatch):
    """預設(chat_completions)不得走新路徑 —— 它尚未經過生產。"""
    monkeypatch.setattr(mr, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(mr, "OPENAI_API_MODE", "chat_completions")
    monkeypatch.setattr(mr, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: pytest.fail("預設設定竟然走了 Responses"))
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：中性\n\n## 一句話總結\n照舊。")
    monkeypatch.setattr(mr, "_run_llm_shadow", lambda *a, **k: None)
    assert "照舊" in mr._call_llm_analysis_impl(*_ARGS)


def test_deepseek_never_takes_the_luna_path(monkeypatch):
    """回切之後不得殘留 —— 那是使用者要求「保留 DeepSeek 設計」的實質內容。"""
    monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "OPENAI_API_MODE", "responses")   # 就算模式開著
    monkeypatch.setattr(mr, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: pytest.fail("DeepSeek 竟然走了 Luna 路徑"))
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：偏空\n\n## 一句話總結\nDS。")
    monkeypatch.setattr(mr, "_run_llm_shadow", lambda *a, **k: None)
    assert "DS。" in mr._call_llm_analysis_impl(*_ARGS)


def test_a_broken_luna_response_falls_back_instead_of_losing_the_email(
        luna_on, monkeypatch):
    """**晨報不可斷。** Luna 壞掉時要落回既有路徑,不是回半份、也不是不寄。"""
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: _response({"完全": "不合 schema"}))
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：中性\n\n## 一句話總結\n備援。")
    monkeypatch.setattr(mr, "_run_llm_shadow", lambda *a, **k: None)
    text = mr._call_llm_analysis_impl(*_ARGS)
    assert "備援。" in text, "Luna 失敗後沒有落回既有路徑"


def test_a_network_failure_falls_back_and_is_recorded(luna_on, monkeypatch):
    """例外也要落回,而且要在降級清單留痕 —— 靜默降級等於沒有降級。"""

    def _boom(payload):
        raise RuntimeError("ReadTimeout")

    monkeypatch.setattr(mr, "_call_openai_responses", _boom)
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：中性\n\n## 一句話總結\n備援。")
    monkeypatch.setattr(mr, "_run_llm_shadow", lambda *a, **k: None)
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._DEGRADED_STEPS.clear()
        assert "備援。" in mr._call_llm_analysis_impl(*_ARGS)
        assert "llm:luna_path_failed" in mr._DEGRADED_STEPS
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_repair_happens_at_most_once_and_both_attempts_are_billed(
        luna_on, monkeypatch):
    """修補**最多一次**,而且那一次同樣計費、同樣進 attempts。

    「成本上限 +1」如果不把修補算進去,那個宣稱就是假的 —— 而十天實驗的
    成本結論正是建立在它上面。
    """
    calls = []

    def _fake(payload):
        calls.append(payload)
        return _response({"壞": "的"} if len(calls) == 1 else _GOOD)

    monkeypatch.setattr(mr, "_call_openai_responses", _fake)
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: pytest.fail("不該落回 —— 修補成功了"))
    mr._RUN_MANIFEST.pop("llm", None)
    text = mr._call_llm_analysis_impl(*_ARGS)
    assert len(calls) == 2, f"修補次數不是一次:{len(calls)}"
    assert "REPAIR" in calls[1]["input"], "修補請求沒有帶上問題清單"
    assert mr._analysis_complete_enough(text)
    # **兩次都要計費入帳,但語意不同**:被採用的那次進 `llm.primary`
    # (成本彙總看那裡),不合格的那次進 `attempts`。
    # 修補失敗的呼叫一樣要付錢 —— 不記等於低估成本。
    slot = mr._RUN_MANIFEST.get("llm") or {}
    primary = slot.get("primary") or {}
    attempts = [a for a in (slot.get("attempts") or []) if a.get("role") == "primary"]
    assert primary.get("calls") == 1, f"被採用的那次沒進 llm.primary:{primary}"
    assert primary.get("prompt_tokens") == 90_000, "採用的那次沒有帶 usage"
    assert primary.get("estimated_cost_usd"), "採用的那次沒有算成本"
    assert len(attempts) == 1, f"不合格的那次沒有入帳:{attempts}"
    assert attempts[0].get("estimated_cost_usd"), "不合格的那次沒有計費"
    assert attempts[0].get("reject_reason"), "沒有記下為什麼不合格"

    # 第二次也壞 → 不再修補,落回既有路徑
    calls.clear()
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: (calls.append(p), _response({"壞": "的"}))[1])
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：中性\n\n## 一句話總結\n備援。")
    monkeypatch.setattr(mr, "_run_llm_shadow", lambda *a, **k: None)
    assert "備援。" in mr._call_llm_analysis_impl(*_ARGS)
    assert len(calls) == 2, "修補失敗後仍在重試"


def test_the_manifest_records_which_profile_and_evidence_were_used(
        luna_on, monkeypatch):
    """沒有記下來,事後補不回來 —— 而配對語意全靠這幾個欄位。"""
    monkeypatch.setattr(mr, "_call_openai_responses", lambda p: _response(_GOOD))
    mr._RUN_MANIFEST.pop("llm", None)
    mr._call_llm_analysis_impl(*_ARGS)
    bundle = (mr._RUN_MANIFEST.get("llm") or {}).get("primary_bundle") or {}
    assert bundle.get("profile_id") == "luna56_xhigh_v1"
    assert bundle.get("evidence_sha") and bundle.get("prompt_sha")
    assert "developer_instructions" not in bundle, "prompt 本體進了 manifest"
    metrics = (mr._RUN_MANIFEST.get("llm") or {}).get("primary_metrics") or {}
    assert metrics.get("parsed") is True


def test_the_shadow_gets_the_legacy_prompt_not_the_luna_one(luna_on, monkeypatch):
    """影子送的必須是 **DeepSeek 的既有問法** —— 那正是「各自最佳化」。

    送 Luna 的 prompt 給 DeepSeek,比的就變成「DeepSeek 用別人的 prompt」,
    而那不是任何人想知道的事。
    """
    seen = {}

    def _shadow(prompt, primary_text, now, **kw):
        seen["prompt"] = prompt
        seen.update(kw)

    monkeypatch.setattr(mr, "_call_openai_responses", lambda p: _response(_GOOD))
    monkeypatch.setattr(mr, "_run_llm_shadow", _shadow)
    mr._call_llm_analysis_impl(*_ARGS)
    assert seen, "主分析成功卻沒有觸發影子"
    assert "你是嚴謹但敢於下判斷的科技股財經分析師" in seen["prompt"], \
        "影子拿到的不是 legacy prompt"
    assert "EVIDENCE" not in seen["prompt"], "影子拿到了 Luna 的 payload"
    assert seen.get("primary_profile") == "luna56_xhigh_v1"
    assert seen.get("shadow_profile") == "deepseek_legacy_v1"
    assert seen.get("packet") is not None, "影子沒有拿到 packet,證據指紋記不了"


# ---------------------------------------------------------------- r1 外審修正

def test_a_billable_timeout_is_recorded_even_though_usage_is_unknown(
        luna_on, monkeypatch):
    """r1(Codex,#6):**送出去了就可能被計費。**

    ReadTimeout / 連線中斷 / 回應不是 JSON,都發生在 server 已經收下請求
    之後。不入帳的話總成本與呼叫數會低估,而十天實驗的結論建立在成本上。
    """
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: (_ for _ in ()).throw(RuntimeError("ReadTimeout")))
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：中性\n\n## 一句話總結\n備援。")
    monkeypatch.setattr(mr, "_run_llm_shadow", lambda *a, **k: None)
    mr._RUN_MANIFEST.pop("llm", None)
    assert "備援。" in mr._call_llm_analysis_impl(*_ARGS)

    attempts = [a for a in ((mr._RUN_MANIFEST.get("llm") or {}).get("attempts") or [])
                if a.get("role") == "primary"]
    assert attempts, "逾時的那次請求完全沒有入帳"
    assert attempts[-1].get("billable_unmeasured") is True, attempts[-1]
    assert attempts[-1].get("elapsed_seconds") is not None, "沒有記耗時"


def test_a_failed_luna_day_still_produces_an_experiment_record(
        luna_on, monkeypatch):
    """r1(Codex,#4):**失敗的那天也要有一列紀錄。**

    只記成功的那幾天,「誰比較常失敗」這個問題的答案永遠是 100% ——
    而那正是十天實驗要回答的問題之一。
    """
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "luna56-xhigh-vs-dsv4pro-v1")
    monkeypatch.setattr(mr, "LLM_SHADOW_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(mr, "LLM_SHADOW_REASONING_EFFORT", "max")
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: (_ for _ in ()).throw(RuntimeError("ReadTimeout")))
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：中性\n\n## 一句話總結\n備援。")
    monkeypatch.setattr(mr, "_run_llm_shadow", lambda *a, **k: None)
    mr._RUN_MANIFEST.pop("llm_experiment", None)
    mr._call_llm_analysis_impl(*_ARGS)

    # r2(Codex,#4):**紀錄放在自己的鍵下。** 寫進 `llm_shadow` 會被既有路徑
    # 結尾的整包指派蓋掉 —— 可靠度又回到只量成功的那些天。
    rec = mr._RUN_MANIFEST.get("llm_experiment") or {}
    assert rec, "Luna 失敗的那天沒有留下實驗紀錄"
    assert rec["primary_ok"] is False and rec["shadow_ok"] is False
    assert rec["experiment_id"] == "luna56-xhigh-vs-dsv4pro-v1"
    assert rec.get("failure_reason"), "沒有記下失敗原因"

    import llm_experiment as lx
    cohort = lx.cohort_key(rec)
    # 這一天不進有效分母,但**要進可靠度的分母** —— 那是它的價值所在。
    assert not lx.is_comparable(rec, cohort)
    assert lx.reliability([rec], cohort)["primary_ok_rate"] == 0.0


def test_recording_the_failure_never_breaks_the_email(luna_on, monkeypatch):
    """紀錄不得反過來弄壞晨報 —— 它是觀測,不是功能。"""
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "e1")
    monkeypatch.setattr(mr, "_lx", None)        # 讓記錄那段必然拋例外
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：中性\n\n## 一句話總結\n備援。")
    monkeypatch.setattr(mr, "_run_llm_shadow", lambda *a, **k: None)
    assert "備援。" in mr._call_llm_analysis_impl(*_ARGS)


def test_the_failure_record_survives_the_legacy_shadow_run(luna_on, monkeypatch):
    """r2(Codex,#4):失敗紀錄**不得被既有路徑的影子結果蓋掉**。

    `_run_llm_shadow` 結尾是 `_RUN_MANIFEST["llm_shadow"] = stat` —— 整包指派。
    紀錄若寫在那個鍵底下,主分析失敗那天的證據會在幾行之後消失,
    而可靠度指標又回到「只量 Luna 成功的那些天」。
    """
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "e1")
    monkeypatch.setattr(mr, "LLM_SHADOW_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場：中性\n\n## 一句話總結\n備援。")

    def _shadow_that_overwrites(prompt, primary_text, now, **kw):
        # 真實 `_run_llm_shadow` 的最後一行就是這個形狀
        mr._RUN_MANIFEST["llm_shadow"] = {"skipped": "disabled"}

    monkeypatch.setattr(mr, "_run_llm_shadow", _shadow_that_overwrites)
    mr._RUN_MANIFEST.pop("llm_experiment", None)
    mr._call_llm_analysis_impl(*_ARGS)
    assert (mr._RUN_MANIFEST.get("llm_experiment") or {}).get("primary_ok") is False, \
        "失敗紀錄被影子的整包指派蓋掉了"


def test_a_shadow_skipped_before_the_call_still_leaves_a_row(
        luna_on, monkeypatch):
    """r3(Codex,#4):**跳過也要留一列。**

    執行預算不足或 provider 不合法時,shadow 在**呼叫之前**就被跳過。
    不留紀錄的話,可靠度只量得到「跑得完的那些天」—— 而最慢、最容易被跳過
    的日子正是應該被算進去的,`shadow_ok_rate` 因此偏高。
    """
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "e1")
    monkeypatch.setattr(mr, "LLM_SHADOW_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "LLM_SHADOW_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(mr, "_run_budget_ok", lambda *a, **k: False)
    rows = []
    monkeypatch.setattr(mr, "_persist_experiment_record",
                        lambda rec, today: rows.append(rec))
    packet = mr._ep.build({}, {}, {}, [], [], {}, sanitize=str)
    mr._run_llm_shadow("prompt", "正式輸出", mr.dt.datetime.now(mr.TPE),
                       packet=packet, primary_profile="luna56_xhigh_v1",
                       shadow_profile="deepseek_legacy_v1")
    assert rows, "預算不足而跳過的那天完全沒有紀錄"
    assert rows[0]["shadow_ok"] is False
    assert "run_budget" in rows[0]["failure_reason"], rows[0]


def test_the_comparable_metrics_are_computed_for_both_sides(
        luna_on, monkeypatch):
    """r3(Codex,#2):十配對達標時要有東西可以判讀。

    `analysis_metrics` 的函式先前**在生產完全沒有呼叫端** —— 帳本會宣告
    「可以做判讀」,而實際上只有立場、字數與 body overlap。
    """
    packet = mr._ep.build({}, {}, {}, [{"title": "央行維持利率", "source": "CBC",
                                        "published": "p", "official": True}],
                          [], {}, sanitize=str)
    m = mr._comparable_metrics(packet, "央行維持利率,偏多。", "今日中性。")
    assert set(m) == {"primary", "shadow"}
    for side in ("primary", "shadow"):
        assert "numeric_consistency" in m[side]
        assert "evidence_coverage" in m[side]
        assert "source_diversity" in m[side]
        assert "cost" in m[side]
    # 兩側**用同一組指標**才叫可比
    assert set(m["primary"]) == set(m["shadow"])
    # 結構化指標只有 Luna 有,刻意不混進來
    assert "completeness_rate" not in m["primary"]


def test_metric_failure_never_breaks_the_email(monkeypatch):
    """量測是觀測,不是功能 —— 它壞掉不得讓晨報中斷。"""
    monkeypatch.setattr(mr._am, "text_metrics",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    packet = mr._ep.build({}, {}, {}, [], [], {}, sanitize=str)
    out = mr._comparable_metrics(packet, "a", "b")
    assert "error" in out


def test_the_production_path_actually_attaches_the_metrics(luna_on, monkeypatch):
    """r3(Codex,#2):**指標要真的被生產路徑帶進帳本。**

    只驗 `_comparable_metrics` 本身不夠 —— 它算得再對,呼叫端沒把結果傳下去
    就等於沒有。這正是「機制存在但沒有呼叫端」的形狀,而那條 finding
    說的就是 `analysis_metrics` 三個函式在生產完全沒有被呼叫。
    """
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "e1")
    monkeypatch.setattr(mr, "LLM_SHADOW_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "LLM_SHADOW_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(mr, "_run_budget_ok", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_call_deepseek", lambda p, role="": "影子的分析輸出")
    # `llm_shadow` 是在 `_run_llm_shadow` 裡才 import 的(函式內 local import),
    # 所以要 patch 模組本身,不是 `mr._ls`。
    import llm_shadow as _ls
    monkeypatch.setattr(_ls, "run_comparison",
                        lambda **kw: {"today": {"shadow_ok": True,
                                                "prompt_sha": "s1"},
                                      "cumulative": {}})
    rows = []
    monkeypatch.setattr(mr, "_persist_experiment_record",
                        lambda rec, today: rows.append(rec))

    packet = mr._ep.build({}, {}, {}, [{"title": "央行維持利率", "source": "CBC",
                                        "published": "p", "official": True}],
                          [], {}, sanitize=str)
    mr._run_llm_shadow("legacy prompt", "正式輸出:央行維持利率",
                       mr.dt.datetime.now(mr.TPE), packet=packet,
                       primary_profile="luna56_xhigh_v1",
                       shadow_profile="deepseek_legacy_v1")
    assert rows, "成功的那天沒有留下實驗紀錄"
    m = rows[0].get("metrics") or {}
    assert set(m) >= {"primary", "shadow"}, f"帳本沒有帶兩側指標:{m}"
    assert "evidence_coverage" in m["primary"], m["primary"]
    # 深度揭露也要在
    assert rows[0]["primary_coverage"].get("available") is not None


# ------------------------------------------------------- 第十二輪 P1-3

#: **這份輸出以前叫 `_GOOD`。** 每個 evidence_ids 都是空的、claim_audit 也空,
#: 而它被當成「生產形狀的合格輸出」用來驗整條路徑 —— 也就是說
#: 「重大主張不必有根據」被測試釘成了通過條件。
_UNSUPPORTED = json.loads(json.dumps(_GOOD))
_UNSUPPORTED["key_drivers"][0]["evidence_ids"] = []
_UNSUPPORTED["taiwan_market"]["evidence_ids"] = []
_UNSUPPORTED["global_market"]["evidence_ids"] = []
_UNSUPPORTED["market_regime"]["evidence_ids"] = []
_UNSUPPORTED["top_news_analysis"] = []
_UNSUPPORTED["claim_audit"] = []


def test_an_ungrounded_report_is_rejected_and_falls_back(luna_on, monkeypatch):
    """**沒有根據的重大主張不得被寄出**(第十二輪 P1-3)。

    實測過的反例:`materiality=high` 的 `fact`、`evidence_ids=[]`、
    `claim_audit=[]` —— 這份輸出原本零問題通過驗證,而 renderer 會把它
    排進「昨夜三大重點」與「我的明確立場」寄出去。

    缺陷的形狀是**空集合讓迴圈沒跑**:高重要性檢查寫在
    `for c in claim_audit` 裡,claim_audit 空的時候整段直接跳過。

    strict schema 保證的是形狀,不是根據。
    """
    calls = []
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: (calls.append(p), _response(_UNSUPPORTED))[1])
    # 這段要**過得了完整性檢查** —— 太短會落到備援文字,
    # 那時測到的就不是「有沒有落回 legacy」而是「備援有沒有作用」。
    legacy = ("## 我的明確立場\n立場:偏多\n既有路徑寫的分析。\n"
              "## 一句話總結\n維持核心部位。")
    monkeypatch.setattr(mr, "_call_llm_text", lambda p: legacy)

    text = mr._call_llm_analysis_impl(*_ARGS)
    assert text == legacy, (
        "沒有根據的報告被採用了 —— 它會被原樣寄出,而且看起來很有把握")
    assert len(calls) == 2, f"應該修補一次再放棄,實際送了 {len(calls)} 次"
    problems = (mr._RUN_MANIFEST.get("llm") or {}).get("luna_problems") or []
    assert any("證據" in p for p in problems), f"拒收原因沒有說清楚:{problems}"


def test_the_rejected_report_never_reaches_the_renderer():
    """更前面一步:那份輸出**原本渲染得出完整段落** —— 所以擋要擋在驗證。"""
    import analysis_render as ar
    rendered = ar.render(_UNSUPPORTED)
    assert "費半走強" in rendered, (
        "反例改壞了 —— 它必須是「渲染得出來」的那種,"
        "否則這條測的就不是「驗證有沒有擋住」")


def test_the_good_fixture_actually_cites_evidence():
    """反向:`_GOOD` 不得再退化成「什麼都不引用」。

    它是整個檔案的基準;基準鬆掉,上面每一條路徑測試都會跟著失去意義。
    """
    ids = {i for d in _GOOD["key_drivers"] for i in d["evidence_ids"]}
    assert ids, "_GOOD 的 key_drivers 沒有引用任何證據"
    assert _GOOD["claim_audit"], "_GOOD 沒有稽核軌跡"
    known = {n["source_item_id"] for n in _NEWS}
    assert ids <= known, f"_GOOD 引用了測試資料裡不存在的證據:{ids - known}"


# ------------------------------------------ 第十三輪 r1:失敗日的呼叫數

def test_a_failed_day_counts_the_fallback_calls_too(luna_on, monkeypatch):
    """**失敗那列要等落回路徑跑完才定案**(第十三輪 r1,#1)。

    原本在落回**之前**就寫帳:legacy 呼叫與短版重試的計費全部不算,
    而失敗日正是花最多次呼叫的那些天 —— 低估的方向又偏向「很便宜」。
    """
    rows = []
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "e")
    monkeypatch.setattr(mr, "_persist_experiment_record",
                        lambda rec, today: rows.append(rec))
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: (_ for _ in ()).throw(RuntimeError("逾時")))

    def _legacy(prompt):
        # legacy 這一次呼叫要出現在失敗那列的計費裡
        mr._record_llm_call("primary", "deepseek", "deepseek-v4-pro",
                            accepted=True,
                            usage={"prompt_tokens": 100, "completion_tokens": 10})
        return ("## 我的明確立場\n立場:偏多\n既有路徑寫的分析。\n"
                "## 一句話總結\n維持核心部位。")

    monkeypatch.setattr(mr, "_call_llm_text", _legacy)
    mr._RUN_MANIFEST.pop("llm", None)
    mr._call_llm_analysis_impl(*_ARGS)

    assert len(rows) == 1, f"失敗日沒有留下紀錄:{rows}"
    assert rows[0]["primary_ok"] is False
    # 例外那條 Luna 只送出 1 次(第一次就拋),所以 legacy 那次讓它變 2。
    assert rows[0]["provider_calls"] == 2, (
        f"失敗日只算到 {rows[0]['provider_calls']} 次呼叫 —— "
        "落回路徑那次沒被算進去")


def test_the_no_output_branch_also_defers(luna_on, monkeypatch):
    """**兩個失敗分支都要延到落回之後**(第十三輪 r1,#1)。

    「Luna 拋例外」與「Luna 回空」是兩條各自寫帳的分支;只測其中一條,
    另一條改回「立刻寫帳」也不會紅 —— 而我第一次的突變驗證正好證明了
    這件事(改無產出那條,測試全綠)。
    """
    rows = []
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "e")
    monkeypatch.setattr(mr, "_persist_experiment_record",
                        lambda rec, today: rows.append(rec))
    # 合法 JSON、但 schema 不合 → `_luna_analysis` 回空字串(不是拋例外)
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: _response({"完全": "不合 schema"}))

    def _legacy(prompt):
        mr._record_llm_call("primary", "deepseek", "deepseek-v4-pro",
                            accepted=True,
                            usage={"prompt_tokens": 100, "completion_tokens": 10})
        return ("## 我的明確立場\n立場:偏多\n既有路徑寫的分析。\n"
                "## 一句話總結\n維持核心部位。")

    monkeypatch.setattr(mr, "_call_llm_text", _legacy)
    mr._RUN_MANIFEST.pop("llm", None)
    mr._call_llm_analysis_impl(*_ARGS)
    assert len(rows) == 1 and rows[0]["primary_ok"] is False
    # **精確值,不是下界。** 這條路徑 Luna 自己就有 2 次(初次 + 修補),
    # 所以 `>= 2` 在還沒算 legacy 時就已經滿足 —— 第一版正是這樣寫的,
    # 而突變(改回立刻寫帳)因此不紅。**門檻訂得比實際低,等於沒訂。**
    assert rows[0]["provider_calls"] == 3, (
        f"應是 2 次 Luna(初次+修補)+ 1 次 legacy,實際 "
        f"{rows[0]['provider_calls']} —— 落回那次沒算進去")


def test_the_failure_row_is_written_even_when_everything_fails(luna_on,
                                                               monkeypatch):
    """**`finally` 要涵蓋每一個出口。**

    這一段有七個 return;用「在每個 return 前補一行」的寫法,
    漏掉任何一個都不會有錯誤訊息 —— 只會讓那一類失敗日從帳本消失。
    """
    rows = []
    monkeypatch.setattr(mr, "LLM_EXPERIMENT_ID", "e")
    monkeypatch.setattr(mr, "_persist_experiment_record",
                        lambda rec, today: rows.append(rec))
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: (_ for _ in ()).throw(RuntimeError("逾時")))
    # 落回路徑也整個掛掉 → 走到備援文字那個出口
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: (_ for _ in ()).throw(RuntimeError("也掛了")))
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")
    mr._call_llm_analysis_impl(*_ARGS)
    assert len(rows) == 1, "連備援文字那個出口都要留下失敗紀錄"
