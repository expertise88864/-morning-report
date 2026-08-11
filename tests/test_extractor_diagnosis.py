# -*- coding: utf-8 -*-
"""**抽取器為什麼零產出 —— 診斷要能分開處置不同的原因**(2026-08-11)。

生產連續多天:`called=true, items=35, parsed=0, valid=0, outcome="ok"`,
沒有 `error`。也就是說解析器**靜靜回了空陣列**,而它有四條完全不同的
空路徑 —— 離線分不出是哪一段,只能猜。

四種原因的處置完全不同:
  * `empty_response`  → provider 沒回東西(換 provider / 看額度);
  * `no_array_found`  → 模型在講話而不是輸出 JSON(prompt 或 schema);
  * `truncated_array` → 開了括號沒有收(被截斷 → 減量重試/調高額度);
  * `bad_json_array`  → 括號齊全但解不開(格式);
  * `object_without_events` → 包了一層但鍵名不對(adapter 契約)。
"""
from __future__ import annotations

from llm_postprocess import _parse_llm_event_json as parse


def _kind(text):
    d: dict = {}
    out = parse(text, diag=d)
    return d.get("kind"), len(out), d


def test_each_empty_reason_gets_its_own_name():
    """四種原因分得開 —— 這正是上一版做不到的事。"""
    assert _kind("")[0] == "empty_response"
    assert _kind("我沒有找到任何符合條件的事件。")[0] == "no_array_found"
    assert _kind('[{"a":1} {"b":2}]')[0] == "bad_json_array"
    assert _kind('{"result":"none"}')[0] == "object_without_events"
    assert _kind('{"events":')[0] == "bad_json_object"


def test_a_truncated_answer_is_not_the_model_chatting():
    """**開了括號卻沒有收 = 被截斷**(外審 r1),不是「模型在講話」——
    兩者的處置不同:截斷要減量重試或調高輸出額度,模型講話要改
    prompt/schema。壓成同一個名字就等於沒有診斷。"""
    assert _kind('[{"a":1},{"b":')[0] == "truncated_array"
    # 包了一層的那條路徑同理:物件解不開,而裡面的陣列沒有收
    assert _kind('{"events":[{"a":1}')[0] == "truncated_array"
    assert _kind("我沒有找到任何符合條件的事件。")[0] == "no_array_found"


def test_the_successful_shapes_are_named_too():
    """成功也要說是哪一種形狀 —— `{"events": …}` 與裸陣列是兩條路,
    其中一條壞掉時要看得出來。"""
    kind, n, _ = _kind('[{"a":1},{"b":2}]')
    assert (kind, n) == ("ok_array", 2)
    kind, n, _ = _kind('{"events":[{"a":1}]}')
    assert (kind, n) == ("ok_object", 1)
    # 圍欄包起來的照樣解得開
    fenced = "```json" + chr(10) + '[{"a":1}]' + chr(10) + "```"
    assert _kind(fenced)[0] == "ok_array"
    # **空陣列是合法的「今天沒事件」**,與失敗要分得開
    assert _kind("[]")[0] == "array_without_objects"


def test_the_diagnosis_carries_the_shape_not_the_whole_answer():
    """形狀夠看出「這是不是 JSON」就好 —— 這個欄位會進 run manifest
    (公開 repo),不放整份回應。"""
    import llm_postprocess as lp
    long_text = "模型的長篇大論。" * 200
    kind, _, d = _kind(long_text)
    assert kind == "no_array_found"
    assert d["chars"] == len(long_text)
    assert len(d["head"]) == lp.PARSE_HEAD_CHARS


def test_the_diagnosis_is_optional_and_never_raises():
    """**晨報不可斷**:不給 `diag` 時行為與舊版完全相同。"""
    assert parse('[{"a":1}]') == [{"a": 1}]
    assert parse(None) == []
    assert parse("") == []


def test_the_caller_records_the_diagnosis():
    """**沒接上等於不存在**:呼叫端要把它寫進 manifest,
    否則下一次生產只會再靜默一次(這條盯的是接線本身)。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                  encoding="utf-8").read()
    i = src.index("_parse_llm_event_json(_call_or_halve(prompt)")
    seg = src[i - 400:i + 400]
    assert "diag=_pdiag" in seg, seg[-300:]
    assert '_stat["parse"]' in seg, seg[-300:]
    # 進 manifest 的外部文字要過既有的機密遮蔽
    assert "_redact_secret_text" in seg


def test_the_watchdog_message_says_which_reason():
    """零產出的缺陷訊息要**帶著原因** —— 只說「活到下游 0 筆」的話,
    收信的人還是得自己去猜是哪一段。"""
    import run_quality as rq
    m = {"git_sha": "a", "github_run_id": "1", "run_nonce": "x",
         "report_kind": rq.MORNING_REPORT,
         "llm": {"analysis_origin": "luna_specialized",
                 "payload_budget": {"chars_before": 1, "chars_after": 1,
                                    "limit": 9, "over_budget": False},
                 "primary_metrics": {"parsed": True, "claims": 3,
                                     "sections_present": 8,
                                     "validation_problems": 0},
                 "recap_saved": "saved",
                 "request_measurements": [{"role": "primary", "chars": 10,
                                           "tokens": 5, "accepted": True}]},
         "news": {"fulltext_plan": {"clusters": 3, "targets": 5,
                                    "available_news": 40}},
         "llm_extractor": {"called": True, "items": 35, "parsed": 0,
                           "valid": 0, "survived": 0, "outcome": "ok",
                           "parse": {"kind": "no_array_found", "chars": 812}}}
    hit = [p for p in rq.assess(m, mode="strict", expected_sha="a",
                                expected_run_id="1")
           if p["code"] == "event_extractor_dead"]
    assert hit, "前提:這一班本來就該被判缺陷"
    assert "no_array_found" in hit[0]["detail"], hit[0]["detail"]


# ===== 2026-08-11 診斷第一次上工:`bad_json_array` =====

def test_a_trailing_comma_is_repaired_losslessly():
    """**多餘的逗號是純語法缺陷,補它不改變任何語意**(生產第一次拿到的
    答案就是 `bad_json_array`)。只做這一種無損修補,而且要留下痕跡 ——
    「修過」與「本來就好」是兩件事。"""
    kind, n, d = _kind('[{"a":1},{"b":2},]')
    assert (kind, n) == ("ok_array_after_repair", 2)
    assert d["repair"] == "trailing_comma"
    # 物件內的多餘逗號同理
    assert _kind('[{"a":1,}]')[0] == "ok_array_after_repair"


def test_the_repair_never_touches_string_content():
    """**只動字串外面的逗號**(外審 r1):用正則掃整段的話,新聞標題裡的
    「…成長,}」也會被改掉 —— 那不是修語法,那是**竄改內容**,
    而且改完還會被當成正常解析。"""
    import json
    from llm_postprocess import _strip_trailing_commas as strip
    rows = [{"title": "營收成長,}", "note": "逗號在字串裡,]"}]
    body = json.dumps(rows, ensure_ascii=False)
    assert json.loads(strip(body)) == rows          # 一個字元都不能變
    assert json.loads(strip(body + " ")) == rows
    # 真的多餘的照樣去掉
    assert strip('[{"a":1},]') == '[{"a":1}]'
    assert strip('[{"a":1,}]') == '[{"a":1}]'
    assert strip("[1, 2]") == "[1, 2]"              # 正常的逗號不動
    # 走完整條解析路徑,字串內容原樣保留
    d = {}
    out = parse(json.dumps(rows, ensure_ascii=False)[:-1] + ",]", diag=d)
    assert out == rows and d["repair"] == "trailing_comma"


def test_a_genuinely_broken_array_is_still_reported():
    """修不好就照實回報,不猜內容 —— 而且要帶著 JSON 的錯誤訊息。"""
    kind, n, d = _kind('[{"a":1} {"b":2}]')
    assert (kind, n) == ("bad_json_array", 0)
    assert d.get("error"), d


def test_a_healthy_array_is_not_marked_as_repaired():
    """沒修過的不得掛上修補標記(不然帳面上永遠像有問題)。"""
    kind, _, d = _kind('[{"a":1}]')
    assert kind == "ok_array" and "repair" not in d

