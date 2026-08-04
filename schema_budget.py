# -*- coding: utf-8 -*-
"""**這份 schema 送得出去嗎**(第十九輪 P2-4)。

`analysis_schema` 決定「契約長什麼樣」;這裡回答一個完全不同的問題:
**provider 的 strict Structured Outputs 會不會直接拒收它。**

分開的理由是失效方式不同 —— 契約寫錯了測試會紅;預算超了**測試全綠、
真實 API 拒絕整個請求**,而那一天信件會整封落回 legacy。
深度目前已經貼齊上限 10/10,再包一層 metadata 就會炸,所以這幾個數字
需要一個跑得起來的關卡,不是文件裡的提醒。
"""
from __future__ import annotations

import json

import analysis_schema as _sch

#: **provider 的 strict Structured Outputs 硬限制。** 超過任何一項,
#: 真實 API 會直接拒絕整個請求 —— 而單元測試全綠。
#: 深度目前已經**貼齊上限**:再包一層 metadata 就會炸,所以這幾個數字
#: 需要一個跑得起來的關卡,而不是寫在文件裡的提醒。
STRICT_MAX_DEPTH = 10
STRICT_MAX_PROPERTIES = 5000
STRICT_MAX_CHARS = 120_000


def strict_budget(schema=None) -> dict:
    """量目前的 schema 用掉多少預算。**量,不是推理。**"""
    obj = _sch.ANALYSIS_OUTPUT_SCHEMA if schema is None else schema

    def _depth(o, d=0):
        if isinstance(o, dict):
            return max([_depth(v, d + 1) for v in o.values()] or [d])
        if isinstance(o, list):
            return max([_depth(v, d + 1) for v in o] or [d])
        return d

    def _props(o):
        n = 0
        if isinstance(o, dict):
            n += len(o.get("properties") or {})
            for v in o.values():
                n += _props(v)
        elif isinstance(o, list):
            for v in o:
                n += _props(v)
        return n
    return {"depth": _depth(obj), "properties": _props(obj),
            "chars": len(json.dumps(obj, ensure_ascii=False)),
            "max_depth": STRICT_MAX_DEPTH,
            "max_properties": STRICT_MAX_PROPERTIES,
            "max_chars": STRICT_MAX_CHARS}


def strict_budget_problems(schema=None) -> list:
    """超出預算的項目。空 = 這份 schema 送得出去。"""
    b = strict_budget(schema)
    out = []
    for key in ("depth", "properties", "chars"):
        if b[key] > b[f"max_{key}"]:
            out.append(f"{key} {b[key]} 超過 strict 上限 {b[f'max_{key}']}"
                       " —— 新欄位要攤平,不能再包一層")
    return out
