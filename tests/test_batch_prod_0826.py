# -*- coding: utf-8 -*-
"""2026-08-26 生產:DeepSeek 402 Insufficient Balance → emergency_fallback。

**帳戶沒錢不是程式缺陷**,但信裡寫的處置是錯的:「稍後手動重跑 workflow
(DeepSeek 的暫時性故障多在數小時內恢復)」—— 402 重跑幾次都一樣,使用者
照著做只是浪費一個早上。而且重試迴圈自己也對著 402 空轉了兩次。

`llm_telemetry.refusal_reason` 的 docstring 本來就寫著「重試幾次都一樣」,
分類器一直都在,只是沒有人拿它來決定重試、也沒有人拿它來寫那段字。
"""
import pytest

import llm_telemetry as lt
import morning_report as mr


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _HTTPErr(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.response = _Resp(code)


def test_the_advice_matches_the_actual_cause():
    """訊息要能分開**處置不同**的原因。"""
    def _tip(code, msg):
        t = mr._fallback_analysis_text([], _HTTPErr(code, msg))
        return t[t.index("## 二、提示"):]

    pay = _tip(402, 'HTTP 402: {"message":"Insufficient Balance"}')
    assert "重跑不會好" in pay and "儲值" in pay, pay
    assert "暫時性故障多在數小時內恢復" not in pay, pay

    auth = _tip(401, "HTTP 401: invalid key")
    assert "重跑不會好" in auth and "DEEPSEEK_API_KEY" in auth, auth
    assert "儲值" not in auth, ("餘額與金鑰是兩個處置", auth)

    # 分不出原因時保留原本的通用建議(不確定就不要亂指方向)
    other = _tip(500, "HTTP 500: upstream oops")
    assert "暫時性故障多在數小時內恢復" in other, other
    assert "重跑不會好" not in other, other


def test_a_refused_request_is_not_retried(monkeypatch):
    """空轉的代價不是白花錢(被拒不計費),是**把有限的時間預算燒掉** ——
    每次還 sleep,而整班有 deadline。08/26 的 manifest 有四次 primary 嘗試,
    後兩次都是 402。

    **行為測試,不是原始碼比對**:第一版把守衛放進通用的 `except Exception`,
    而 `raise_for_status()` 拋的是 `requests.exceptions.HTTPError`,那條路
    先被上面專屬的 handler 接走 —— 守衛在一段 402 永遠走不到的程式碼裡,
    而只比對原始碼的測試照樣綠。餘額是**帳號級**的,換模型再送一次結果
    一模一樣,所以要連 fallback model 都不試。
    """
    import requests

    calls = []

    class _Resp402:
        status_code = 402
        text = '{"error":{"message":"Insufficient Balance"}}'

        def raise_for_status(self):
            err = requests.exceptions.HTTPError("402 Payment Required")
            err.response = self
            raise err

    def _post(*a, **k):
        calls.append(k.get("json") or {})
        return _Resp402()

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(mr, "_llm_sleep",
                        lambda *a, **k: calls.append("SLEPT"))
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "test-key")
    # **要有第二個模型才量得到「不換模型」**:`fallback_models` 是
    # `[DEEPSEEK_MODEL] + ("deepseek-v4-flash",)` 去重 —— 預設值剛好等於
    # 那個 fallback,清單只剩一個,於是「break 換下一個模型」與「raise」
    # 在測試裡長得一樣(第一版因此對著兩個突變都綠)。
    monkeypatch.setattr(mr, "DEEPSEEK_MODEL", "deepseek-v4-pro")
    with pytest.raises(RuntimeError) as ei:
        mr._call_deepseek("你好", role="primary")
    assert "402" in str(ei.value) or "Insufficient" in str(ei.value), ei.value
    # **一次就停**:不重試、不換 fallback model、不 sleep
    assert len(calls) == 1, calls
    assert "SLEPT" not in calls, calls


def test_a_transient_failure_is_still_retried(monkeypatch):
    """反向:別把「不重試」變成對所有失敗都成立(那會讓暫時性故障不再重試)。"""
    import requests

    n = {"posts": 0, "slept": 0}

    class _Resp503:
        status_code = 503
        text = "upstream busy"

        def raise_for_status(self):
            err = requests.exceptions.HTTPError("503")
            err.response = self
            raise err

    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: (n.__setitem__("posts", n["posts"] + 1)
                                         or _Resp503()))
    monkeypatch.setattr(mr, "_llm_sleep",
                        lambda *a, **k: n.__setitem__("slept", n["slept"] + 1))
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "test-key")
    with pytest.raises(RuntimeError):
        mr._call_deepseek("你好", role="primary")
    assert n["posts"] > 1 and n["slept"] > 0, n


def test_the_refusal_classifier_still_separates_the_two_causes():
    assert lt.refusal_reason(_HTTPErr(402, "x")) == "payment"
    assert lt.refusal_reason(_HTTPErr(401, "x")) == "auth"
    assert lt.refusal_reason(_HTTPErr(500, "x")) == ""
    # 純文字也認得(有些路徑只拿得到訊息字串)
    assert lt.refusal_reason("HTTP 402: Insufficient Balance") == "payment"


def test_the_fallback_still_carries_the_raw_news():
    """降級信的核心價值是「原始新聞清單給你自己判讀」—— 換掉提示那段
    不可以把它一起換掉。"""
    t = mr._fallback_analysis_text(
        [{"source": "CNBC", "title": "Oil drops more than 3%"}],
        _HTTPErr(402, "HTTP 402: Insufficient Balance"))
    assert "原始新聞清單" in t and "Oil drops more than 3%" in t
    assert "LLM 服務暫時不可用" in t
