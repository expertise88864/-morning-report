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
`additionalProperties` / `items` / `enum` / `minimum` / `maximum`。
**沒實作的關鍵字要明講**(見 `UNSUPPORTED`),否則「沒檢查」會被誤讀成
「檢查過了」—— 那是本 repo 最常見的失效形狀。

r1(Codex,#2):第一版漏了 `minimum`/`maximum`,**而且沒把它們列進
`UNSUPPORTED`** —— 也就是說我在這個模組開頭寫下的規則,被這個模組自己
違反了。後果不是抽象的:`stance.score=999`、`confidence=2` 會被判成合法,
於是新加的「fixture 自己要合法」守衛會替一份 API 必然拒絕的物件背書。
"""
from __future__ import annotations

import math as _math

#: 本模組**真的會檢查**的關鍵字。`description` 只是說明,不影響合法性。
IMPLEMENTED = frozenset({
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "minimum", "maximum", "description"})


def unsupported_keywords(schema, path: str = "") -> list:
    """這份 schema 用到、而本模組沒實作的關鍵字(含位置)。

    r1(Codex,#2)之後補的:原本靠一張手寫的 `UNSUPPORTED` 黑名單,
    而我漏了 `minimum`/`maximum` —— 黑名單沒列到的東西就靜默通過,
    **守衛自己決定要掃多大,那個範圍一定會漏。**
    改成從 schema 反推:白名單以外的關鍵字一律點名,新關鍵字進 schema
    的當下就會被指出來,不必有人記得更新黑名單。
    """
    out: list = []
    if isinstance(schema, dict):
        # r1(Codex,pass 2):**不得先問「這看起來像不像 schema」。**
        # 我加那個閘門是為了避免誤判非 schema 的 dict,結果只有
        # `{"anyOf": [...]}` 的節點(沒有 `type`)整個檢查被跳過 ——
        # 而那正是最該擋的形狀。**舊的黑名單反而擋得住它。**
        # 傳進來的每一個 dict 都已經是 schema 節點,沒有需要猜的餘地。
        out += [f"{path or '(root)'}:{k}"
                for k in schema if k not in IMPLEMENTED]
        for k, v in schema.items():
            if k == "properties" and isinstance(v, dict):
                for name, sub in v.items():
                    out += unsupported_keywords(sub, f"{path}.{name}".lstrip("."))
            elif k == "items":
                out += unsupported_keywords(v, f"{path}[]")
    return out

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
          "integer": int, "number": (int, float), "null": type(None)}


def is_json_number(value) -> bool:
    """這個值是**合法的 JSON 數字**嗎。

    Python 的數字模型與 JSON 的不一樣,而差異每一條都咬過人:
      * `bool` 是 `int` 的子類 —— `True` 不是 JSON number(既有判準)
      * `NaN` / `inf` / `-inf` **是 Python float,但 JSON 沒有這些值**
        (r19 外審 P2:`json.dumps` 會產出 `NaN` 字面值,那是別的解析器
        讀不懂的東西;而 `NaN` 的比較運算全為 False,所以連
        `minimum`/`maximum` 都繞得過去 —— 型別與範圍兩關全過)。

    這支是**唯一的一份**:`evidence_serialize.normalize_json()` 也用它,
    免得兩邊各自重新發明一次 JSON 的數字語意。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or _math.isfinite(value)


def _type_ok(value, want: str) -> bool:
    py = _TYPES.get(want)
    if py is None:
        return True
    if want in ("number", "integer"):
        # bool 與非有限值都不是合法的 JSON 數字(判準只有 `is_json_number`)
        if not is_json_number(value):
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
    bad = [k for k in schema if k not in IMPLEMENTED]
    if bad:
        raise NotImplementedError(
            f"{path or '(root)'} 用到本模組沒實作的 schema 關鍵字:{bad} —— "
            "先實作再用,不要讓它靜默通過")

    want = schema.get("type")
    if isinstance(want, str) and not _type_ok(obj, want):
        # **處置不同的原因要分得開**:`NaN` 的型別「是」float,說它
        # 「型別應為 number,實際 float」會讓人以為判準壞了 ——
        # 真正的原因是 JSON 沒有這個值。
        if (want in ("number", "integer") and isinstance(obj, float)
                and not _math.isfinite(obj)):
            return [f"{path or '(root)'}:{obj!r} 不是合法的 JSON 數字"
                    "(JSON 沒有 NaN / Infinity)"]
        return [f"{path or '(root)'}:型別應為 {want},實際 "
                f"{type(obj).__name__}"]

    if "enum" in schema and obj not in schema["enum"]:
        out.append(f"{path or '(root)'}:{obj!r} 不在 enum {schema['enum']} 裡")

    # 數值範圍。**`bool` 不算數值** —— Python 的 True 是 1,不擋的話
    # `confidence: True` 會通過 `0 <= x <= 1`。
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if isinstance(lo, (int, float)) and obj < lo:
            out.append(f"{path or '(root)'}:{obj} 小於下限 {lo}")
        if isinstance(hi, (int, float)) and obj > hi:
            out.append(f"{path or '(root)'}:{obj} 大於上限 {hi}")

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
