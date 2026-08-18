# -*- coding: utf-8 -*-
"""**一份 completion contract,三家 provider 共用**(repo-wide 外審 P1-2)。

先前三家各有一套政策,而且是各自演化出來的:

    Gemini    只有 `STOP` 算成功(2026-08-17 補上)
    DeepSeek  只擋 `length`
    OpenAI    只擋 `length`

於是 DeepSeek 的 `insufficient_system_resource`(官方文件:**因推理系統
資源不足而生成被中斷**)在 `content` 非空時被當成完整答案 —— 與先前
Gemini 那個缺陷同型:被中斷的回應剛好仍是合法 JSON(30 件事件只吐出前
5 件),解析器沒有理由知道它少了後半段,而 manifest 顯示這個能力健康。
"""
import ast
import io
from pathlib import Path

import completion_contract as cc

_ROOT = Path(__file__).resolve().parents[1]


def _src(name):
    return io.open(_ROOT / name, encoding="utf-8").read()


# ---------------------------------------------------------------- 對照本身

def test_only_a_normal_finish_counts_as_success():
    """**只有正常結束算成功。** 這是整份契約的一句話。"""
    for provider, reason in (("deepseek", "stop"), ("openai", "stop"),
                             ("gemini", "STOP")):
        assert cc.is_normal(provider, reason), (provider, reason)
    for provider, reason in (("deepseek", "length"),
                             ("deepseek", "content_filter"),
                             ("deepseek", "tool_calls"),
                             ("deepseek", "insufficient_system_resource"),
                             ("openai", "content_filter"),
                             ("openai", "function_call"),
                             ("gemini", "SAFETY"), ("gemini", "MAX_TOKENS")):
        assert not cc.is_normal(provider, reason), (provider, reason)


def test_the_deepseek_resource_interruption_is_the_production_counterexample():
    """外審點名的那一個:`content` 非空、`finish != length`,先前被當完整答案。"""
    out = cc.classify("deepseek", "insufficient_system_resource")
    assert out == cc.RESOURCE_INTERRUPTED
    assert out != cc.NORMAL
    # 暫時性 —— 換個時間或模型有意義,所以准許重試(與裁決類不同)
    assert cc.retryable(out) is True


def test_a_missing_or_unknown_reason_fails_closed():
    """**缺欄位也算異常。** 拒錯的代價是這一棒降級,接錯的代價是一份悄悄
    少掉內容的報告 —— 兩者不對稱。"""
    for reason in (None, "", "?", "some_new_reason_2027"):
        for provider in ("deepseek", "openai", "gemini"):
            assert cc.classify(provider, reason) == cc.UNKNOWN, (provider, reason)
            assert not cc.is_normal(provider, reason)
    # 不認得的 provider 也一樣 fail closed
    assert cc.classify("新的供應商", "stop") == cc.UNKNOWN


def test_a_verdict_is_not_worth_retrying_on_the_same_model():
    """截斷與內容裁決都是對**這一份請求**的判定 —— 原樣再送只是多付一次錢。"""
    assert cc.retryable(cc.TRUNCATED) is False
    assert cc.retryable(cc.FILTERED) is False
    assert cc.retryable(cc.TOOL_REQUEST) is False
    assert cc.retryable(cc.RESOURCE_INTERRUPTED) is True
    assert cc.retryable(cc.UNKNOWN) is True


def test_the_gemini_verdict_set_is_derived_not_a_second_copy():
    """**兩份政策一定會漂。** Gemini 先前自己維護一份 `_GEMINI_VERDICT_REASONS`。"""
    import morning_report as mr
    assert mr._GEMINI_VERDICT_REASONS == cc.verdict_reasons("gemini")
    # 而且不是空集合 —— 空集合會讓「不重試裁決」整條規則消失
    assert len(cc.verdict_reasons("gemini")) >= 8
    # 推導的來源要真的是契約:改契約就要改到它
    assert "SAFETY" in cc.verdict_reasons("gemini")
    assert "STOP" not in cc.verdict_reasons("gemini")


def test_every_provider_declares_its_normal_reason():
    """空表 = 每一個原因都 UNKNOWN = 這家 provider 永遠失敗。
    **空集合不算通過。**"""
    for provider in ("deepseek", "openai", "gemini"):
        table = cc._MAP[provider]
        assert table, provider
        assert cc.NORMAL in table.values(), provider


# ------------------------------------------------- 三家 adapter 真的接上了

def _calls_in(func_name: str, module: str = "morning_report.py") -> str:
    tree = ast.parse(_src(module))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == func_name)
    return ast.dump(fn)


def test_all_three_adapters_ask_the_shared_contract():
    """**沒有呼叫端的契約等於沒有契約。**

    三條路徑都要問同一份表:DeepSeek 主分析、DeepSeek 抽取器、OpenAI、Gemini。
    """
    for fn in ("_call_deepseek", "_call_deepseek_extractor", "_call_openai",
               "_call_gemini_once"):
        body = _calls_in(fn)
        assert "classify" in body, f"{fn} 沒有問共用契約"


def test_no_adapter_keeps_a_hand_written_finish_reason_policy():
    """**adapter 只做對照,不自己發明政策。**

    先前每家各自寫 `if finish == "length"` —— 那是三份政策。截斷仍然由
    `ExtractorOutputTruncated` 表達(它有專屬的減量重試語意),但**判定
    哪一個原因算截斷**要來自契約。
    """
    src = _src("morning_report.py")
    for banned in ('if _finish == "length"', 'if finish == "length"'):
        assert banned not in src, f"還有手寫的結束原因政策:{banned}"


def test_the_deepseek_error_carries_the_outcome_not_a_message_string():
    """呼叫端不必解析訊息字串就能決定要不要重試 —— 與 Gemini 那個同形狀。"""
    import morning_report as mr
    e = mr.DeepSeekCompletionError("insufficient_system_resource",
                                   cc.RESOURCE_INTERRUPTED, "x")
    assert e.outcome == cc.RESOURCE_INTERRUPTED and e.retryable is True
    e2 = mr.DeepSeekCompletionError("content_filter", cc.FILTERED, "x")
    assert e2.retryable is False


# ------------------------------------------- 端到端:被中斷的回應不得被接受

class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self.text = ""
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _deepseek_reply(finish, content='[{"entity":"2330","event_type":"earnings"}]'):
    return {"choices": [{"message": {"content": content},
                         "finish_reason": finish}],
            "usage": {"completion_tokens": 120}}


def test_an_interrupted_deepseek_reply_is_rejected_even_with_content(monkeypatch):
    """**外審點名的那一個。**

    `insufficient_system_resource` + 非空 content + 剛好是合法 JSON ——
    先前這會被當完整答案回傳,而它其實是被中斷的半截(30 件只吐了 5 件)。
    """
    import morning_report as mr
    monkeypatch.setattr(mr.requests, "post",
                        lambda *a, **k: _FakeResp(
                            _deepseek_reply("insufficient_system_resource")))
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(mr, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    try:
        mr._call_deepseek("prompt")
    except Exception as e:                                  # noqa: BLE001
        assert "insufficient_system_resource" in str(e), e
    else:
        raise AssertionError("被中斷的回應被當成完整答案接受了")


def test_an_interrupted_extractor_reply_is_rejected(monkeypatch):
    """抽取器最容易踩到:合法 JSON、只有前幾件,涵蓋率靜默下降。"""
    import morning_report as mr
    monkeypatch.setattr(mr.requests, "post",
                        lambda *a, **k: _FakeResp(
                            _deepseek_reply("insufficient_system_resource")))
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    try:
        mr._call_deepseek_extractor("prompt")
    except mr.DeepSeekCompletionError as e:
        assert e.outcome == cc.RESOURCE_INTERRUPTED, e.outcome
    else:
        raise AssertionError("抽取器接受了被中斷的回應")


def test_a_normal_reply_is_still_accepted(monkeypatch):
    """**反向**:正常結束照舊接受 —— 這條規則不是把 provider 關掉。"""
    import morning_report as mr
    monkeypatch.setattr(mr.requests, "post",
                        lambda *a, **k: _FakeResp(_deepseek_reply("stop", "答案")))
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    assert mr._call_deepseek_extractor("prompt") == "答案"


def test_a_filtered_openai_reply_is_rejected_even_with_content(monkeypatch):
    """OpenAI 也走同一份契約 —— **AST 測不到行為**(突變驗證抓到:把
    OpenAI 那段判斷改成 `if False`,只檢查「有沒有呼叫 classify」的測試
    照樣綠)。`content_filter` 在 content 非空時先前會被當完整答案。
    """
    import morning_report as mr
    monkeypatch.setattr(mr.requests, "post",
                        lambda *a, **k: _FakeResp(
                            {"choices": [{"message": {"content": "半截答案"},
                                          "finish_reason": "content_filter"}],
                             "usage": {}}))
    monkeypatch.setattr(mr, "OPENAI_API_KEY", "x")
    try:
        mr._call_openai("prompt", model="gpt-5.6")
    except RuntimeError as e:
        assert "非正常結束" in str(e), e
    else:
        raise AssertionError("被內容政策擋下的回應被當成完整答案接受了")


def test_a_normal_openai_reply_is_still_accepted(monkeypatch):
    """反向:正常結束照舊接受。"""
    import morning_report as mr
    monkeypatch.setattr(mr.requests, "post",
                        lambda *a, **k: _FakeResp(
                            {"choices": [{"message": {"content": "答案"},
                                          "finish_reason": "stop"}],
                             "usage": {}}))
    monkeypatch.setattr(mr, "OPENAI_API_KEY", "x")
    assert mr._call_openai("prompt", model="gpt-5.6") == "答案"


# --------------------------------- 外審第二輪:契約的判定要真的被呼叫端照辦

def _count_posts(monkeypatch, payload):
    """記下實際送出幾次 HTTP —— **重試次數要用送出次數量**,不是用訊息猜。"""
    import morning_report as mr
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _FakeResp(payload)

    monkeypatch.setattr(mr.requests, "post", fake_post)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(mr, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(mr, "_llm_sleep", lambda *_a, **_k: None)
    return calls


def test_a_verdict_is_not_resent_to_the_same_model(monkeypatch):
    """**契約說不值得重試,呼叫端就不要重試。**

    先前 `DeepSeekCompletionError.retryable` 被通用處理器忽略:內容裁決
    每個備援模型各送三次,而那是對**這一份請求**的判定 —— 原樣再送只是
    多付錢,而且每個回應會被記兩次(一次已量測、一次計費但未量測)。
    """
    import morning_report as mr
    calls = _count_posts(monkeypatch, _deepseek_reply("content_filter", "半截"))
    try:
        mr._call_deepseek("prompt")
    except Exception:                                       # noqa: BLE001
        pass
    assert len(calls) == 1, f"裁決類被重送了 {len(calls)} 次"


def test_a_transient_interruption_is_still_retried(monkeypatch):
    """**反向**:資源不足是暫時性的,仍然重試 —— 這條規則不是把重試關掉。"""
    import morning_report as mr
    calls = _count_posts(
        monkeypatch, _deepseek_reply("insufficient_system_resource", "半截"))
    try:
        mr._call_deepseek("prompt")
    except Exception:                                       # noqa: BLE001
        pass
    assert len(calls) > 1, "暫時性中斷被當成裁決,連一次重試都沒有"


def test_a_filtered_empty_reply_keeps_its_reason(monkeypatch):
    """**被內容政策擋下的回應本來就常常沒有 content。**

    先擋「回應無 content」的話,結束原因與 usage 整個從遙測消失,
    而通用處理器會把 provider 的裁決當成暫時性失敗一再重送。
    """
    import morning_report as mr
    calls = _count_posts(
        monkeypatch, {"choices": [{"message": {"content": ""},
                                   "finish_reason": "content_filter"}],
                      "usage": {"completion_tokens": 0}})
    try:
        mr._call_deepseek("prompt")
    except Exception as e:                                  # noqa: BLE001
        assert "content_filter" in str(e), e
    else:
        raise AssertionError("空 content 的裁決被當成成功")
    assert len(calls) == 1, f"空 content 的裁決被重送了 {len(calls)} 次"


def test_a_normal_reply_with_no_content_is_still_an_error(monkeypatch):
    """反向:**正常結束卻沒有 content** 仍然是錯誤(那是另一種失敗)。"""
    import morning_report as mr
    _count_posts(monkeypatch,
                 {"choices": [{"message": {"content": ""},
                               "finish_reason": "stop"}], "usage": {}})
    try:
        mr._call_deepseek("prompt")
    except Exception as e:                                  # noqa: BLE001
        assert "content" in str(e), e
    else:
        raise AssertionError("空 content 被當成成功")


# ------------------------- 外審第三輪:抽取器要照契約重試,不是整個放棄

def _extractor_news(n=3):
    return [{"source_item_id": f"n{i}", "title": f"台積電消息{i}",
             "summary": "", "source": "鉅亨台股",
             "published": "2026-08-18T06:00:00+08:00"} for i in range(n)]


def test_the_extractor_retries_a_transient_interruption(monkeypatch):
    """**暫時性中斷不得讓抽取器整個放棄。**

    先前 `_call_or_halve` 只接截斷,於是 `insufficient_system_resource`
    一路往上 → 抽取器改走確定性路徑。那正是「35 則進去 0 則出來」的形狀,
    只是換了一個原因。
    """
    import morning_report as mr
    calls = []

    def flaky(_prompt):
        calls.append(1)
        if len(calls) == 1:
            raise mr.DeepSeekCompletionError(
                "insufficient_system_resource", cc.RESOURCE_INTERRUPTED, "中斷")
        return '[{"entity":"2330","event_type":"earnings","title":"台積電財報"}]'

    monkeypatch.setattr(mr, "_call_deepseek_extractor", flaky)
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    out = mr.call_llm_event_extractor(_extractor_news(), [])
    assert len(calls) == 2, f"沒有重試(送出 {len(calls)} 次)"
    assert out, "重試成功了卻沒有事件"
    stat = (mr._RUN_MANIFEST.get("llm_extractor") or {})
    assert stat.get("retried") is True, stat
    assert "resource" in str(stat.get("retry_reason") or ""), stat


def test_the_extractor_does_not_retry_a_verdict(monkeypatch):
    """**反向**:內容裁決是對這一份請求的判定 —— 原樣再送只是多付錢。"""
    import morning_report as mr
    calls = []

    def filtered(_prompt):
        calls.append(1)
        raise mr.DeepSeekCompletionError("content_filter", cc.FILTERED, "被擋")

    monkeypatch.setattr(mr, "_call_deepseek_extractor", filtered)
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    mr.call_llm_event_extractor(_extractor_news(), [])
    assert len(calls) == 1, f"裁決類被重送了 {len(calls)} 次"


def test_the_extractor_retry_shares_the_existing_budget(monkeypatch):
    """**成本上限不變**:重試額度與「額度用完就減半」共用同一格。

    反例要能分出勝負:**先中斷、再截斷**。共用額度時第一次重試就用掉它,
    截斷那次直接往上拋(共 2 次);各自有額度的話截斷還會再減半重試一次
    (共 3 次)。只用「連續中斷」量不到 —— 那時第二次的例外根本沒人接,
    次數兩種寫法都是 2(突變驗證抓到)。
    """
    import morning_report as mr
    calls = []

    def flaky(_prompt):
        calls.append(1)
        if len(calls) == 1:
            raise mr.DeepSeekCompletionError(
                "insufficient_system_resource", cc.RESOURCE_INTERRUPTED, "中斷")
        raise mr.ExtractorOutputTruncated("額度用完")

    monkeypatch.setattr(mr, "_call_deepseek_extractor", flaky)
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    mr.call_llm_event_extractor(_extractor_news(), [])
    assert len(calls) == 2, f"重試額度沒有共用(送出 {len(calls)} 次)"


def test_a_repeated_interruption_stops_after_one_retry(monkeypatch):
    """連續中斷只重試一次 —— 重試本身要有上限。"""
    import morning_report as mr
    calls = []

    def always(_prompt):
        calls.append(1)
        raise mr.DeepSeekCompletionError(
            "insufficient_system_resource", cc.RESOURCE_INTERRUPTED, "中斷")

    monkeypatch.setattr(mr, "_call_deepseek_extractor", always)
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    mr.call_llm_event_extractor(_extractor_news(), [])
    assert len(calls) == 2, f"重試沒有上限(送出 {len(calls)} 次)"


def test_the_budget_is_shared_in_both_orders(monkeypatch):
    """**兩種重試共用一格額度,而且順序反過來也一樣。**

    上一條是「先中斷、後截斷」;這一條是「先截斷、後中斷」——
    只測一種順序的話,另一條路徑的扣額度那一行測不出來(突變驗證抓到)。
    """
    import morning_report as mr
    calls = []

    def flaky(_prompt):
        calls.append(1)
        if len(calls) == 1:
            raise mr.ExtractorOutputTruncated("額度用完")
        raise mr.DeepSeekCompletionError(
            "insufficient_system_resource", cc.RESOURCE_INTERRUPTED, "中斷")

    monkeypatch.setattr(mr, "_call_deepseek_extractor", flaky)
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    mr.call_llm_event_extractor(_extractor_news(4), [])
    assert len(calls) == 2, f"重試額度沒有共用(送出 {len(calls)} 次)"


def test_every_sent_request_is_recorded_even_when_the_budget_runs_out(monkeypatch):
    """**記帳的條件是「這次請求送出去了」,不是「我們還想不想重試」。**

    額度用完時原本直接 `raise`,於是第二次送出去的請求完全沒有紀錄 ——
    manifest 顯示 1 次、實際送了 2 次,成本與 provider 健康度都少算一次。
    """
    import morning_report as mr
    mr._RUN_MANIFEST.setdefault("llm", {})["attempts"] = []
    calls = []

    def always(_prompt):
        calls.append(1)
        raise mr.DeepSeekCompletionError(
            "insufficient_system_resource", cc.RESOURCE_INTERRUPTED, "中斷")

    monkeypatch.setattr(mr, "_call_deepseek_extractor", always)
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    mr.call_llm_event_extractor(_extractor_news(), [])
    attempts = [a for a in (mr._RUN_MANIFEST["llm"].get("attempts") or [])
                if a.get("role") == "extractor"]
    assert len(calls) == 2, calls
    assert len(attempts) == len(calls), (
        f"送出 {len(calls)} 次卻只記了 {len(attempts)} 次")


def test_the_skipped_retry_label_says_what_actually_used_the_budget(
        monkeypatch, capsys):
    """**標籤要說實話。** 額度現在有兩種用途(減量、provider 中斷),
    寫死「用於減量」在中斷那一種情況下是假的 —— 而這一格存在的理由
    就是「為什麼沒重試」不要靠猜。
    """
    import morning_report as mr
    calls = []

    def flaky(_prompt):
        calls.append(1)
        if len(calls) == 1:
            raise mr.DeepSeekCompletionError(
                "insufficient_system_resource", cc.RESOURCE_INTERRUPTED, "中斷")
        # 第二次:解析得出東西但全數不合格 → 會想再重試一次,但額度沒了
        return '[{"entity":"","event_type":"不是合法型別","title":""}]'

    monkeypatch.setattr(mr, "_call_deepseek_extractor", flaky)
    monkeypatch.setattr(mr, "_extractor_provider", lambda: "deepseek")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("LLM_EVENT_EXTRACTION", "1")
    mr.call_llm_event_extractor(_extractor_news(), [])
    label = str((mr._RUN_MANIFEST.get("llm_extractor") or {}).get(
        "schema_retry_skipped") or "")
    assert label, "沒有記下為什麼不重試"
    assert "truncation" not in label, f"額度用在中斷上,標籤卻說減量:{label}"
    assert "resource" in label, label
    # **日誌與 manifest 要說同一件事**:只驗 manifest 的話,stderr 那行
    # 寫死「用於減量」的矛盾診斷留在原地(外審第五輪抓到)。
    log = capsys.readouterr().err
    assert "重試預算已用於" in log, log[-400:]
    assert "減量" not in log.split("重試預算已用於")[1][:20], log[-400:]
