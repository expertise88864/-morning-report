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
                     "evidence_ids": [], "counterevidence_ids": [],
                     "falsification_trigger": "夜盤翻黑"}],
    "scenario_tree": {"base": {"narrative": "震盪走高", "probability": 0.6,
                               "triggers": []},
                      "bull": {"narrative": "突破", "probability": 0.2,
                               "triggers": []},
                      "bear": {"narrative": "回測", "probability": 0.2,
                               "triggers": []},
                      "invalidation_triggers": []},
    "taiwan_market": {"summary": "量能回升。", "taiex_view": "偏多",
                      "tsmc_view": "守月線", "evidence_ids": []},
    "global_market": {"summary": "美股收紅。", "us_to_tw_linkage": "費半傳導",
                      "evidence_ids": []},
    "portfolio_implications": {"summary": "維持核心。",
                               "actions_to_consider": [], "risks": []},
    "top_news_analysis": [], "contradictions": [], "data_gaps": [],
    "watch_triggers": [], "claim_audit": [],
    "market_regime": {"label": "偏多", "evidence_ids": []},
    "priced_in": {"already_reflected": [], "not_yet_reflected": []},
}

_ARGS = ({"QQQ": {"close": 500.0}}, {"fair_value": 100.0},
         {"model1": 1000.0}, [], [], "")


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
