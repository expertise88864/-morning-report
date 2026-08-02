# -*- coding: utf-8 -*-
"""**本地的 strict JSON Schema 檢查**(第十三輪 P2-3/P2-4)。

## 為什麼需要它

`ANALYSIS_OUTPUT_SCHEMA` 是送給 OpenAI 的 strict schema:所有欄位必填、
`additionalProperties: False`。但驗證只發生在**遠端** —— 本地完全沒有東西
會告訴你「這個物件 API 根本不會接受」。後果實測到兩個:

  * 測試裡叫 `_GOOD` 的 fixture(拿來驗整條生產路徑)其實**不合法**:
    `top_news_analysis` 少三個必填欄位、`claim_audit` 少兩個,而且帶了一個
    schema 裡沒有的 `claim_id`。也就是說那些測試驗的是一種真實 API 永遠
    不會產出的形狀 —— **測試要用生產的形狀,而這裡連形狀都不對。**
  * 金絲雀的 strict 探測最後只做 `json.loads()`:模型回 `{"hello":"world"}`
    也算通過。那個探測存在的理由就是驗 structured output,而它只驗了
    「是不是 JSON」。

## 範圍

只實作這份 schema 真的用到的關鍵字:`type` / `properties` / `required` /
`additionalProperties` / `items` / `enum`。**沒實作的關鍵字要明講**
(見 `UNSUPPORTED`),否則「沒檢查」會被誤讀成「檢查過了」——
那是本 repo 最常見的失效形狀。
"""
from __future__ import annotations

#: 這份 schema 用得到、而本模組**沒有**實作的關鍵字。出現就拋 ——
#: 靜默略過會讓人以為驗過了。
UNSUPPORTED = ("allOf", "anyOf", "oneOf", "not", "$ref", "patternProperties")

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
          "integer": int, "number": (int, float), "null": type(None)}


def _type_ok(value, want: str) -> bool:
    py = _TYPES.get(want)
    if py is None:
        return True
    if want == "integer" and isinstance(value, bool):
        return False          # Python 的 bool 是 int 的子類
    if want in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, py)


def violations(obj, schema: dict, path: str = "") -> list:
    """回傳這個物件**不符合 schema 的地方**(空 = 合法)。

    刻意回清單而不是拋例外:呼叫端(測試、金絲雀)要能一次看到全部,
    而不是修一個才看到下一個。
    """
    out: list = []
    if not isinstance(schema, dict):
        return out
    bad = [k for k in UNSUPPORTED if k in schema]
    if bad:
        raise NotImplementedError(
            f"{path or '(root)'} 用到本模組沒實作的 schema 關鍵字:{bad} —— "
            "先實作再用,不要讓它靜默通過")

    want = schema.get("type")
    if isinstance(want, str) and not _type_ok(obj, want):
        return [f"{path or '(root)'}:型別應為 {want},實際 "
                f"{type(obj).__name__}"]

    if "enum" in schema and obj not in schema["enum"]:
        out.append(f"{path or '(root)'}:{obj!r} 不在 enum {schema['enum']} 裡")

    if want == "object" or (want is None and isinstance(obj, dict)):
        if not isinstance(obj, dict):
            return out
        props = schema.get("properties") or {}
        for key in (schema.get("required") or []):
            if key not in obj:
                out.append(f"{path}.{key}".lstrip(".") + ":必填欄位缺少")
        if schema.get("additionalProperties") is False:
            for key in obj:
                if key not in props:
                    out.append(f"{path}.{key}".lstrip(".")
                               + ":schema 沒有這個欄位(additionalProperties=False)")
        for key, sub in props.items():
            if key in obj:
                out.extend(violations(obj[key], sub, f"{path}.{key}".lstrip(".")))

    if want == "array" or (want is None and isinstance(obj, list)):
        items = schema.get("items")
        if isinstance(items, dict) and isinstance(obj, list):
            for i, v in enumerate(obj):
                out.extend(violations(v, items, f"{path}[{i}]"))
    return out


def is_valid(obj, schema: dict) -> bool:
    """合不合法。要看**為什麼**不合法請用 `violations()`。"""
    return not violations(obj, schema)
