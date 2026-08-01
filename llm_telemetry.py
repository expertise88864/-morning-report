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

#: **模型登錄簿**(第九輪 P2-2)。每一列都要說得出數字**從哪來**。
#:
#: 原本只有一個 `DEFAULT_MAX_OUTPUT = 128_000` 套用到所有模型,而公開資料
#: 並沒有保證那是 family-wide 的契約 —— 那是把一個模型的規格當成整族的假設。
#: 送出超過真實上限的 `max_completion_tokens` 會直接 400,而 400 在這條路徑上
#: 的症狀是整份分析作廢。
#:
#: 出處:OpenAI Models 文件(使用者於 2026-08-01 提供,含 max output 128K /
#: context 1.05M / knowledge cutoff 2026-02-16)。
#: 沒有出處的模型不放進來 —— 見 `UNKNOWN_MODEL_MAX_OUTPUT`。
MODEL_LIMITS = {
    "gpt-5.6-sol": {"max_output": 128_000, "context": 1_050_000},
    "gpt-5.6-terra": {"max_output": 128_000, "context": 1_050_000},
    "gpt-5.6-luna": {"max_output": 128_000, "context": 1_050_000},
}

#: 沒收錄的模型用**保守**上限,不是樂觀的。理由不對稱:
#: 額度給得比真實上限低,最壞是輸出被截斷(有 `finish_reason=length` 可偵測,
#: 而且有減量重試);給得比真實上限高,是當場 400、整份分析作廢。
#: 要放寬就把那個模型連同出處加進 `MODEL_LIMITS` —— 那是一個要有人查過的動作。
UNKNOWN_MODEL_MAX_OUTPUT = 16_000

#: 舊常數保留給沒有指名模型的呼叫端(它是**粗略上界**,不是契約)。
DEFAULT_MAX_OUTPUT = 128_000


def max_output_for(model: str) -> tuple:
    """(輸出上限, 出處)。未收錄的模型回保守值並明說沒有出處。"""
    m = (model or "").strip().lower()
    for name, spec in MODEL_LIMITS.items():
        if m == name or m.startswith(name):
            return spec["max_output"], f"MODEL_LIMITS[{name}]"
    if not m:
        return DEFAULT_MAX_OUTPUT, "未指名模型,用粗略上界"
    return UNKNOWN_MODEL_MAX_OUTPUT, f"{model} 未收錄,用保守上限"


def output_cap(effort: str, base_tokens: int,
               max_output: Optional[int] = None, model: str = "") -> int:
    """依推理強度算輸出額度,並夾在該模型的真實上限內。

    未知強度取 medium 的倍數(不猜高也不猜低);未收錄的模型取保守上限。
    """
    mult = CAP_MULTIPLIER.get((effort or "").strip().lower(), CAP_MULTIPLIER["medium"])
    cap = max_output if max_output is not None else max_output_for(model)[0]
    return min(int(base_tokens) * mult, int(cap))


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
                 finish_reason: str = "", error: str = "",
                 elapsed: float = 0.0) -> dict:
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
        ct = cached_tokens_of(usage)
        if ct is not None:
            rec["cached_tokens"] = ct
        cw = cache_write_tokens_of(usage)
        if cw is not None:
            rec["cache_write_tokens"] = cw
        cost = estimate_cost(model, usage)
        if cost["usd"] is not None:
            rec["estimated_cost_usd"] = cost["usd"]
        rec["cost_basis"] = cost["basis"]
    if elapsed:
        # **失敗的呼叫也要有耗時。** 逾時是本 repo 最常見的 LLM 失敗模式,
        # 而「花了幾秒才逾時」是判斷 timeout 該不該調的唯一依據。
        rec["elapsed_seconds"] = round(float(elapsed), 1)
    if finish_reason:
        rec["finish_reason"] = finish_reason
    if error:
        rec["error"] = str(error)[:160]
    return rec


#: 同一角色的重試要累加,否則與帳單對不上。
_ACCUMULATE = ("prompt_tokens", "completion_tokens", "total_tokens",
               "reasoning_tokens", "cached_tokens", "cache_write_tokens")


#: 這些是浮點,要分開累加(`isinstance(x, int)` 對 float 是 False,
#: 混在一起會讓成本與耗時**靜默不累加** —— 而那正是「看起來有數字」的失敗)。
_ACCUMULATE_FLOAT = ("estimated_cost_usd", "elapsed_seconds")


def merge_same_role(previous: Optional[dict], record: dict) -> dict:
    """把同一角色的前一次紀錄累加進來(token/成本/耗時累加,其餘取最新)。"""
    out = dict(record)
    prev = previous or {}
    for k in _ACCUMULATE:
        if isinstance(prev.get(k), int) and isinstance(out.get(k), int):
            out[k] += prev[k]
    for k in _ACCUMULATE_FLOAT:
        if isinstance(prev.get(k), (int, float)) and isinstance(out.get(k), (int, float)):
            out[k] = round(out[k] + prev[k], 6)
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


#: 各 provider 的時間預算基準(總額秒, 單次請求秒)。**依實測訂。**
#:
#: 2026-08-01 生產實測:`ReadTimeout … api.openai.com (read timeout=75.0)` ——
#: GPT-5.6 跑本專案的 85,814-token prompt **在 75 秒內完成不了**,而備援的
#: Gemini 也失敗,使用者收到的是降級版基本報告。同一天 DeepSeek v4-pro 在
#: 同樣的 75 秒內一次完成(reasoning_tokens=517)—— 兩家的生成速度差一個量級,
#: 用同一組秒數等於對其中一家必然過短。
#:
#: 上限來自另一個實測:該班總耗時 588s / 執行預算 1140s,也就是還有約 550 秒
#: 的餘裕。所以放寬是**有空間**的,不是把寄信的時間拿去賭。
PROVIDER_TIMEOUT_BASE = {
    "openai": (360.0, 240.0),
    "deepseek": (180.0, 75.0),
}
DEFAULT_TIMEOUT_BASE = (180.0, 75.0)

#: 總額的硬上限。再長就會開始擠壓寄信 —— 「晨報不可斷」優先於「跑完推理」。
#: 批#101:600 → 900,與 `RUN_BUDGET_SECONDS` 2100 一起放寬。
#: 實測參考:xhigh 在 2026-08-01 用了 196 秒,離上限還很遠;
#: 放寬是為了讓 max 不必靠擠掉新聞全文來換時間。
MAX_TOTAL_TIMEOUT = 900.0


def timeout_base(provider: str) -> tuple:
    """(總額, 單次請求)的基準秒數。"""
    return PROVIDER_TIMEOUT_BASE.get(
        (provider or "").strip().lower(), DEFAULT_TIMEOUT_BASE)


def timeout_for(effort: str, base_seconds: float,
                cap: float = MAX_TOTAL_TIMEOUT) -> float:
    """依推理強度放大 timeout,但不超過上限。未知強度不放大(不猜)。"""
    scaled = base_seconds * EFFORT_TIME_MULTIPLIER.get(
        (effort or "").strip().lower(), 1.0)
    return round(min(scaled, cap), 1)


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


# ---------------------------------------------------------------- 成本估算

#: 每 100 萬 token 的官方牌價(USD)。**只收錄有出處的。**
#:
#: 查不到單價的一律不估,而不是拿別處的數字近似 —— 這個數字會直接被拿來做
#: 「換不換模型」的決定,而我在 2026-08-01 已經用 OpenRouter 的價格估過一次
#: GPT-5.6,結果比官方低 2.5 倍。錯的成本數字比沒有成本數字更糟。
#:
#: 出處:OpenAI Models 文件(使用者於 2026-08-01 提供)。
#: DeepSeek 刻意留空 —— 我手上沒有官方單價,寧可回報「未收錄」。
MODEL_PRICING = {
    "gpt-5.6-sol": {"input": 5.00, "output": 30.00},
    "gpt-5.6-terra": {"input": 2.00, "output": 12.00},
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
}


def cache_write_tokens_of(usage: Optional[dict]) -> Optional[int]:
    """寫入快取的 token 數(批#100)。

    2026-08-01 實測回應:`prompt_tokens_details: {cached_tokens: 0,
    cache_write_tokens: 93191}` —— 我原本只記 `cached_tokens`(當天是 0),
    於是「快取」這件事在 manifest 裡看起來完全沒發生,而實際上有 93,191 個
    token 被寫進快取。寫入通常另有費率,**沒收錄費率就不能假裝成本是精確的**。
    """
    det = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    if isinstance(det, dict) and isinstance(det.get("cache_write_tokens"), int):
        return det["cache_write_tokens"]
    return None


def cached_tokens_of(usage: Optional[dict]) -> Optional[int]:
    """快取命中的輸入 token 數。兩家的欄位名不同,**擇一不相加**。

    OpenAI:`prompt_tokens_details.cached_tokens`;
    DeepSeek:`prompt_cache_hit_tokens`。
    """
    if not isinstance(usage, dict):
        return None
    det = usage.get("prompt_tokens_details")
    if isinstance(det, dict) and isinstance(det.get("cached_tokens"), int):
        return det["cached_tokens"]
    hit = usage.get("prompt_cache_hit_tokens")
    return hit if isinstance(hit, int) else None


def price_of(model: str) -> Optional[dict]:
    """查單價。先精確比對,再前綴比對(容納 `-2026-02-16` 這類日期後綴)。"""
    m = (model or "").strip().lower()
    if m in MODEL_PRICING:
        return MODEL_PRICING[m]
    for name, price in MODEL_PRICING.items():
        if m.startswith(name):
            return price
    return None


def estimate_cost(model: str, usage: Optional[dict]) -> dict:
    """估這一次呼叫的成本。**估不出來就說估不出來。**

    回 `{"usd": float|None, "basis": str}`。`basis` 一定要寫,因為這個數字
    帶著兩個會被忘記的假設:(a) 推理 token 以 output 計價(這是推理模型的
    定價方式,而它通常是帳單的主要來源);(b) 快取命中的輸入**以全價計** ——
    折扣比例我手上沒有官方數字,所以這是**上界**而不是實際金額。
    """
    price = price_of(model)
    if not price:
        return {"usd": None, "basis": f"未收錄 {model or '(未知模型)'} 的單價,不估"}
    if not isinstance(usage, dict):
        return {"usd": None, "basis": "沒有 usage,不估"}
    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not (isinstance(pt, int) and isinstance(ct, int)):
        return {"usd": None, "basis": "usage 缺 token 數,不估"}
    usd = (pt * price["input"] + ct * price["output"]) / 1_000_000
    basis = "官方牌價;推理以 output 計價"
    cached = cached_tokens_of(usage)
    if cached:
        basis += f";快取命中 {cached} tok 以全價計(折扣未收錄),此項偏高"
    written = cache_write_tokens_of(usage)
    if written:
        # 兩個方向要一起講。只講「上界」會讓人以為實付一定更低,
        # 而 2026-08-01 的帳單就高於這裡的估計。
        basis += f";另有 {written} tok 寫入快取,其費率未收錄、**未計入**,此項偏低"
    return {"usd": round(usd, 6), "basis": basis}


#: 抽取器逾時時的替補順序。**依「機械性任務夠用且便宜」排,不依品質排** ——
#: 抽取是把新聞抄成 JSON 欄位,換到更貴的模型不會抄得更準。
EXTRACTOR_FALLBACK_ORDER = ("openai", "deepseek", "gemini")


def fallback_extractor_provider(primary: str, has_key) -> str:
    """抽取器該改用誰;沒有可用的替代就回空字串(批#96)。

    2026-08-01 連續兩班的抽取器都在同一個 endpoint 逾時
    (`api.deepseek.com` read timeout,35 則進去 0 則出來),
    而它被釘在單一 provider 上 —— 對方機房的狀況直接等於今天沒有事件抽取。

    只在**網路層**失敗時才換人:HTTP 4xx 是我們的請求有問題,換一家會用同樣
    的錯誤參數再錯一次;額度用完有既有的減量重試路徑,不該混進來。
    """
    p = (primary or "").strip().lower()
    for cand in EXTRACTOR_FALLBACK_ORDER:
        if cand != p and has_key(PROVIDER_KEY_ENV[cand]):
            return cand
    return ""


#: 角色槽位(`attempts` 是失敗紀錄的清單,不是角色)。
_ROLE_SLOTS = ("primary", "extractor", "shadow")


def run_cost_summary(slot: Optional[dict]) -> dict:
    """把整班的 LLM 成本彙整成可以跟帳單對照的數字(批#100)。

    **重點是誠實標記「這個數字不完整」。**

    逾時的呼叫照樣計費 —— server 端已經生成的 token 不會因為 client 放棄就
    不算 —— 但它沒有 usage 可讀,所以永遠進不了加總。2026-08-01 實際帳單約
    $0.1,而 manifest 只記到 $0.056,差額主要就是 08:57 那班逾時 75 秒的呼叫。

    只報一個看起來精確的總額,會讓人以為帳單對不上是別的原因;
    **成本估算如果只在成功時準確,它在最該看的時候(一直逾時的那幾天)最不準。**
    """
    slot = slot or {}
    total, measured, models = 0.0, 0, set()
    for role in _ROLE_SLOTS:
        rec = slot.get(role)
        if not isinstance(rec, dict):
            continue
        models.add(str(rec.get("model") or ""))
        if isinstance(rec.get("estimated_cost_usd"), (int, float)):
            total += rec["estimated_cost_usd"]
            measured += int(rec.get("calls") or 1)
        elif rec.get("prompt_tokens"):
            measured += int(rec.get("calls") or 1)
    billed_unknown = [a for a in (slot.get("attempts") or [])
                      if isinstance(a, dict) and a.get("billable_unmeasured")]
    out = {"total_usd": round(total, 6), "measured_calls": measured,
           "unmeasured_billable_calls": len(billed_unknown)}
    notes = []
    if billed_unknown:
        notes.append(f"另有 {len(billed_unknown)} 次呼叫已送出但沒有 usage"
                     "(逾時/連線中斷)—— 那些仍會計費,**不在總額內**")
    unknown_price = sorted(m for m in models if m and price_of(m) is None)
    if unknown_price:
        notes.append(f"未收錄單價:{'、'.join(unknown_price)}")
    if notes:
        out["incomplete"] = ";".join(notes)
    return out
