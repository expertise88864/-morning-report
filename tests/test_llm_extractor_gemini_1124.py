"""2026-11-24:抽取器每天 45 秒逾時、OpenAI 429 後整批歸零;Gemini 備援換新型號並記帳。全程離線。"""
import datetime as dt

import pytest
import requests

import extractor_failover as xf
import gemini_adapter as ga
import llm_pricing as lp
import morning_report as mr


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _http(status):
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status}", response=response)


def test_deepseek_extractor_turns_thinking_off_and_records_usage(monkeypatch):
    sent = {}

    def post(url, json=None, **kw):
        sent.update(json)
        return _Resp({"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}],
                      "usage": {"prompt_tokens": 4000, "completion_tokens": 900, "total_tokens": 4900}})
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(mr.requests, "post", post)
    mr._RUN_MANIFEST.pop("llm", None)
    assert mr._call_deepseek_extractor("p") == "[]"
    assert sent["thinking"] == {"type": "disabled"}
    rec = mr._RUN_MANIFEST["llm"]["extractor"]
    assert rec["provider"] == "deepseek" and rec["completion_tokens"] == 900 and rec["estimated_cost_usd"] > 0


@pytest.mark.parametrize("status,blocked", [(401, True), (402, True), (429, True), (400, False), (500, False)])
def test_only_account_problems_count_as_blocked(status, blocked):
    assert xf.account_blocked(_http(status)) is blocked
    assert not xf.account_blocked(ValueError("x"))


def _keys(monkeypatch, *names):
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(k, "x") if k in names else monkeypatch.delenv(k, raising=False)


def test_a_blocked_backup_moves_on_and_is_not_tried_again(monkeypatch):
    _keys(monkeypatch, "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY")
    calls = []

    def dispatch(provider, prompt):
        calls.append(provider)
        if provider == "openai":
            raise _http(429)
        return "[]"
    fo, stat = xf.Failover(), {}
    assert fo.run(dispatch, "deepseek", fo.next("deepseek"), "p", stat) == "[]"
    assert fo.run(dispatch, "deepseek", fo.next("deepseek"), "p", stat) == "[]"
    assert calls == ["openai", "gemini", "gemini"] and stat["fallback_to"] == "gemini"


def test_a_bad_request_is_not_passed_along(monkeypatch):
    _keys(monkeypatch, "OPENAI_API_KEY", "GEMINI_API_KEY")

    def dispatch(provider, prompt):
        raise _http(400)
    with pytest.raises(requests.exceptions.HTTPError):
        xf.Failover().run(dispatch, "deepseek", "openai", "p", {})


def test_the_whole_extractor_survives_timeout_then_429(monkeypatch):
    """11-24 實況重演:DeepSeek 逾時、OpenAI 429,有 Gemini 就要跑完每一批。"""
    _keys(monkeypatch, "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY")
    seen = {"deepseek": 0, "openai": 0, "gemini": 0}

    def deepseek(p):
        seen["deepseek"] += 1
        raise requests.exceptions.ConnectionError("Read timed out.")

    def openai(p, **kw):
        seen["openai"] += 1
        raise _http(429)

    def gemini(p, role="primary"):
        seen["gemini"] += 1
        assert role == "extractor"
        return "[]"
    monkeypatch.setattr(mr, "EXTRACTOR_PROVIDER", "deepseek")
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setattr(mr, name, "x")
    monkeypatch.setattr(mr, "_call_deepseek_extractor", deepseek)
    monkeypatch.setattr(mr, "_call_openai", openai)
    monkeypatch.setattr(mr, "_call_gemini", gemini)
    mr._RUN_MANIFEST.pop("llm", None)
    news = [{"title": f"公司{i} 財報公布 營收成長", "source": "鉅亨", "link": f"https://e.example/{i}"} for i in range(30)]
    mr.call_llm_event_extractor(news, [])
    batches = mr._RUN_MANIFEST["llm_extractor"]["batches"]
    assert len(batches) >= 2 and all(b["outcome"] == "ok" for b in batches), batches
    assert seen["deepseek"] == 1 and seen["openai"] == 1 and seen["gemini"] >= len(batches)


def test_deepseek_out_of_balance_also_switches(monkeypatch):
    """10-30 實況:DeepSeek 402 欠費,先前整批歸零。"""
    _keys(monkeypatch, "GEMINI_API_KEY", "DEEPSEEK_API_KEY")

    def broke(p):
        raise _http(402)
    monkeypatch.setattr(mr, "EXTRACTOR_PROVIDER", "deepseek")
    for name in ("DEEPSEEK_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setattr(mr, name, "x")
    monkeypatch.setattr(mr, "_call_deepseek_extractor", broke)
    monkeypatch.setattr(mr, "_call_gemini", lambda p, role="primary": "[]")
    mr.call_llm_event_extractor([{"title": "台積電 財報公布", "source": "鉅亨", "link": "https://e.example/1"}], [])
    assert all(b["outcome"] == "ok" for b in mr._RUN_MANIFEST["llm_extractor"]["batches"])


def test_thinking_settings_follow_each_model_family():
    assert ga.generation_config("gemini-3.8-flash", "extractor", 9)["thinkingConfig"] == {"thinkingLevel": "low"}
    assert ga.generation_config("gemini-3.5-flash-lite", "extractor", 9)["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert ga.generation_config("gemini-2.5-flash-lite", "extractor", 9)["thinkingConfig"] == {"thinkingBudget": 0}
    assert ga.generation_config("gemini-3.8-flash", "primary", 9) == {"maxOutputTokens": 9}


def test_primary_gets_room_for_thinking_within_the_documented_limit():
    for model in mr.GEMINI_FALLBACK_MODELS:
        assert 32_000 <= mr._gemini_output_tokens(model, "primary") <= 65_536
    assert mr.GEMINI_FALLBACK_MODELS == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def test_usage_counts_thinking_as_output():
    meta = {"promptTokenCount": 100, "candidatesTokenCount": 30, "thoughtsTokenCount": 20, "totalTokenCount": 150,
            "cachedContentTokenCount": 10}
    u = ga.usage({"usageMetadata": meta})
    assert u["completion_tokens"] == 50 and u["completion_tokens_details"]["reasoning_tokens"] == 20
    del meta["totalTokenCount"]
    assert ga.usage({"usageMetadata": meta})["completion_tokens"] == 50
    for bad in (None, {}, {"usageMetadata": None}, {"usageMetadata": {"promptTokenCount": "x"}},
                {"usageMetadata": {"promptTokenCount": 5}}):
        assert ga.usage(bad) is None


def test_price_steps_and_source():
    before, after = (dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc) for y in (2026, 2027))
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    assert lp.estimate_cost("gemini-3.8-flash", usage, before)["usd"] == 4.5
    got = lp.estimate_cost("gemini-3.8-flash", usage, after)
    assert got["usd"] == 9.0 and "aistudio.google.com" in got["basis"] and "openai" not in got["basis"]
    assert lp.estimate_cost("gemini-3.5-flash-lite", usage, after)["usd"] == 2.8
    assert lp.estimate_cost("gemini-9", usage, after)["usd"] is None


@pytest.mark.parametrize("reason,accepted", [("STOP", True), ("MAX_TOKENS", False)])
def test_gemini_calls_are_billed_even_when_rejected(monkeypatch, reason, accepted):
    data = {"candidates": [{"finishReason": reason, "content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {"promptTokenCount": 100, "totalTokenCount": 150}}
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(mr.requests, "post", lambda *a, **k: _Resp(data))
    mr._RUN_MANIFEST.pop("llm", None)
    if accepted:
        assert mr._call_gemini("p") == "ok"
        assert mr._RUN_MANIFEST["llm"]["primary"]["completion_tokens"] == 50
    else:
        with pytest.raises(RuntimeError):
            mr._call_gemini("p")
        attempts = mr._RUN_MANIFEST["llm"]["attempts"]
        assert len(attempts) == 2 and all(a["completion_tokens"] == 50 for a in attempts)
        assert not any(a.get("billable_unmeasured") for a in attempts)
