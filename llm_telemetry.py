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

#: 正式排程可用的推理強度上限,**依 provider 分開**(第九輪 P1-2、批#93)。
#:
#: 批#92 用一張跨 provider 的表(primary 一律 medium),上線第一班就誤報:
#: 2026-08-01 生產 manifest 顯示 `deepseek-v4-pro / requested=high / applied=high
#: / reasoning_tokens=517 / completion=5,557 / calls=1` —— 一次就完成,
#: 而 `high` 正是這個 repo 的**程式碼預設值**。也就是說那條守衛每天都會把
#: `llm:config_issue` 塞進降級清單,而降級清單一旦有常駐雜訊,真的問題就被淹掉。
#:
#: 根因是**推理強度的標籤在不同 provider 之間不可比**:DeepSeek 的 high 只推理
#: 517 個 token,GPT-5.6 的 high 可以燒掉數萬個(2026-07-31 抽取器 0 產出正是
#: 推理吃光額度)。所以上限必須各自依**實測**訂,沒實測過的就別假裝知道。
SCHEDULED_MAX_EFFORT = {
    # 實測:2026-08-01 生產,high 一次完成(見上)。high 以上未實測。
    "deepseek": {"primary": "high", "extractor": "high", "shadow": "high"},
    # 主分析 xhigh 可用 —— 但**前提是 timeout 一起放大**(見 timeout_for)。
    # 抽取器維持 low:2026-07-31 的 1560 則 0 產出就是抽取器推理過頭造成的,
    # 而抽取是機械性任務,推理再多也不會抄得更準。
    "openai": {"primary": "xhigh", "extractor": "low", "shadow": "xhigh"},
}
_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

#: 推理強度 → 時間倍率。**額度與 wall-clock 是同一個預算**(第九輪 P1-2)。
#:
#: 把 `max_completion_tokens` 放大只避得開 server 端截斷,避不開 client 端
#: timeout:xhigh 的額度是 98,000 token,而 75 秒內不可能生成那個量 ——
#: 結果是逾時掉備援,連想評估的那個模型的輸出都看不到。倍率刻意遠小於額度倍率
#: (額度 6→14 是 2.3 倍,時間只放大 2.4 倍):額度是**上限**,沒用到不計費,
#: 給寬無代價;時間是**真的會被花掉**的東西,而它直接排擠寄信。
EFFORT_TIME_MULTIPLIER = {"none": 1.0, "minimal": 1.0, "low": 1.0,
                          "medium": 1.0, "high": 1.6, "xhigh": 2.4, "max": 3.0}


def timeout_for(effort: str, base_seconds: float) -> float:
    """依推理強度放大 timeout。未知強度不放大(不猜)。"""
    return round(base_seconds * EFFORT_TIME_MULTIPLIER.get(
        (effort or "").strip().lower(), 1.0), 1)


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
            continue
        prov = (roles.get(role) or "").strip().lower()
        cap = SCHEDULED_MAX_EFFORT.get(prov, {}).get(role) if scheduled else None
        if cap and eff and effort_rank(eff) > effort_rank(cap):
            out.append(
                f"{role}({prov})推理強度 {eff} 超過實測過的上限 {cap} —— "
                "超出的部分沒有量測支持,逾時掉備援的風險未知;"
                "要提高請先用手動執行或影子量一次 reasoning_tokens")
    return out


def config_source_issues(raw: str) -> list:
    """哪些 LLM 設定**其實沒生效**(批#93)。

    2026-08-01 的事故:使用者設了 `LLM_PROVIDER=openai` 卻仍跑 DeepSeek,
    而 manifest **分辨不出「沒設」與「設成 deepseek」** —— workflow 在 YAML
    裡就用 `${{ vars.X || 'deepseek' }}` 補了預設,程式看到的永遠是最終值。
    (最可能的原因是設成了 Secrets:`vars.X` 讀不到 Secret,就落回預設。)

    所以 workflow 另外把**原始的 `vars.*`** 以 `k=v;k=v` 傳進來,由這裡指出
    哪些是空的。診斷必須進 manifest 而不是只印在 job log —— job log 要 admin
    權限才讀得到,而 manifest 是 commit 進 repo 的。
    """
    if not raw:
        return []
    seen = {}
    for chunk in str(raw).split(";"):
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            seen[k.strip()] = v.strip()
    unset = sorted(k for k, v in seen.items() if not v)
    if not unset:
        return []
    return [f"這些 repo variable 沒有設定(走 workflow 預設值):{'、'.join(unset)}"
            " —— 若你以為設過了,請確認是設在 Variables 而不是 Secrets"]


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


def config_snapshot(*, provider: str, extractor_provider: str,
                    shadow_provider: str, model: str, primary_effort: str,
                    extractor_effort: str, request_timeout: float,
                    total_timeout: float, raw_vars: str, has_key):
    """組出「本班打算用什麼」的快照 + 設定問題清單(批#93)。

    **設定是輸入,不該只在成功路徑上被記錄。** 2026-08-01 使用者以為切到 OpenAI
    卻仍跑 DeepSeek,而當時 manifest 只在呼叫**被接受後**才寫 provider ——
    呼叫失敗或走備援時,就完全看不出本班原本打算用什麼、逾時預算是多少。
    """
    snap = {"provider": provider, "extractor": extractor_provider,
            "model": model, "primary_effort": primary_effort,
            "request_timeout": request_timeout, "total_timeout": total_timeout}
    if shadow_provider:
        snap["shadow"] = shadow_provider
    issues = config_source_issues(raw_vars) + validate_llm_config(
        provider=provider, extractor_provider=extractor_provider,
        shadow_provider=shadow_provider, has_key=has_key,
        efforts={"primary": primary_effort, "extractor": extractor_effort})
    return snap, issues
