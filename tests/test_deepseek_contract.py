# -*- coding: utf-8 -*-
"""**DeepSeek Responses 的契約,以實機回應為據**(外審 P1-2)。

外審指出的問題不是「這條路跑不通」,而是**它沒有契約**:
沿用 OpenAI 的 adapter 換 base URL,唯一的路由測試自己造一個 OpenAI 形狀的
假回應 —— 那證明不了 DeepSeek 生產會回什麼。

所以這一份的判準全部釘在 `tests/fixtures/deepseek_responses_v1.json` 上:
那是 2026-08-08 用生產同一條 `build_payload`(32K strict schema、
`effort=max`)對 `api.deepseek.com/v1/responses` 送出後的**真實回應**,
去掉 id / 時間戳 / instructions / schema 內容並縮短文字,
**形狀、鍵名、巢狀結構、usage 數字一字未改**。

fixture 對不上就是 provider 換了契約 —— 那時要改的是 adapter,
不是把測試改成通過。
"""
from __future__ import annotations

import json
from pathlib import Path

import deepseek_responses as ds

_FIXTURE = Path(__file__).parent / "fixtures" / "deepseek_responses_v1.json"


def _real() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 實機形狀

def test_the_captured_response_still_has_the_shape_we_parse():
    """**這條是其餘所有斷言的地基。** 它守的是 fixture 本身的形狀,
    而不是 adapter —— 兩者分開才看得出「是誰變了」。"""
    r = _real()
    assert r["status"] == "completed"
    assert r["model"] == "deepseek-v4-flash"
    types = [i["type"] for i in r["output"]]
    assert types == ["reasoning", "message"], (
        f"實機回應的 output 順序/型別變了:{types}")
    # 思考是**獨立的一項**,不是 message 的一部分
    assert r["output"][0]["content"][0]["type"] == "reasoning_text"
    msg = r["output"][1]
    assert msg["phase"] == "final_answer" and msg["role"] == "assistant"
    assert msg["content"][0]["type"] == "output_text"
    assert set(r["usage"]) >= {"input_tokens", "output_tokens", "total_tokens",
                               "input_tokens_details", "output_tokens_details"}


def test_the_answer_is_taken_and_the_reasoning_is_not():
    """**最容易出錯的一條**:思考佔了實測 45,085 字,混進去就是拿
    思考過程當 JSON 解析,而失敗的樣子會被讀成「模型不聽話」。"""
    out = ds.extract_output(_real())
    assert out["status"] == "completed"
    assert out["text"].startswith("{"), out["text"][:80]
    assert "repair task" not in out["text"], "思考內容混進答案了"
    assert json.loads(out["text"])["executive_summary"]
    assert out["empty_content"] is False and out["refusal"] == ""


def test_the_reasoning_is_reachable_for_telemetry_only():
    """思考取得到(遙測要看推理量),但它與答案是兩個不同的取用點。"""
    assert "repair task" in ds.reasoning_text(_real())
    assert ds.reasoning_text({}) == "" and ds.reasoning_text(None) == ""


def test_the_applied_effort_comes_from_the_response():
    """要求值與**生效值**分開 —— 靜默退回預設時 manifest 不得顯示我們要的。"""
    assert ds.applied_effort(_real()) == "max"
    assert ds.applied_effort({"reasoning": {}}) == ""
    assert ds.applied_effort(None) == ""


def test_usage_maps_onto_the_shared_cost_fields():
    """usage 讀錯的症狀是**成本靜默記成 0**,而不是報錯。"""
    u = ds.normalize_usage(_real()["usage"])
    assert u["prompt_tokens"] == 13_506
    assert u["completion_tokens"] == 17_491
    assert u["total_tokens"] == 30_997
    assert u["prompt_tokens_details"]["cached_tokens"] == 13_312
    assert u["completion_tokens_details"]["reasoning_tokens"] == 13_566
    # 沒有的欄位不得憑空造(缺 ≠ 0)
    assert "cache_write_tokens" not in u["prompt_tokens_details"]
    assert ds.normalize_usage(None) == {} and ds.normalize_usage("x") == {}


def test_visible_output_separates_thinking_from_answer():
    """13,566 推理 / 17,491 總輸出 → 可見答案只有 3,925 個 token。
    只看總輸出的話,「想很多寫很少」與「想很少寫很多」一模一樣。"""
    assert ds.visible_output_tokens(_real()["usage"]) == 17_491 - 13_566
    assert ds.visible_output_tokens({"output_tokens": 100}) == 100
    assert ds.visible_output_tokens({}) is None


# ---------------------------------------------------------------- 契約的邊角

def test_a_fenced_answer_is_unwrapped_not_rejected():
    """**strict schema 是指引不是保證。** 官方 JSON 模式只保證「合法 JSON
    字串」,實測看過答案被 ```json 圍欄包起來 —— 那是包裝問題,不是
    內容不合格,不該讓整條路徑落回。"""
    r = _real()
    inner = r["output"][1]["content"][0]["text"]
    r["output"][1]["content"][0]["text"] = f"```json\n{inner}\n```"
    assert json.loads(ds.extract_output(r)["text"])["executive_summary"]
    # 沒有圍欄的原樣不動
    assert ds.strip_json_fence('{"a":1}') == '{"a":1}'
    # 只有開頭沒有結尾的圍欄也要剝得掉(被截斷時會這樣)
    assert ds.strip_json_fence('```json\n{"a":1}') == '{"a":1}'


def test_an_empty_content_is_flagged_not_silently_parsed():
    """官方明說 JSON 模式偶爾回**空 content**。「回了但沒東西」與
    「沒回」對呼叫端是不同處置,不能長得像解析失敗。"""
    r = _real()
    r["output"][1]["content"] = [{"type": "output_text", "text": ""}]
    out = ds.extract_output(r)
    assert out["empty_content"] is True and out["text"] == ""
    # 沒有 message 的情況(通常是被截斷)**不算** empty_content
    r2 = _real()
    r2["output"] = [r2["output"][0]]
    assert ds.extract_output(r2)["empty_content"] is False


def test_an_incomplete_response_reports_its_reason():
    """輸出額度不夠時要說得出原因 —— 那決定要不要加額度重試。"""
    r = _real()
    r["status"] = "incomplete"
    r["incomplete_details"] = {"reason": "max_output_tokens"}
    out = ds.extract_output(r)
    assert out["status"] == "incomplete"
    assert out["incomplete_reason"] == "max_output_tokens"


def test_a_refusal_is_surfaced_separately_from_an_empty_answer():
    r = _real()
    r["output"][1]["content"] = [{"type": "refusal", "refusal": "無法協助"}]
    out = ds.extract_output(r)
    assert out["refusal"] == "無法協助" and out["empty_content"] is False


def test_commentary_is_not_mixed_into_the_final_answer():
    """有 `final_answer` 就只取它 —— 旁白混進 JSON 會讓解析失敗,
    而失敗的樣子是「模型不聽話」,實際上是我們讀錯了。"""
    r = _real()
    r["output"].insert(1, {"type": "message", "phase": "commentary",
                           "role": "assistant",
                           "content": [{"type": "output_text",
                                        "text": "讓我想想…"}]})
    out = ds.extract_output(r)
    assert out["had_commentary"] is True
    assert "讓我想想" not in out["text"]
    assert json.loads(out["text"])["executive_summary"]


def test_garbage_input_never_raises():
    """**晨報不可斷。** 解析端拿到任何東西都只能回空,不能拋。"""
    for bad in (None, "", 0, [], {"output": "not a list"},
                {"output": [None, 3, {"type": "message", "content": "x"}]}):
        out = ds.extract_output(bad)
        assert out["text"] == "" and isinstance(out, dict)


# ---------------------------------------------------------------- 請求側

def test_the_request_carries_the_format_type_that_deepseek_requires():
    """**2026-08-08 實測**:`text.format` 少了 `type` 是 400
    (`missing field 'type'`),而那個 400 不指名任何選配欄位 ——
    退讓迴圈救不了,整份分析當場作廢。"""
    body = ds.build_payload(
        model="deepseek-v4-flash", instructions="i", user_input="u",
        effort="max", response_format={"name": "x", "schema": {}, "strict": True})
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    # 呼叫端自己給了 type 就尊重它
    body2 = ds.build_payload(
        model="m", instructions="i", user_input="u",
        response_format={"type": "json_object"})
    assert body2["text"]["format"]["type"] == "json_object"


def test_the_captured_request_shape_is_what_we_still_build():
    """實機**被接受**的那份請求有哪些鍵 —— 少一個或多一個都可能 400。"""
    body = ds.build_payload(
        model="deepseek-v4-flash", instructions="i", user_input="u",
        effort="max", verbosity="high",
        response_format={"name": "x", "schema": {}, "strict": True},
        max_output_tokens=112_000, store=False,
        reasoning_summary="auto", reasoning_context="current_turn",
        prompt_cache_key="morning-luna56_xhigh_v1")
    assert set(body) == {"model", "instructions", "input", "store",
                         "safety_identifier", "reasoning", "text",
                         "max_output_tokens", "prompt_cache_key"}
    assert body["reasoning"] == {"effort": "max", "summary": "auto",
                                 "context": "current_turn"}
    assert body["safety_identifier"] == "morning-report-tw"
    assert "@" not in body["safety_identifier"], "識別碼不得含個資"


def test_optional_fields_can_be_dropped_one_at_a_time():
    """400 指名選配欄位時逐一退讓,而不是整份作廢。
    `reasoning.context` 也在清單裡 —— 那是 OpenAI 的欄位,DeepSeek 未文件化。"""
    body = ds.build_payload(
        model="m", instructions="i", user_input="u", effort="max",
        prompt_cache_key="k", prompt_cache_ttl_seconds=60)
    assert "reasoning.context" in ds.OPTIONAL_FIELDS
    for field in ds.OPTIONAL_FIELDS:
        after = ds.drop_field(body, field)
        parent = field.split(".")[0]
        if "." in field:
            assert field.split(".")[1] not in (after.get(parent) or {})
        else:
            assert parent not in after
        assert body != after or parent not in body, "drop 沒有生效"
    # **不就地改** —— manifest 記到的必須是原本要送的那一份
    ds.drop_field(body, "reasoning.summary")
    assert body["reasoning"]["summary"] == "auto"


# ---------------------------------------------------------------- 生產接線

def test_production_parses_the_real_captured_response_end_to_end(monkeypatch):
    """**整條特化路徑吃真實回應的形狀**(外審 P1-2 的核心要求)。

    先前唯一的路由測試自己造一個 OpenAI 形狀的假回應 —— 它證明不了
    DeepSeek 生產會回什麼。這裡把實機 fixture 的**外殼**原樣拿來,
    只換掉答案內文(fixture 的答案已縮短、過不了 schema),
    於是「reasoning 項不得混進答案」「usage 讀得到」「effort 讀得到」
    這三件事是由真實形狀驗的。
    """
    import fixtures_analysis as fx
    import morning_report as mr

    monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(mr, "LLM_PRIMARY_PROMPT_PROFILE", "")
    monkeypatch.setattr(mr, "_PRIMARY_EFFORT", "max")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")

    good = fx.valid_analysis()
    good["data_gaps"] = [
        {"gap_id": g, "what_is_missing": "這項檢查需要的行情欄位",
         "impact_on_conclusions": "今天這個面向沒有答案"}
        for g in ("gap:us_vs_taifex", "gap:prediction_vs_breadth",
                  "gap:sector_internal_divergence", "gap:rates_vs_tech")]
    real = _real()
    real["output"][1]["content"][0]["text"] = json.dumps(good, ensure_ascii=False)

    monkeypatch.setattr(mr, "_call_deepseek_responses", lambda p: real)
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: __import__("pytest").fail("不該落回 legacy"))
    mr._RUN_MANIFEST.pop("llm", None)
    args = ({"QQQ": {"close": 500.0, "change_pct": 1.0}}, {"fair_value": 100.0},
            {"model1": 1000.0}, fx.news(), [], "")
    text = mr._call_llm_analysis_impl(*args)
    assert mr._analysis_complete_enough(text)
    # 思考那 13,566 個 token 不得被當成答案的一部分
    assert "repair task" not in text
    primary = (mr._RUN_MANIFEST.get("llm") or {}).get("primary") or {}
    assert primary["prompt_tokens"] == 13_506, "usage 沒有從真實形狀讀出來"
    assert primary["reasoning_tokens"] == 13_566
    assert primary["applied_effort"] == "max", "生效強度沒有從回應讀"
    assert primary["provider"] == "deepseek"
    assert primary["model"] == "deepseek-v4-flash"


def test_an_empty_content_is_repaired_before_giving_up(monkeypatch):
    """空 content 是**可修補的** —— 直接放棄等於把一次可救的呼叫當失敗,
    而修補額度本來就留著給這種事(官方明說 JSON 模式偶爾會這樣)。"""
    import fixtures_analysis as fx
    import morning_report as mr

    monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(mr, "LLM_PRIMARY_PROMPT_PROFILE", "")
    monkeypatch.setattr(mr, "_PRIMARY_EFFORT", "max")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")

    good = fx.valid_analysis()
    good["data_gaps"] = [
        {"gap_id": g, "what_is_missing": "x", "impact_on_conclusions": "y"}
        for g in ("gap:us_vs_taifex", "gap:prediction_vs_breadth",
                  "gap:sector_internal_divergence", "gap:rates_vs_tech")]
    empty, full = _real(), _real()
    empty["output"][1]["content"] = [{"type": "output_text", "text": ""}]
    full["output"][1]["content"][0]["text"] = json.dumps(good, ensure_ascii=False)

    calls = []

    def _fake(payload):
        calls.append(payload)
        return empty if len(calls) == 1 else full

    monkeypatch.setattr(mr, "_call_deepseek_responses", _fake)
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: __import__("pytest").fail("空 content 就放棄了"))
    mr._RUN_MANIFEST.pop("llm", None)
    args = ({"QQQ": {"close": 500.0, "change_pct": 1.0}}, {"fair_value": 100.0},
            {"model1": 1000.0}, fx.news(), [], "")
    assert mr._analysis_complete_enough(mr._call_llm_analysis_impl(*args))
    assert len(calls) == 2 and "REPAIR" in calls[1]["input"]
    assert (mr._RUN_MANIFEST.get("llm") or {}).get("empty_content_seen") is True, (
        "空 content 沒有留痕 —— 那是 provider 契約的訊號,不得靜默")


def test_choosing_openai_never_routes_into_the_deepseek_path(monkeypatch):
    """**選 openai 不得被送去打 DeepSeek**(adapter 專屬化之後的必然要求)。

    特化路徑現在用 `deepseek_responses` 的契約、DeepSeek 的 base URL 與金鑰。
    先前 `openai` 也映到特化 profile —— 手上有 DeepSeek 金鑰時,
    使用者要的 openai 會被靜默換成 DeepSeek,而 manifest 顯示的仍是 openai。
    """
    import fixtures_analysis as fx
    import morning_report as mr

    monkeypatch.setattr(mr, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.setattr(mr, "LLM_PRIMARY_PROMPT_PROFILE", "")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")
    monkeypatch.setattr(mr, "_call_deepseek_responses",
                        lambda p: __import__("pytest").fail(
                            "選 openai 卻走了 DeepSeek 的特化路徑"))
    monkeypatch.setattr(mr, "_call_llm_text",
                        lambda p: "## 我的明確立場\n立場:中性\n\n## 一句話總結\n照舊。")
    args = ({"QQQ": {"close": 500.0, "change_pct": 1.0}}, {"fair_value": 100.0},
            {"model1": 1000.0}, fx.news(), [], "")
    assert "照舊" in mr._call_llm_analysis_impl(*args)
