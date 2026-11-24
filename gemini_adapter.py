"""Gemini 備援的型號差異:思考設定、用量欄位與官方牌價(2026-11-24)。

`gemini-2.5-flash` 最早 2026-12-01 停用、之後自動轉 3.5-flash-lite,備援鏈因此換新。3.x 用 `thinkingLevel`
而不是 `thinkingBudget`,而且思考無法完全關閉;`maxOutputTokens` 仍是思考 + 答案的總額。
出處:aistudio.google.com/docs 的 thinking、models、pricing 頁(2026-11-24 查)。
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

PRICING_SOURCE = "aistudio.google.com/docs/pricing 2026-11-24"
#: 每 100 萬 token 美元;output 含思考。3.8-flash 2027-01-01 起漲一倍(官方未註明時區,以 UTC 計)。
_PRICES = {
    "gemini-3.8-flash": {"input": 0.75, "cached_input": 0.075, "output": 3.75},
    "gemini-3.5-flash-lite": {"input": 0.30, "cached_input": 0.03, "output": 2.50},
}
_STEP = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)
_STEP_PRICES = {"gemini-3.8-flash": {"input": 1.50, "cached_input": 0.15, "output": 7.50}}
#: 抽取是抄錄不是推理:用該型號允許的最低思考。主分析用型號預設。
_EXTRACTOR_LEVEL = {"gemini-3.8-flash": "low", "gemini-3.5-flash-lite": "minimal"}


def price_at(model: str, at=None) -> Optional[dict]:
    m = (model or "").strip().lower()
    after = isinstance(at, dt.datetime) and at.tzinfo is not None and at >= _STEP
    return (_STEP_PRICES.get(m) if after else None) or _PRICES.get(m)


def generation_config(model: str, role: str, max_tokens: int) -> dict:
    config: dict = {"maxOutputTokens": max_tokens}
    if model.startswith("gemini-2."):
        config["temperature"] = 0.3
        if role == "extractor":
            config["thinkingConfig"] = {"thinkingBudget": 0}
    elif role == "extractor" and model in _EXTRACTOR_LEVEL:
        config["thinkingConfig"] = {"thinkingLevel": _EXTRACTOR_LEVEL[model]}
    return config


def usage(data) -> Optional[dict]:
    """`usageMetadata` → 共用的用量形狀。輸出以「總數 − 輸入」計,思考一定算進去(官方以 output 計價)。"""
    meta = data.get("usageMetadata") if isinstance(data, dict) else None
    if not isinstance(meta, dict):
        return None

    def count(key):
        value = meta.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    prompt, total = count("promptTokenCount"), count("totalTokenCount")
    answer, thoughts = count("candidatesTokenCount"), count("thoughtsTokenCount") or 0
    if prompt is None:
        return None
    if total is not None and total >= prompt:
        out = total - prompt
    elif answer is not None:
        out = answer + thoughts
    else:
        return None
    return {"prompt_tokens": prompt, "completion_tokens": out, "total_tokens": prompt + out,
            "completion_tokens_details": {"reasoning_tokens": thoughts},
            "prompt_tokens_details": {"cached_tokens": count("cachedContentTokenCount") or 0}}
