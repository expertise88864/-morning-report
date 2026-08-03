# -*- coding: utf-8 -*-
"""**分側的成本與延遲**(第十四輪 P1-4)。

## 為什麼非做不可

`run_manifest.json` 每天被下一班覆蓋。2026-08-03 那天要回答「Luna 花了多少」
時,答案還在 manifest 裡(Luna $0.0669、DeepSeek $0.0479);**隔天早上
那份就沒了**。而實驗帳本裡只有整班總和 `run_cost_total_usd` ——
十配對達標時能比的只有「這一班總共多少錢」,比不出兩套系統各自的成本效益,
而那正是「要不要永久換成 Luna」的核心問題。

實測到的教訓不是抽象的:當天那個「Luna 比 DeepSeek 貴 40%」的結論,
如果晚一天問就查不到了。**能事後重建的東西才叫記錄下來。**

## 抽取器算誰的

**算共用,不分攤。** 抽取器在 EvidencePacket 組裝**之前**跑一次,兩側吃的是
同一份產物 —— 它不屬於任何一側。按比例拆給兩邊是編造(拆法本身就是結論),
所以這裡只標 `attribution="shared"`,由讀的人決定要不要納入。
`side_costs()` 因此把它跟兩側分開回報,而不是加進去。

## 延遲為什麼不給 p95

十個配對算不出 p95 —— 那個數字會完全由單一極端值決定,而它看起來像統計量。
給的是 `median` / `max` / `n`,三個都誠實,而且 `n` 讓人自己判斷夠不夠。

## 缺資料一律 `None`

沿用第十四輪 P2-1 的規約:**`0` 是事實,`None` 是不知道**。
沒量到的欄位不得填 0 —— 那個方向永遠偏向「這個實驗很便宜」。
"""
from __future__ import annotations

from typing import Optional

#: 兩個被比較的側,加上共用的抽取器。**順序固定**(報表與測試都依賴它)。
SIDES = ("primary", "shadow", "extractor")

#: 抽取器不屬於任何一側。見模組說明。
ATTRIBUTION = {"primary": "primary", "shadow": "shadow", "extractor": "shared"}

#: 逐側要留下來的數值欄位。**它們全部來自 provider 回報的 usage**,
#: 不是估出來的(單價另有 `pricing_tier` / `pricing_source` 溯源)。
TOKEN_FIELDS = ("prompt_tokens", "cached_tokens", "cache_write_tokens",
                "completion_tokens", "reasoning_tokens")


def _num(v):
    """數值就回它自己,否則 `None`。**`bool` 不是數值**(True 會被加成 1)。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _side(llm: dict, role: str) -> dict:
    """一側的當班實況:被接受的那次 + 被拒絕的嘗試(它們也要付錢)。"""
    accepted = llm.get(role) if isinstance(llm.get(role), dict) else None
    tried = [a for a in (llm.get("attempts") or [])
             if isinstance(a, dict) and a.get("role") == role]
    if accepted is None and not tried:
        # 沒有這一側。**明說不知道,不要回一組零** —— 影子被預算擋掉那天
        # 若記成「花了 0 元」,十天平均會把它當成一次免費的成功。
        return {"available": None, "basis": f"manifest 沒有 {role} 的紀錄",
                "attribution": ATTRIBUTION[role]}
    # **不要寫成 `_num(...) or 1`。** 那正是 P2-1 修掉的形狀:`0` 會被
    # 當成「沒有值」而掉進後備。缺 `calls` 的舊紀錄才退回 1。
    calls = _num((accepted or {}).get("calls"))
    out = {"available": True, "attribution": ATTRIBUTION[role],
           "model": (accepted or {}).get("model") or (
               tried[0].get("model") if tried else None),
           "accepted_calls": (int(calls) if calls is not None
                              else (1 if accepted else 0)),
           # 被拒絕的呼叫**也送出去了**,也計費。它們是「修補」的直接量測。
           "rejected_calls": len(tried),
           "measured_cost_usd": _num((accepted or {}).get("estimated_cost_usd")),
           # 沒有被拒絕的嘗試 → 失敗花費就是 **0,不是「不知道」**
           # (`rejected_calls` 出自同一份資料,說得出那個零是真的零)。
           # 有嘗試但都量不到金額時這裡也是 0,而下一欄會說有幾次量不到 ——
           # 兩個欄位一起讀才是完整的:量到的部分 + 量不到的次數。
           "failed_attempt_cost_usd": round(sum(
               _num(a.get("estimated_cost_usd")) or 0.0 for a in tried), 6),
           "billable_unmeasured_calls": sum(
               1 for a in tried if a.get("billable_unmeasured")),
           "elapsed_seconds": _num((accepted or {}).get("elapsed_seconds")),
           "pricing_tier": (accepted or {}).get("pricing_tier"),
           "pricing_source": (accepted or {}).get("pricing_source")}
    out.update({f: _num((accepted or {}).get(f)) for f in TOKEN_FIELDS})
    return out


def from_manifest(llm: Optional[dict]) -> dict:
    """把 manifest 的 `llm` 區塊整理成**逐側**的一列紀錄。

    這是要寫進實驗帳本的東西 —— manifest 隔天會被覆蓋,而帳本是追加的。
    """
    d = llm if isinstance(llm, dict) else {}
    return {role: _side(d, role) for role in SIDES}


def _median(values: list):
    v = sorted(values)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else round((v[mid - 1] + v[mid]) / 2, 3)


def _collect(rows: list, role: str, field: str) -> list:
    """帳本裡這一側、這個欄位**真的量到**的值。"""
    out = []
    for r in rows:
        side = ((r or {}).get(f"{role}_telemetry") or {})
        if not isinstance(side, dict) or side.get("available") is not True:
            continue
        v = _num(side.get(field))
        if v is not None:
            out.append(v)
    return out


def side_costs(ledger: Optional[list]) -> dict:
    """跨帳本的逐側成本與延遲。**橫跨所有嘗試,不是代表樣本。**

    配對可以一天只算一次,帳單不行 —— 重跑要付第二次錢,那筆錢真的花掉了。
    所以呼叫端要傳**整本帳**(而不是 `canonical()` 的結果)。

    `extractor` 與兩側分開回報:它是共用成本,加進任何一側都是編造。
    """
    rows = [r for r in (ledger or []) if isinstance(r, dict)]
    out = {"rows_seen": len(rows)}
    for role in SIDES:
        costs = _collect(rows, role, "measured_cost_usd")
        failed = _collect(rows, role, "failed_attempt_cost_usd")
        lat = _collect(rows, role, "elapsed_seconds")
        rejected = _collect(rows, role, "rejected_calls")
        unmeasured = _collect(rows, role, "billable_unmeasured_calls")
        out[role] = {
            "attribution": ATTRIBUTION[role],
            # **有幾天真的量到** —— 它與 `rows_seen` 差很多時,下面的總和
            # 只涵蓋了一部分的班次,不能當成「這十天的帳單」。
            "days_measured": len(costs),
            "cost_usd": round(sum(costs), 6) if costs else None,
            "failed_attempt_cost_usd": round(sum(failed), 6) if failed else None,
            "repair_calls": sum(rejected) if rejected else None,
            "billable_unmeasured_calls": sum(unmeasured) if unmeasured else None,
            # p95 需要的樣本數遠多於十個。給三個誠實的數字,讓人自己判斷。
            "latency_median_seconds": _median(lat),
            "latency_max_seconds": max(lat) if lat else None,
            "latency_samples": len(lat),
        }
    return out
