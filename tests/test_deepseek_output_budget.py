# -*- coding: utf-8 -*-
"""**DeepSeek 的輸出額度必須隨推理強度放大**(2026-08-02 生產缺陷)。

## 那天發生什麼

週日綜合信的「重大政策深度解析」寫到一半就斷:第一個政策只有一段沒有分段的
文字,第二個政策只剩標題。manifest 的數字說明了一切:

    requested_effort = max
    completion_tokens = 7000      ← 剛好等於送出去的 max_tokens
    reasoning_tokens  = 6757      ← 其中 6,757 拿去推理

**答案只剩 243 個 token。**

## 根因

`max_tokens` 在 DeepSeek 是 **reasoning + 答案的總額**(本 repo 的抽取器路徑
早就寫下這句話),而 `_call_deepseek` 送的是**寫死的 7,000**,
完全不隨推理強度放大 —— OpenAI 那條路徑用 `output_cap`,DeepSeek 這條沒有。
批#118 把預設從 `high` 改成 `max` 之後,推理量暴增,答案就被擠掉了。

這是 `CAP_MULTIPLIER` 存在的理由,而它只被套用在一半的 provider 上。

## 這個檔盯什麼

  1. 額度真的隨強度放大(而且兩個 provider 用**同一條規則**)
  2. 截斷會留下訊號(`finish_reason=length` 進 manifest 與降級清單)

第 2 點與第 1 點同等重要:那天真正糟的不是「被截斷」,而是
**被截斷而沒有任何人知道** —— 唯一的線索是 completion_tokens 剛好等於
max_tokens 這個要人自己去比對的巧合。
"""
import ast
from pathlib import Path

import llm_telemetry as lt

_SRC = Path(__file__).resolve().parents[1] / "morning_report.py"


def test_the_deepseek_budget_scales_with_reasoning_effort():
    """`max` 的額度必須明顯大於 `high`,否則推理會把答案擠掉。"""
    base = 7000
    high = lt.output_cap("high", base, model="deepseek-v4-pro")
    mx = lt.output_cap("max", base, model="deepseek-v4-pro")
    assert mx > high > base, f"額度沒有隨強度放大:high={high} max={mx}"
    # 那天的實測:推理吃掉 6,757。額度要留得下推理**再加上**一份完整答案。
    assert mx - 6757 >= base, (
        f"max 的額度 {mx} 扣掉實測的 6,757 推理之後,剩不下一份完整答案")


def test_the_documented_output_limit_has_a_source():
    """v4-pro 的上限不是猜的 —— 這張表的規則是「說得出數字從哪來」。

    先前刻意留空(沒有出處就不填),而留空的代價在 2026-08-02 具體化:
    `max_output_for` 回保守的 16,000,而真正被送出去的是寫死的 7,000。
    """
    cap, source = lt.max_output_for("deepseek-v4-pro")
    assert cap == 384_000, f"v4-pro 的輸出上限變了:{cap}"
    assert "MODEL_LIMITS" in source, f"上限沒有出處:{source}"


def _fn(name: str):
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node) -> set:
    """這個函式**真的呼叫**了哪些名字(含 `x.y()` 的 y)。

    刻意用 AST 而不是子字串。第一版寫 `"output_cap" in 原始碼`,
    而**我自己的註解裡就有那個字串** —— 於是把程式碼改回寫死的常數,
    測試照樣綠(突變驗證當場抓到)。
    **會出現在散文裡的字串,不能拿來當守衛的判準。**
    """
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _names(node) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_the_deepseek_call_uses_the_shared_budget_rule():
    """**兩個 provider 用同一條規則。**

    寫死的常數不會有錯誤訊息,只會在某人調高推理強度的那天把答案擠掉 ——
    而那正是 2026-08-02 發生的事。
    """
    fn = _fn("_call_deepseek")
    assert "output_cap" in _calls(fn), (
        "_call_deepseek 沒有**呼叫** output_cap —— 額度又變回寫死的常數,"
        "調高推理強度時答案會被推理擠掉")
    assert "DEEPSEEK_REASONING_EFFORT" in _names(fn), \
        "額度沒有吃推理強度,那就不是「隨強度放大」"


def test_truncation_leaves_a_signal():
    """`finish_reason=length` 必須進 manifest 與降級清單。

    那天真正糟的不是「被截斷」,而是**被截斷而沒有任何人知道**:
    manifest 的 finish_reason 是 None,唯一的線索是 completion_tokens
    剛好等於 max_tokens 這個要人自己去比對的巧合。
    """
    fn = _fn("_call_deepseek")
    text = ast.get_source_segment(_SRC.read_text(encoding="utf-8"), fn) or ""
    assert "llm:truncated" in text, "截斷沒有進降級清單"
    # `finish_reason` 要真的被當成**關鍵字參數**交給記錄器 ——
    # 只出現在註解裡不算(那正是上一條栽在的地方)。
    rec = [c for c in ast.walk(fn)
           if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
           and c.func.id == "_record_llm_call"]
    assert rec, "_call_deepseek 沒有呼叫 _record_llm_call"
    assert any(kw.arg == "finish_reason" for kw in rec[0].keywords), \
        "finish_reason 沒有被交給記錄器,manifest 仍然看不到截斷"


def test_the_openai_path_still_uses_the_same_rule():
    """反向:別為了修 DeepSeek 而把 OpenAI 那條改壞。"""
    assert "output_cap" in _calls(_fn("_call_openai"))


# ---------------------------------------------------------------- 行為驗證

def _fake_deepseek(monkeypatch, finish_reason, content="半截的政策解析"):
    """樁掉 HTTP,讓其餘全部走真實程式碼。"""
    import morning_report as mr

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": content},
                                 "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": 5000, "completion_tokens": 7000,
                              "completion_tokens_details": {"reasoning_tokens": 6757}}}

    monkeypatch.setattr(mr.requests, "post", lambda *a, **k: _R())
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(mr, "DEEPSEEK_REASONING_EFFORT", "max")
    return mr


def test_a_truncated_response_is_rejected_not_returned(monkeypatch):
    """r1(Codex):**截斷要被拒絕,不只是被看見。**

    我第一版只記了訊號就照樣回傳半截內容 —— 而週日那條路徑
    (`analyze_weekend_policy`)沒有完整性檢查,於是同樣的半截政策段
    還是會寄出去,整個修復白做。

    另外兩條路徑早就是這樣做的(`_call_openai`、`_call_deepseek_extractor`),
    而 `record_llm_call` 的契約也明說 accepted=True 代表
    「content 非空**且** finish 不是 length」。
    """
    import pytest as _pytest

    mr = _fake_deepseek(monkeypatch, "length")
    mr._RUN_MANIFEST.pop("llm", None)
    saved = list(mr._DEGRADED_STEPS)
    try:
        mr._DEGRADED_STEPS.clear()
        with _pytest.raises(mr.ExtractorOutputTruncated):
            mr._call_deepseek("prompt")
        assert any(s.startswith("llm:truncated") for s in mr._DEGRADED_STEPS), \
            f"截斷沒有進降級清單:{mr._DEGRADED_STEPS}"
        attempts = (mr._RUN_MANIFEST.get("llm") or {}).get("attempts") or []
        assert attempts, "截斷的那次呼叫沒有入帳"
        assert "length" in str(attempts[-1].get("error") or ""), attempts[-1]
        assert (mr._RUN_MANIFEST.get("llm") or {}).get("primary") is None, \
            "截斷的回應被記成 accepted —— 那會讓它看起來是這封信的作者"
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_a_complete_response_is_still_returned(monkeypatch):
    """反向:別為了擋截斷而把正常回應也擋掉。"""
    mr = _fake_deepseek(monkeypatch, "stop", content="完整的政策解析")
    assert mr._call_deepseek("prompt") == "完整的政策解析"


def test_truncation_does_not_burn_three_retries(monkeypatch):
    """同樣的參數必然再截斷一次,而每次都是滿額推理的計費呼叫。"""
    import pytest as _pytest

    calls = []
    mr = _fake_deepseek(monkeypatch, "length")
    real_post = mr.requests.post
    monkeypatch.setattr(mr.requests, "post",
                        lambda *a, **k: (calls.append(1), real_post(*a, **k))[1])
    with _pytest.raises(mr.ExtractorOutputTruncated):
        mr._call_deepseek("prompt")
    assert len(calls) == 1, f"截斷之後又重試了 {len(calls) - 1} 次"
