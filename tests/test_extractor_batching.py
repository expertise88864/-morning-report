# -*- coding: utf-8 -*-
"""repo-wide 外審 Commit D/E:抽取器分批與 Gemini adapter 收尾。

Commit D:**不要再 35 則塞成單一一筆 LLM 交易**(2026-08-17 生產:
35 進 1 出 —— 答案是隨則數成長的大陣列,一次截斷整包沉沒)。
有界批次 + per-batch coverage + Gemini 抽取器關思考。

Commit E:Gemini 串接**所有** answer parts(只讀 parts[0] 會把後半段
靜默丟掉);retired 模型連同 telemetry registry 一起清掉。
"""
import json

import completion_contract as cc
import morning_report as mr


def _news(n):
    return [{"title": f"台積電消息{i}", "summary": "", "source": "鉅亨台股",
             "published": "2026-08-19T06:00:00+08:00"} for i in range(n)]


def _payload_items(prompt: str) -> list:
    """從 prompt 的 UNTRUSTED 圍欄取出實際送進去的清單。"""
    body = prompt.split("<UNTRUSTED_SOURCE_DATA>\n", 1)[1]
    body = body.split("\n</UNTRUSTED_SOURCE_DATA>", 1)[0]
    return json.loads(body)


def _wire(monkeypatch, fake):
    monkeypatch.setattr(mr, "_call_deepseek_extractor", fake)
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")


def test_the_extractor_splits_items_into_bounded_batches(monkeypatch):
    """35 則 → 依 `EXTRACTOR_BATCH_ITEMS`(12)分 3 批,各批不重不漏。

    分批是**呼叫層**的事實,必須從送出去的 prompt 量,不能只看 manifest
    自報(manifest 可以寫對而呼叫仍是一整包)。
    """
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        return "[]"

    _wire(monkeypatch, fake)
    mr.call_llm_event_extractor(_news(35), [])
    sizes = [len(_payload_items(p)) for p in prompts]
    assert sizes == [12, 12, 11], sizes
    ids = [tuple(it["source_item_id"] for it in _payload_items(p))
           for p in prompts]
    flat = [i for grp in ids for i in grp]
    assert len(flat) == len(set(flat)) == 35, "批與批之間重複或漏送"
    stat = mr._RUN_MANIFEST.get("llm_extractor") or {}
    assert [b["items"] for b in stat.get("batches") or []] == [12, 12, 11]
    assert stat.get("outcome") == "ok"


def test_one_failed_batch_does_not_sink_the_others(monkeypatch):
    """**一批失敗只損失那一批** —— 分批的第二個目的。

    第 2 批被內容裁決擋下(不可重試),第 1/3 批的事件必須活著;
    manifest 要把那一批標成 error、整體標成 partial,不得掩成 ok。
    """
    calls = {"n": 0}

    def fake(prompt):
        calls["n"] += 1
        items = _payload_items(prompt)
        if calls["n"] == 2:
            raise mr.DeepSeekCompletionError(
                "content_filter", cc.FILTERED, "被擋")
        return json.dumps([{
            "entity": "2330", "event_type": "earnings", "direction": 1,
            "confidence": 0.6, "lifecycle": "confirmed",
            "title": items[0]["title"],
            "source_item_ids": [items[0]["source_item_id"]]}])

    _wire(monkeypatch, fake)
    out = mr.call_llm_event_extractor(_news(35), [])
    stat = mr._RUN_MANIFEST.get("llm_extractor") or {}
    outcomes = [b["outcome"] for b in stat["batches"]]
    assert outcomes[0] == "ok" and outcomes[2] == "ok", outcomes
    assert outcomes[1].startswith("error:"), outcomes
    assert stat["outcome"] == "partial:1/3", stat["outcome"]
    assert stat["valid"] == 2, stat
    assert any(e.get("source") == "LLM extractor" for e in out), \
        "其餘批次的事件沒有活到輸出"


def test_all_batches_failing_falls_back_to_deterministic(monkeypatch):
    """全軍覆沒 —— 與分批前「整個失敗」同一種結局:退回確定性事件,
    outcome 標 error 而不是 partial(partial 意味著還有活的批)。"""

    def fake(_prompt):
        raise mr.DeepSeekCompletionError("content_filter", cc.FILTERED, "被擋")

    _wire(monkeypatch, fake)
    out = mr.call_llm_event_extractor(_news(35), [])
    stat = mr._RUN_MANIFEST.get("llm_extractor") or {}
    assert stat["outcome"] == "error:all_batches", stat["outcome"]
    assert not any(e.get("source") == "LLM extractor" for e in out)


def test_truncation_halves_the_batch_not_the_whole_list(monkeypatch):
    """額度用完的減量重試以**批**為單位:12 則的批減成 6 則 ——
    不是回頭把 35 則的總表減半(那會把別批的新聞攪進來)。"""
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise mr.ExtractorOutputTruncated("額度用完")
        return "[]"

    _wire(monkeypatch, fake)
    mr.call_llm_event_extractor(_news(35), [])
    first_ids = [it["source_item_id"] for it in _payload_items(prompts[0])]
    retry_ids = [it["source_item_id"] for it in _payload_items(prompts[1])]
    assert len(first_ids) == 12 and len(retry_ids) == 6, \
        (len(first_ids), len(retry_ids))
    assert retry_ids == first_ids[:6], "減半必須留在同一批之內"


# ---------------------------------------------------------- Commit D:關思考


def _gemini_stub(monkeypatch, *, finish="STOP", parts=None):
    captured = {}
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "k")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"finishReason": finish,
                                    "content": {"parts": parts or
                                                [{"text": "ok"}]}}]}

    def fake_post(url, json, timeout, headers=None):
        captured["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(mr.requests, "post", fake_post)
    return captured


def test_the_gemini_extractor_disables_thinking(monkeypatch):
    """抽取是**抄錄不是推理**:2.5 系列預設開思考,思考曾把額度吃光、
    答案在陣列中間被切斷(2026-08-17 生產)。抽取器送
    `thinkingBudget: 0`;主分析**不得**跟著關(它要的就是推理)。"""
    cap = _gemini_stub(monkeypatch)
    mr._call_gemini_once("gemini-2.5-flash", "p", role="extractor")
    tc = cap["payload"]["generationConfig"].get("thinkingConfig")
    assert tc == {"thinkingBudget": 0}, tc
    cap2 = _gemini_stub(monkeypatch)
    mr._call_gemini_once("gemini-2.5-flash", "p", role="primary")
    assert "thinkingConfig" not in cap2["payload"]["generationConfig"]


# ---------------------------------------------------- Commit E:多 part 串接


def test_gemini_joins_all_answer_parts(monkeypatch):
    """官方契約允許答案拆成多個 part —— 只讀 parts[0] 會把後半段
    **靜默丟掉**,對抽取器是「合法 JSON 但少一半事件」的同型危險。"""
    _gemini_stub(monkeypatch, parts=[{"text": '[{"entity":'},
                                     {"text": '"2330"}]'}])
    out = mr._call_gemini_once("gemini-2.5-flash", "p")
    assert out == '[{"entity":"2330"}]', out


def test_gemini_thought_parts_are_not_the_answer(monkeypatch):
    """`thought: true` 的 part 是思考摘要不是答案 —— 串進去會污染 JSON。"""
    _gemini_stub(monkeypatch, parts=[{"thought": True, "text": "我想想…"},
                                     {"text": "[]"}])
    assert mr._call_gemini_once("gemini-2.5-flash", "p") == "[]"


def test_the_retired_model_left_the_registry_too():
    """runtime 降級鏈已無 `gemini-2.0-flash`(2026-06-01 退役),而
    **registry 留著它,未來有人看到表裡「支援」就可能重新放回 runtime**
    (repo-wide 外審 P3)。連同 `MODEL_LIMITS` 一起清掉;未收錄的模型
    自動落到保守上限,性質不變。"""
    import llm_telemetry as lt
    assert "gemini-2.0-flash" not in mr.GEMINI_FALLBACK_MODELS
    assert "gemini-2.0-flash" not in lt.MODEL_LIMITS
    limit, src = lt.max_output_for("gemini-2.0-flash")
    assert limit == lt.UNKNOWN_MODEL_MAX_OUTPUT and "未收錄" in src


# ------------------------------------------------ 外審 D/E r1:P1 全程 deadline


def test_batches_stop_launching_when_the_run_budget_expires(monkeypatch):
    """進場閘只估了一筆交易 —— 分批把最壞情況乘上批數。時間預算歸零時,
    後續批**不啟動**(第一批已由進場閘付過訂金),標 skipped 而不是掩過,
    帶著已抽到的事件繼續 —— 保住主分析與寄信的核心尾段。"""
    calls = {"n": 0}

    def fake(prompt):
        calls["n"] += 1
        items = _payload_items(prompt)
        return json.dumps([{
            "entity": "2330", "event_type": "earnings", "direction": 1,
            "confidence": 0.6, "lifecycle": "confirmed",
            "title": items[0]["title"],
            "source_item_ids": [items[0]["source_item_id"]]}])

    _wire(monkeypatch, fake)
    # 剩餘時間 = 核心尾段保留 → 抽取器額度 0,deadline 立即到期
    monkeypatch.setattr(mr, "_run_seconds_left",
                        lambda: mr._core_tail_seconds())
    out = mr.call_llm_event_extractor(_news(35), [])
    assert calls["n"] == 1, f"到期後仍啟動了新批(共 {calls['n']} 次呼叫)"
    stat = mr._RUN_MANIFEST.get("llm_extractor") or {}
    outcomes = [b["outcome"] for b in stat["batches"]]
    assert outcomes == ["ok", "skipped:deadline", "skipped:deadline"], outcomes
    assert stat["outcome"] == "partial:2/3", stat["outcome"]
    assert any(e.get("source") == "LLM extractor" for e in out), \
        "第一批的事件沒有活下來"


# ------------------------------------------------ 外審 D/E r1:P2 跨批 ID 圍欄


def test_an_id_from_another_batch_is_fenced_out(monkeypatch):
    """`source_item_id` 是全域編號,prompt 範例寫著 ["n3","n17"] ——
    別批的模型抄範例就會把事件掛到**無關的新聞**上(時間/provenance 被
    當權威)。只認實際送進該批的 ID:圈外的剝掉;剝光了就沒有 ID,
    provenance 落到 llm_self_reported。"""
    calls = {"n": 0}

    def fake(prompt):
        calls["n"] += 1
        items = _payload_items(prompt)
        own = items[0]["source_item_id"]
        if calls["n"] == 2:
            # 批 2:抄了範例的 n3(屬於批 1)+ 自己的 ID
            return json.dumps([{
                "entity": "2330", "event_type": "earnings", "direction": 1,
                "confidence": 0.6, "lifecycle": "confirmed",
                "title": items[0]["title"],
                "published": "2026-01-05T00:00:00+00:00",
                "source_item_ids": ["n3", own]}])
        if calls["n"] == 3:
            # 批 3:**只**抄了範例的 n17(屬於批 2)
            return json.dumps([{
                "entity": "2317", "event_type": "orders", "direction": 1,
                "confidence": 0.6, "lifecycle": "confirmed",
                "title": "鴻海外洩測試事件",
                "published": "2026-01-05T00:00:00+00:00",
                "source_item_ids": ["n17"]}])
        return "[]"

    _wire(monkeypatch, fake)
    out = mr.call_llm_event_extractor(_news(35), [])
    stat = mr._RUN_MANIFEST.get("llm_extractor") or {}
    assert stat["batches"][1].get("cross_batch_ids_dropped") == 1
    assert stat["batches"][2].get("cross_batch_ids_dropped") == 1
    llm = {e["title"]: e for e in out if e.get("source") == "LLM extractor"}
    # 批 2 的事件:圈內 ID 存活 → 權威時間來自來源項(不是模型自報的 1/5)
    ev2 = llm.get("台積電消息12")
    assert ev2 and ev2.get("provenance") == "source_item_id", ev2
    assert not str(ev2.get("published", "")).startswith("2026-01-05")
    # 批 3 的事件:ID 剝光 → 不得拿到 source_item_id 的 provenance
    ev3 = llm.get("鴻海外洩測試事件")
    assert ev3 is not None, "圍欄不該把整個事件丟掉,只剝 ID"
    assert ev3.get("provenance") != "source_item_id", ev3


def test_the_deadline_bounds_requests_inside_a_batch(monkeypatch):
    """r2:光有啟動閘不夠 —— **正在跑的批(含第一批)也要被約束**。
    deadline 掛上既有的 `_LLM_DEADLINE` 鉗制:批內每個請求拿到的 timeout
    被剩餘額度夾住(不是預設的大 cap);抽取器結束後一定復原。"""
    import time as _time
    seen = []

    def fake(prompt):
        seen.append(mr._llm_request_timeout())
        return "[]"

    _wire(monkeypatch, fake)
    monkeypatch.setattr(mr, "_run_seconds_left",
                        lambda: mr._core_tail_seconds() + 5.0)
    assert mr._LLM_DEADLINE is None
    _t0 = _time.monotonic()
    mr.call_llm_event_extractor(_news(35), [])
    _elapsed = _time.monotonic() - _t0
    assert seen, "沒有請求問過 timeout"
    assert all(t <= 5.0 + _elapsed for t in seen), \
        f"批內請求沒被抽取器額度夾住:{seen}"
    assert mr._LLM_DEADLINE is None, "抽取器結束後 deadline 沒有復原"


def test_the_deepseek_extractor_request_is_clamped_too(monkeypatch):
    """r2:`_call_deepseek_extractor` 原本寫死 timeout=45 ——
    `_LLM_DEADLINE` 管不到這條路。改走 `_llm_request_timeout(45)`:
    有 deadline 時被剩餘額度夾住,沒有 deadline 時維持 45 上限。"""
    import time as _time
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    cap = {}

    class R:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": "[]"}}],
                    "usage": {}}

    def post(url, json=None, headers=None, timeout=None):
        cap["timeout"] = timeout
        return R()

    monkeypatch.setattr(mr.requests, "post", post)
    monkeypatch.setattr(mr, "_LLM_DEADLINE", _time.monotonic() + 5.0)
    mr._call_deepseek_extractor("p")
    assert cap["timeout"] <= 5.0, cap
    monkeypatch.setattr(mr, "_LLM_DEADLINE", None)
    mr._call_deepseek_extractor("p")
    assert cap["timeout"] <= 45.0, cap
