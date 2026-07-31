# -*- coding: utf-8 -*-
"""LLM 呼叫的**純函式**部分:輸出額度、與一次呼叫的紀錄組裝。

批#91(第九輪 P1-11)。第九輪外審指出主模組只剩約 20 行空間,而
「新增的內容本來就是非常適合抽出的 provider layer」—— 這次不再調高上限,
而是把**不碰 state 也不碰網路**的部分搬出來。

留在主模組的是對外請求(`requests`)與 manifest 寫入,那兩件事本來就屬於那裡;
搬出來的是額度計算與紀錄組裝,它們是純函式,因此可以單獨測、也不受
conftest 的 state 隔離影響。
"""
from __future__ import annotations

from typing import Optional

#: 推理強度 → 輸出額度倍數(相對於可見答案長度)。
#:
#: `max_completion_tokens` 是 **reasoning + 答案的總額**。固定 4 倍(28,000)
#: 在推理達答案的 3 倍時就會被截斷 —— 那正是 2026-07-31 抽取器 1560 則事件
#: 0 產出的成因,而設 xhigh 等於保證重演。額度只是上限、沒用到不計費,
#: 所以寧可給寬:被截斷的代價是整份分析作廢,給寬的代價是零。
#:
#: 依實測推得:抽取器在**機械性**任務上推理量已達答案的 1.5 倍
#: (4,000 tok 推理 / ~2,700 tok 答案),主分析這種複雜推理只會更多。
#: `minimal` 是官方 API reference 列出的值(第九輪 P1-1 指出原本漏了它)。
CAP_MULTIPLIER = {"none": 2, "minimal": 3, "low": 4, "medium": 6,
                  "high": 10, "xhigh": 14, "max": 16}

#: 模型硬上限。**這是家族層級的粗略上界,不是每個 snapshot 的契約**
#: (第九輪 P2-2:公開資料沒有保證 family-wide 128K output cap)。
#: 真正的上限應由 model registry 或 canary 驗出來;在那之前這個值只用來
#: 避免送出荒謬的大數字。
DEFAULT_MAX_OUTPUT = 128_000


def output_cap(effort: str, base_tokens: int,
               max_output: int = DEFAULT_MAX_OUTPUT) -> int:
    """依推理強度算輸出額度。未知強度取 medium 的倍數(不猜高也不猜低)。"""
    mult = CAP_MULTIPLIER.get((effort or "").strip().lower(), CAP_MULTIPLIER["medium"])
    return min(int(base_tokens) * mult, int(max_output))


def reasoning_tokens_of(usage: dict) -> Optional[int]:
    """從 usage 取推理 token。**兩種欄位擇一,不得相加**(第九輪 P2-3)。

    provider 若同時提供 `reasoning_tokens` 與
    `completion_tokens_details.reasoning_tokens`,相加會憑空翻倍 ——
    而這正是拿來估成本、以及判斷 `reasoning_effort` 有沒有生效的數字。
    """
    if not isinstance(usage, dict):
        return None
    top = usage.get("reasoning_tokens")
    if isinstance(top, int):
        return top
    det = usage.get("completion_tokens_details")
    if isinstance(det, dict) and isinstance(det.get("reasoning_tokens"), int):
        return det["reasoning_tokens"]
    return None


def build_record(provider: str, model: str, *, requested_effort: str = "",
                 applied_effort: str = "", usage: Optional[dict] = None,
                 finish_reason: str = "", error: str = "") -> dict:
    """把一次呼叫整理成紀錄。**requested 與 applied 分開**(第九輪 P1-1)。

    400 退讓會移除 `reasoning_effort`,那次呼叫用的是 provider 預設。
    只記 requested 的話,manifest 會顯示使用者要的強度而實際沒帶那個參數 ——
    看起來像有生效。
    """
    rec = {"provider": provider, "model": model,
           "requested_effort": requested_effort or None,
           "applied_effort": applied_effort or None}
    if usage:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = usage.get(k)
            if isinstance(v, int):
                rec[k] = v
        rt = reasoning_tokens_of(usage)
        if rt is not None:
            rec["reasoning_tokens"] = rt
    if finish_reason:
        rec["finish_reason"] = finish_reason
    if error:
        rec["error"] = str(error)[:160]
    return rec


#: 同一角色的重試要累加,否則與帳單對不上。
_ACCUMULATE = ("prompt_tokens", "completion_tokens", "total_tokens",
               "reasoning_tokens")


def merge_same_role(previous: Optional[dict], record: dict) -> dict:
    """把同一角色的前一次紀錄累加進來(token 累加,其餘取最新)。"""
    out = dict(record)
    prev = previous or {}
    for k in _ACCUMULATE:
        if isinstance(prev.get(k), int) and isinstance(out.get(k), int):
            out[k] += prev[k]
    out["calls"] = int(prev.get("calls") or 0) + 1
    return out
