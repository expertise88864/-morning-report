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


# ---------------------------------------------------------------- 設定與預算

#: 合法的 provider。**未知值必須當場失敗**,不得靜默落到別人身上
#: (第九輪 P0-1 的教訓:fallthrough 讓「看起來有分開設定、實際沒有」)。
VALID_PROVIDERS = ("deepseek", "openai", "gemini", "anthropic")

#: 每個 provider 需要哪把金鑰。**只驗被選中的那個** ——
#: 既有的「有任一把金鑰就啟動」是在只有 DeepSeek 的年代寫的,
#: 換成 OpenAI-only 之後那個閘門會誤判成可用(第九輪 P1-7)。
PROVIDER_KEY_ENV = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY",
                    "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

#: 正式排程允許的推理強度上限(第九輪 P1-2)。
#:
#: 提高 token 額度只避免 server 端截斷,**避不掉 client 端的 75 秒 timeout**:
#: xhigh 的 98,000 token 額度遠超過 75 秒能生成的量,結果是逾時 → 掉備援,
#: 連想評估的那個模型的輸出都看不到。額度與 wall-clock 必須是同一個預算。
#: 高強度留給手動執行/影子/離線 benchmark,那裡沒有「信必須準時寄出」的約束。
SCHEDULED_MAX_EFFORT = {"primary": "medium", "extractor": "low",
                        "shadow": "medium"}
_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def effort_rank(effort: str) -> int:
    """推理強度的序位;未知值視為 medium(不猜高也不猜低)。"""
    e = (effort or "").strip().lower()
    return _EFFORT_ORDER.index(e) if e in _EFFORT_ORDER else _EFFORT_ORDER.index("medium")


def validate_llm_config(*, provider: str, extractor_provider: str,
                        shadow_provider: str, has_key,
                        efforts: Optional[dict] = None,
                        scheduled: bool = True) -> list:
    """回傳設定問題清單(空 = 沒問題)。**不拋例外**:呼叫端決定要擋還是只告警。

    第九輪 P1-5:模型現在可由 GitHub Variables 隨時改,而打錯字的症狀是
    **「一切照舊」** —— 沒有錯誤、沒有告警,只是沒切過去。設定本身要能被驗。

    `has_key` 是 `callable(env_name) -> bool`,由呼叫端提供,
    這樣本模組不必碰 os.environ(保持純函式、可單獨測)。
    """
    out = []
    roles = {"primary": provider, "extractor": extractor_provider}
    if shadow_provider:
        roles["shadow"] = shadow_provider
    for role, prov in roles.items():
        prov = (prov or "").strip().lower()
        if not prov:
            continue
        if prov not in VALID_PROVIDERS:
            out.append(f"{role} provider 不是合法值:{prov!r}"
                       f"(可用 {'/'.join(VALID_PROVIDERS)})")
            continue
        env = PROVIDER_KEY_ENV[prov]
        if not has_key(env):
            out.append(f"{role} 選了 {prov} 但缺 {env}")
    if shadow_provider and shadow_provider.strip().lower() == (provider or "").strip().lower():
        out.append("影子與主分析是同一個 provider —— 比較不出東西,只是加倍付費")
    for role, eff in (efforts or {}).items():
        eff = (eff or "").strip().lower()
        if eff and eff not in _EFFORT_ORDER:
            out.append(f"{role} 的推理強度不是合法值:{eff!r}")
        elif scheduled and eff and role in SCHEDULED_MAX_EFFORT:
            cap = SCHEDULED_MAX_EFFORT[role]
            if effort_rank(eff) > effort_rank(cap):
                out.append(
                    f"{role} 推理強度 {eff} 超過正式排程上限 {cap} —— "
                    "額度會遠超過單次請求 timeout 能生成的量,逾時後會掉備援;"
                    "高強度請用手動執行或影子")
    return out


def error_blames_param(err: Optional[dict], param: str) -> bool:
    """OpenAI 的錯誤物件是不是在指責**這個參數**(第九輪 P1-3)。

    400 也可能來自 model ID 錯、額度過大、schema 不合、專案沒權限 ——
    那些情況移除某個選配參數沒有用,只是白花一次呼叫,而**真正的錯誤訊息
    會被第二次的失敗蓋掉**。所以解析不出來時保守回 False:
    寧可讓原始錯誤浮上來(訊息完整、可診斷),也不要盲目重試。
    """
    if not isinstance(err, dict):
        return False
    if str(err.get("param") or "") == param:
        return True
    # 有些回應把參數名放在訊息裡而不是 param 欄位
    return (param in str(err.get("message") or "")
            and str(err.get("type") or "") == "invalid_request_error")


def response_blames_param(response, param: str) -> bool:
    """從**回應物件**判斷 400 是否指責該參數(鴨子型別,只用到 `.json()`)。

    刻意不 import requests:本模組維持零外部相依,才能單獨測。
    """
    try:
        err = (response.json() or {}).get("error")
    except Exception:                       # noqa: BLE001 - 非 JSON 的 400
        return False
    return error_blames_param(err, param)
