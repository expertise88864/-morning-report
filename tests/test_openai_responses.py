# -*- coding: utf-8 -*-
"""**Responses API adapter 的契約**(Phase 3)。

這個檔的存在理由是一句話:**Responses 與 Chat Completions 的 usage 欄位名
完全不同,而既有的成本估算全部讀後者。** 沿用舊解析的話,Luna 那一班的 token
會全部讀成 None → 成本靜默記成 0,而十天實驗的結論正是建立在成本數字上。

「靜默記成 0」不會讓任何測試變紅,也不會讓信寄不出去 —— 它只會讓結論錯。
所以這裡逐欄位驗轉換,而不是驗「有回傳一個 dict」。
"""
import llm_telemetry as lt
import openai_responses as orx

#: 官方文件描述的回應形狀(2026-08-01 查證)。
_USAGE = {
    "input_tokens": 92_262,
    "output_tokens": 23_095,
    "total_tokens": 115_357,
    "input_tokens_details": {"cached_tokens": 80_000, "cache_write_tokens": 12_262},
    "output_tokens_details": {"reasoning_tokens": 18_400},
}


def test_usage_is_translated_into_the_shape_the_cost_code_reads():
    """**這是本檔最重要的一條。**

    既有 `estimate_cost` 讀 prompt_tokens / completion_tokens /
    prompt_tokens_details.*;Responses 給的是 input_tokens / output_tokens /
    input_tokens_details.*。不轉換的症狀是成本 = 0,而且不會有任何東西變紅。
    """
    u = orx.normalize_usage(_USAGE)
    assert u["prompt_tokens"] == 92_262
    assert u["completion_tokens"] == 23_095
    assert u["total_tokens"] == 115_357
    assert u["prompt_tokens_details"]["cached_tokens"] == 80_000
    assert u["prompt_tokens_details"]["cache_write_tokens"] == 12_262
    assert u["completion_tokens_details"]["reasoning_tokens"] == 18_400

    # 既有解析器要真的讀得到(不是只有欄位名長得像)
    assert lt.reasoning_tokens_of(u) == 18_400
    assert lt.cached_tokens_of(u) == 80_000
    assert lt.cache_write_tokens_of(u) == 12_262


def test_the_cost_of_a_luna_run_is_not_silently_zero():
    """端到端:Responses 的 usage 一路走到成本,而且金額合理。

    只驗「有數字」不夠 —— 我要驗它**用了快取費率**。快取是 $0.02 對 $0.20,
    十倍差距;算錯的方向會直接決定十天實驗的結論。
    """
    cost = lt.estimate_cost("gpt-5.6-luna", orx.normalize_usage(_USAGE))
    assert cost["usd"] is not None and cost["usd"] > 0, "Luna 的成本算成 0 或 None"
    # 92,262 輸入裡有 80,000 命中快取、12,262 是寫入,只剩 0 是純未快取
    # → 0.08*0.02 + 0.012262*0.20*1.25 + 0.023095*1.20 ≈ 0.0308
    assert 0.025 < cost["usd"] < 0.040, f"金額不合理:{cost}"
    assert "快取命中" in cost["basis"], f"沒有用到快取費率:{cost['basis']}"


def test_missing_usage_fields_are_left_missing_not_zeroed():
    """缺欄位與「值是 0」是兩件事。

    填成 0 會讓成本看起來精確,而它其實是猜的 —— 本 repo 的價格表規則就是
    「說得出數字從哪來」。
    """
    u = orx.normalize_usage({"input_tokens": 10, "output_tokens": 5})
    assert u == {"prompt_tokens": 10, "completion_tokens": 5}
    assert "prompt_tokens_details" not in u
    assert lt.cached_tokens_of(u) is None, "缺的快取欄位被填成 0 了"
    assert lt.reasoning_tokens_of(u) is None

    # 更真實的情況:**明細存在但少一個欄位**(舊回應有 cached_tokens、
    # 沒有 cache_write_tokens)。整個明細不存在只是最極端的那一種,
    # 只測它會讓「補零」的錯誤逃過 —— 我第一版就是這樣漏掉的。
    partial = orx.normalize_usage({
        "input_tokens": 100, "output_tokens": 20,
        "input_tokens_details": {"cached_tokens": 60},
        "output_tokens_details": {},
    })
    assert partial["prompt_tokens_details"] == {"cached_tokens": 60}, \
        f"少的欄位被補成 0 了:{partial}"
    assert lt.cache_write_tokens_of(partial) is None
    assert "completion_tokens_details" not in partial, \
        "空的 output_tokens_details 造出了一個假的推理 token 數"

    assert orx.normalize_usage(None) == {}
    assert orx.normalize_usage("垃圾") == {}


def test_visible_output_is_separated_from_reasoning():
    """「推理很多答案很短」與「推理很少答案很長」是兩種行為,不能長得一樣。"""
    assert orx.visible_output_tokens(_USAGE) == 23_095 - 18_400
    # 沒有推理明細時,總輸出就是可見輸出(不猜)
    assert orx.visible_output_tokens({"output_tokens": 100}) == 100
    assert orx.visible_output_tokens({}) is None


# ---------------------------------------------------------------- 回應解析

def _msg(text, phase=None, kind="output_text"):
    item = {"type": "message", "role": "assistant",
            "content": [{"type": kind, ("refusal" if kind == "refusal" else "text"): text}]}
    if phase:
        item["phase"] = phase
    return item


def test_commentary_is_not_mixed_into_the_final_answer():
    """**GPT-5.6 的 output message 有 `phase`。**

    把所有 `output_text` 串起來會把旁白混進 JSON,strict 解析就失敗 ——
    而失敗的樣子是「模型不聽話」,實際上是我們讀錯了。
    """
    resp = {"status": "completed", "output": [
        {"type": "reasoning", "summary": [{"text": "(內部)"}]},
        _msg("先講點題外話。", phase="commentary"),
        _msg('{"executive_summary":"今日偏多"}', phase="final_answer"),
    ]}
    out = orx.extract_output(resp)
    assert out["text"] == '{"executive_summary":"今日偏多"}'
    assert "題外話" not in out["text"]
    assert out["had_commentary"] is True


def test_unlabelled_messages_still_produce_text():
    """沒有 `phase` 標記時要退回「全部串接」,不得回空字串。

    回空字串的症狀是「模型什麼都沒回」,而實際上它回了 —— 那會讓
    降級路徑在不該啟動的時候啟動。
    """
    resp = {"status": "completed", "output": [_msg("答案一"), _msg("答案二")]}
    assert orx.extract_output(resp)["text"] == "答案一答案二"


def test_a_refusal_is_reported_separately_from_text():
    """拒答不是「空回應」。兩者的處置不同,混在一起會重試一件不會成功的事。"""
    resp = {"status": "completed", "output": [_msg("不能協助", kind="refusal")]}
    out = orx.extract_output(resp)
    assert out["refusal"] == "不能協助"
    assert out["text"] == ""


def test_an_incomplete_response_says_why():
    """被額度截斷與被內容過濾擋下,處置完全不同(前者減量重試有用)。"""
    resp = {"status": "incomplete", "output": [],
            "incomplete_details": {"reason": "max_output_tokens"}}
    out = orx.extract_output(resp)
    assert out["status"] == "incomplete"
    assert out["incomplete_reason"] == "max_output_tokens"


def test_extraction_never_raises_on_garbage():
    """解析器自己不得炸 —— 它一炸,降級路徑也走不到。"""
    for junk in (None, [], "字串", 42, {"output": "不是清單"},
                 {"output": [None, 7, {"type": "message"}]},
                 {"output": [{"type": "message", "content": [None, 3]}]}):
        out = orx.extract_output(junk)
        assert isinstance(out, dict) and "text" in out


def test_applied_effort_comes_from_the_response_not_the_request():
    """要求值與**生效值**必須分開。

    2026-08-01:luna 拒絕 `max` 時靜默退回 provider 預設,manifest 卻顯示
    我們要求的值 —— 看起來像有生效,而那一班的推理只有 379 token。
    """
    assert orx.applied_effort({"reasoning": {"effort": "xhigh"}}) == "xhigh"
    assert orx.applied_effort({"reasoning": {}}) == ""
    assert orx.applied_effort({}) == ""
    assert orx.applied_effort(None) == ""


# ---------------------------------------------------------------- 請求組裝

def _payload(**kw):
    base = dict(model="gpt-5.6-luna", instructions="穩定前綴",
                user_input="EVIDENCE\n{}", effort="xhigh",
                response_format={"type": "json_schema", "name": "x",
                                 "schema": {}, "strict": True},
                max_output_tokens=40_000, prompt_cache_key="morning-luna-v1",
                prompt_cache_ttl_seconds=1800)
    base.update(kw)
    return orx.build_payload(**base)


def test_the_request_uses_the_responses_shape_not_chat_completions():
    """欄位名錯了就是 400,而 400 在這條路徑上等於整份分析作廢。"""
    p = _payload()
    assert p["model"] == "gpt-5.6-luna"
    assert p["instructions"] == "穩定前綴"
    assert p["input"].startswith("EVIDENCE")
    assert p["reasoning"]["effort"] == "xhigh"
    assert p["text"]["format"]["strict"] is True
    assert p["text"]["verbosity"] == "high"
    assert p["max_output_tokens"] == 40_000
    # chat/completions 的欄位一個都不該出現
    for wrong in ("messages", "reasoning_effort", "response_format",
                  "max_completion_tokens"):
        assert wrong not in p, f"送出了 chat/completions 的欄位:{wrong}"


def test_instructions_and_evidence_stay_separate():
    """串成一段會讓穩定前綴打不中快取,而 cached 與 uncached 差十倍。"""
    p = _payload()
    assert "EVIDENCE" not in p["instructions"]
    assert "穩定前綴" not in p["input"]


def test_reasoning_context_is_pinned_to_the_current_turn():
    """GPT-5.6 **預設 all_turns**。晨報每天是獨立判斷,帶進昨天的推理
    只會多花錢又汙染結論 —— 所以要明設,不能靠預設。
    """
    assert _payload()["reasoning"]["context"] == "current_turn"


def test_nothing_is_stored_on_the_provider_side():
    """`store=false`:晨報的證據含新聞全文與市場資料,沒有理由留在對方那裡。"""
    assert _payload()["store"] is False


def test_the_safety_identifier_carries_no_personal_data():
    """官方建議帶 safety identifier,但它絕不能是收件者信箱或使用者名稱。"""
    ident = _payload()["safety_identifier"]
    assert ident and "@" not in ident
    assert ident == orx.SAFETY_IDENTIFIER
    for pii in ("gmail", "expertise", "user", "@"):
        assert pii not in ident.lower()


def test_optional_fields_can_be_dropped_one_at_a_time():
    """選配欄位被拒絕時要逐一退讓,不是整個請求作廢。

    `reasoning.summary` 官方明說需要組織驗證 —— 為了一個遙測欄位讓晨報斷掉
    是明顯錯誤的取捨。
    """
    p = _payload()
    assert "summary" in p["reasoning"]
    q = orx.drop_field(p, "reasoning.summary")
    assert "summary" not in q["reasoning"]
    assert q["reasoning"]["effort"] == "xhigh", "退讓時把推理強度一起弄掉了"
    assert "summary" in p["reasoning"], "drop_field 就地改了原本的 payload"

    r = orx.drop_field(p, "prompt_cache_options")
    assert "prompt_cache_options" not in r
    assert r["prompt_cache_key"] == "morning-luna-v1"

    for f in orx.OPTIONAL_FIELDS:
        assert isinstance(orx.drop_field(p, f), dict)


def test_cache_options_only_ride_along_with_a_cache_key():
    """沒有 cache key 就不該送 ttl —— 那是一個沒有意義的參數組合。"""
    p = _payload(prompt_cache_key="", prompt_cache_ttl_seconds=1800)
    assert "prompt_cache_options" not in p
    assert "prompt_cache_key" not in p
