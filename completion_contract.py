# -*- coding: utf-8 -*-
"""**一份 completion contract,三家 provider 共用**(repo-wide 外審 P1-2)。

先前三家各有一套政策,而且是各自演化出來的:

    Gemini    只有 `STOP` 算成功(2026-08-17 補上)
    DeepSeek  只擋 `length`
    OpenAI    只擋 `length`

於是 DeepSeek 的 `insufficient_system_resource`(官方文件:**因推理系統
資源不足而生成被中斷**)在 `content` 非空時被當成完整答案。那與先前
Gemini 那個缺陷是**同一種形狀**:被中斷的回應剛好仍是合法 JSON
(30 件事件只吐出前 5 件),解析器沒有理由知道它少了後半段,
manifest 顯示這個能力健康、信裡少掉的部分沒有任何警訊。

三套政策一定會再漂。這裡是唯一的一份:

  * **adapter 只做對照**(provider 的字串 → 這裡的結果),不自己發明政策;
  * **只有 `NORMAL` 算成功**;
  * 重試與否由結果決定,不由呼叫端各自解讀字串。

## 為什麼「缺欄位」也算異常

三家的非串流端點依契約都會給結束原因。**拒錯的代價是這一棒降級**
(外層有模型降級鏈、抽取器還有確定性路徑),**接錯的代價是一份悄悄
少掉內容的報告** —— 兩者不對稱,所以 fail closed。
"""
from __future__ import annotations

#: 正常結束 —— **只有這一個算成功**。
NORMAL = "normal"
#: 被額度截斷。重送同一份請求沒有意義(會再截一次);要改請求本身。
TRUNCATED = "truncated"
#: 被 provider 的內容政策擋下。那是對**這一份請求**的裁決。
FILTERED = "filtered"
#: provider 端資源不足而中斷生成 —— 暫時性,換個時間/模型有意義。
RESOURCE_INTERRUPTED = "resource_interrupted"
#: provider 要求呼叫工具。本報三個端點都沒有送 tools,收到就是契約外的事。
TOOL_REQUEST = "tool_request"
#: 不認得的原因、或欄位缺席。**fail closed。**
UNKNOWN = "unknown"

#: `provider → {結束原因(大寫): 結果}`。
#: 來源:各家官方文件(DeepSeek chat completion、Gemini generateContent、
#: OpenAI chat completions),2026-08-18 查證。
_MAP: dict = {
    "deepseek": {
        "STOP": NORMAL,
        "LENGTH": TRUNCATED,
        "CONTENT_FILTER": FILTERED,
        "TOOL_CALLS": TOOL_REQUEST,
        "INSUFFICIENT_SYSTEM_RESOURCE": RESOURCE_INTERRUPTED,
    },
    "openai": {
        "STOP": NORMAL,
        "LENGTH": TRUNCATED,
        "CONTENT_FILTER": FILTERED,
        "TOOL_CALLS": TOOL_REQUEST,
        "FUNCTION_CALL": TOOL_REQUEST,
    },
    "gemini": {
        "STOP": NORMAL,
        "MAX_TOKENS": TRUNCATED,
        "SAFETY": FILTERED,
        "RECITATION": FILTERED,
        "BLOCKLIST": FILTERED,
        "PROHIBITED_CONTENT": FILTERED,
        "SPII": FILTERED,
        "LANGUAGE": FILTERED,
        "IMAGE_SAFETY": FILTERED,
        "MALFORMED_FUNCTION_CALL": TOOL_REQUEST,
        # `OTHER` 是 Gemini 明列的「其他」—— 它**不是**已知的裁決,
        # 所以照 UNKNOWN 處理(有限重試),不併進 FILTERED。
    },
}

#: 重送**同一個模型、同一份請求**有沒有意義。
#: 截斷與裁決都是對這一份請求的判定,原樣再送只是多付一次錢;
#: 資源不足與不明原因可能是暫時的,給有限重試。
_RETRYABLE: dict = {
    NORMAL: False,
    TRUNCATED: False,
    FILTERED: False,
    TOOL_REQUEST: False,
    RESOURCE_INTERRUPTED: True,
    UNKNOWN: True,
}


def classify(provider: str, reason) -> str:
    """`(provider, 結束原因)` → 結果。**不認得或缺席一律 `UNKNOWN`。**"""
    table = _MAP.get(str(provider or "").strip().lower()) or {}
    return table.get(str(reason or "").strip().upper(), UNKNOWN)


def is_normal(provider: str, reason) -> bool:
    return classify(provider, reason) == NORMAL


def retryable(outcome: str) -> bool:
    """這個結果值不值得在**同一個模型**上再送一次。"""
    return bool(_RETRYABLE.get(str(outcome or ""), True))


def verdict_reasons(provider: str) -> frozenset:
    """這家 provider 裡**不值得同模型重試**的那些原因(給既有降級鏈用)。

    先前 Gemini 自己維護一份 `_GEMINI_VERDICT_REASONS` —— 那是第二份政策,
    而兩份一定會漂。這裡由對照表推導,加一個原因只要改 `_MAP` 一處。
    """
    table = _MAP.get(str(provider or "").strip().lower()) or {}
    return frozenset(r for r, outcome in table.items()
                     if outcome != NORMAL and not retryable(outcome))


def describe(outcome: str) -> str:
    """給人看的一句話(進錯誤訊息與 manifest)。"""
    return {
        NORMAL: "正常結束",
        TRUNCATED: "被額度截斷",
        FILTERED: "被 provider 的內容政策擋下",
        RESOURCE_INTERRUPTED: "provider 資源不足而中斷生成",
        TOOL_REQUEST: "provider 要求呼叫工具(本端點沒有送 tools)",
        UNKNOWN: "不認得的結束原因或欄位缺席",
    }.get(str(outcome or ""), "不認得的結束原因或欄位缺席")
