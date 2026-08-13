# -*- coding: utf-8 -*-
"""LLM 的**設定**:驗證、來源、時間預算與替補 provider。

第十一輪 P2-1。原本這些和「用量計價」同住 `llm_telemetry.py`,而那個檔在
補齊設定來源遙測後撞到 700 行上限 —— 上限守衛做了它該做的事:指出這個檔
已經在做兩件事。

切法依相依方向決定,不依主題喜好:計價那半(額度、token、牌價)**不需要**
知道任何設定;設定這半需要 `supported_efforts` 去驗推理強度。所以是單向的
`llm_config → llm_telemetry`。

刻意**不從 `llm_telemetry` 轉出這些名字**。轉出會形成循環匯入,而循環匯入
只在「先 import 誰」是某個特定順序時才動 —— 那是這個 repo 最不該再有的
一類失敗:沒有錯誤、只是在別的入口壞掉。呼叫端改成直接 import 本模組。
"""
from __future__ import annotations

from typing import Optional

from llm_telemetry import supported_efforts

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
    # 這張表是**生產已驗證**的上限,不是「API 收得下」的上限 ——
    # 第十一輪 P1-3 指出批#118 把兩者混為一談:官方文件只能證明
    # 「API 收得下 max」,不能證明「85k-token 的 prompt 在現行 timeout、
    # 預算與備援條件下能穩定完成」。支援性見 `MODEL_LIMITS[...]["efforts"]`。
    #
    # 2026-08-07/08 **手動執行的量測**(這正是本表下面那句話要求的形式:
    # 「要提高請先用手動執行量一次 reasoning_tokens」)。七輪本機 DRY_RUN
    # 打真實端點、走生產同一條 packet 與 32K strict schema,`effort=max`:
    #
    #   prompt        78,381 / 81,870 / 83,751 / 84,603 / 84,810 tok
    #   reasoning     5,890 / 13,566 / 14,108 / 18,024 / 27,188 tok
    #   elapsed(單次被採用的呼叫) 81.1 / 189.7 / 192.3 / 264.4 / 312.8 s
    #   applied_effort=max(**從回應讀的**,不是回報我們送的)
    #   finish_reason=stop —— 沒有一次被輸出額度截斷
    #
    # 單次請求上限在 max 之下是 450s(150 × 3.0),量到的最慢 312.8s ——
    # 還有餘裕;總預算 1200s 裝得下「兩輪特化 + legacy 備援」。
    #
    # **誠實記下這份證據的邊界**:它是本機手動執行,不是 GitHub Actions
    # runner 上的排程班。CI 的網路與機器較慢時仍可能更久,那要由第一次
    # 排程班的 manifest 來確認(elapsed 與 applied_effort 都有記)。
    # 抽取器維持 high:它走的是另一條呼叫,沒有一起量過。
    "deepseek": {"primary": "max", "extractor": "high"},
    # 主分析 xhigh 可用 —— 但**前提是 timeout 一起放大**(見 timeout_for)。
    # 抽取器維持 low:2026-07-31 的 1560 則 0 產出就是抽取器推理過頭造成的,
    # 而抽取是機械性任務,推理再多也不會抄得更準。
    "openai": {"primary": "xhigh", "extractor": "low"},
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
    # 第十一輪 P1-3:原本 (180, 75) 之下,`max` 的單次上限是 225 秒,
    # 而 v4-pro 在 **high** 就已實測 171.9 秒 —— 只剩 53 秒餘裕,
    # 而 max 的推理量預期更大。餘裕不足時的失敗模式是逾時掉備援,
    # 也就是使用者收到降級版報告(2026-08-01 已經發生過一次)。
    # 拉到 (300, 150):max 之下總額 900s、單次 450s,約是實測值的 2.6 倍。
    # 2026-08-07 再拉總額基準到 400(max 之下 1200s):flash + 1M payload
    # 單次 310-370s,900s 裝不下「兩輪特化 + legacy」(見 MAX_TOTAL_TIMEOUT)。
    "deepseek": (400.0, 150.0),
}
DEFAULT_TIMEOUT_BASE = (180.0, 75.0)

#: 總額的硬上限。再長就會開始擠壓寄信 —— 「晨報不可斷」優先於「跑完推理」。
#: 批#101:600 → 900,與 `RUN_BUDGET_SECONDS` 2100 一起放寬。
#: 2026-08-07:900 → 1200。flash + 1M payload 實測單次特化呼叫 310-370 秒,
#: 900 之下「兩輪特化 + legacy 備援」在結構上不可能 —— E2E 第五次實測
#: 兩輪特化跑完,legacy 直接「總時間預算已耗盡」,信只剩 emergency 備援字。
#: 1200 = 兩輪(~740s)+ legacy(~450s);job timeout-minutes 40 仍有餘裕。
MAX_TOTAL_TIMEOUT = 1200.0


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
                        models: Optional[dict] = None,
                        scheduled: bool = True) -> list:
    """回傳設定問題清單(空 = 沒問題)。**不拋例外**:呼叫端決定要擋還是只告警。

    第九輪 P1-5:模型現在可由 GitHub Variables 隨時改,而打錯字的症狀是
    **「一切照舊」** —— 沒有錯誤、沒有告警,只是沒切過去。設定本身要能被驗。

    `has_key` 是 `callable(env_name) -> bool`,由呼叫端提供,
    這樣本模組不必碰 os.environ(保持純函式、可單獨測)。
    """
    # r1(Codex #8,P2):**回傳要帶嚴重度。**
    # 原本一律是字串,金絲雀因此把全部包成 `fatal=False` —— 於是
    # 「選了 deepseek 卻沒有 DEEPSEEK_API_KEY」這種必定失敗的設定,
    # 金絲雀仍然 exit 0、workflow 綠燈,它就當不成設定閘門。
    # `fatal` 的判準是「這樣上線一定不會動」,不是「風險比較高」。
    out: list = []
    models = models or {}
    roles = {"primary": provider, "extractor": extractor_provider}
    if shadow_provider:
        roles["shadow"] = shadow_provider
    for role, raw_prov in roles.items():
        prov = (raw_prov or "").strip().lower()
        if not prov:
            # r1(Codex,#2):**「空白」與「沒設」不是同一件事。**
            # `LLM_PROVIDER=" "` 在 GitHub Actions 是 truthy(走 repo variable),
            # strip 之後才變空 —— 而 `_call_llm_text` 對不上任何分支就落到
            # Gemini。原本這裡直接 `continue`,於是「實跑 Gemini、遙測聲稱
            # 走 DeepSeek 預設」完全不會被報出來。
            if raw_prov:
                out.append(_issue(
                    f"{role} provider 設成了空白字元 {raw_prov!r} —— "
                    "它會對不上任何 provider 而靜默落到 Gemini", fatal=True))
            elif role == "primary":
                # extractor 空 = 跟隨主分析、shadow 空 = 關閉,那兩個合法;
                # 主分析沒有「空」這個合法狀態(空一樣落到 Gemini)。
                out.append(_issue(
                    "primary provider 是空的 —— 它會靜默落到 Gemini,"
                    "而 manifest 會顯示一個沒有人選過的 provider", fatal=True))
            continue
        if prov not in VALID_PROVIDERS:
            out.append(_issue(f"{role} provider 不是合法值:{prov!r}"
                       f"(可用 {'/'.join(VALID_PROVIDERS)})", fatal=True))
            continue
        env = PROVIDER_KEY_ENV[prov]
        if not has_key(env):
            out.append(_issue(f"{role} 選了 {prov} 但缺 {env}", fatal=True))
    # 第十輪 P1-5:**同 provider 不等於比較不出東西。**
    # Terra vs Luna、Luna low vs Luna xhigh、Pro vs Flash 都是有意義的實驗。
    # 真正沒有意義的是 provider、model、推理強度**三者全同**。
    _same = (shadow_provider and provider
             and shadow_provider.strip().lower() == provider.strip().lower()
             and str(models.get("shadow") or "") == str(models.get("primary") or "")
             and str((efforts or {}).get("shadow") or "")
             == str((efforts or {}).get("primary") or ""))
    if _same:
        out.append(_issue("影子與主分析的 provider、模型與推理強度完全相同 —— "
                          "比較不出東西,只是加倍付費", fatal=False))
    for role, eff in (efforts or {}).items():
        eff = (eff or "").strip().lower()
        if eff and eff not in _EFFORT_ORDER:
            out.append(_issue(f"{role} 的推理強度不是合法值:{eff!r}", fatal=True))
            continue
        prov = (roles.get(role) or "").strip().lower()
        # **模型實測不支援的強度要當場擋下**(批#105)。這比排程上限嚴重得多:
        # 上限只是「沒量過、風險未知」,不支援則是「這一定不會生效」——
        # 而它的症狀是靜默退回 provider 預設,信照常寄出。
        ok = supported_efforts(models.get(role) or "") if eff else None
        if ok and eff not in ok:
            out.append(_issue(
                f"{role} 的模型 {models.get(role)} 實測**不支援**推理強度"
                f" {eff}(可用 {'/'.join(ok)})—— 送出去會被拒絕並靜默"
                "退回 provider 預設", fatal=True))
            continue
        cap = SCHEDULED_MAX_EFFORT.get(prov, {}).get(role) if scheduled else None
        if cap and eff and effort_rank(eff) > effort_rank(cap):
            out.append(_issue(
                f"{role}({prov})推理強度 {eff} 超過實測過的上限 {cap} —— "
                "超出的部分沒有量測支持,逾時掉備援的風險未知;"
                "要提高請先用手動執行或影子量一次 reasoning_tokens", fatal=False))
    return out


class _issue(str):
    """設定問題:本體是字串(既有呼叫端不受影響),額外帶 `fatal` 嚴重度。

    做成 str 子類是刻意的:manifest、log、測試都直接用它當訊息,
    改成 dict 會讓那些地方全部要跟著改,而**改動面越大越容易漏一處**。
    """

    def __new__(cls, msg: str, fatal: bool = False):
        obj = super().__new__(cls, msg)
        obj.fatal = fatal
        return obj


def is_fatal(issue) -> bool:
    """這條設定問題是不是「一定不會動」。未標記的一律當成非致命(保守)。"""
    return bool(getattr(issue, "fatal", False))


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
    return [_issue(
        f"這些 repo variable 沒有設定(走 workflow 預設值):{'、'.join(unset)}"
        " —— 若你以為設過了,請確認是設在 Variables 而不是 Secrets", fatal=False)]


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
                    extractor_effort: str, extractor_model: str = "",
                    request_timeout: float = 0.0,
                    total_timeout: float, raw_vars: str, has_key,
                    resolved: Optional[dict] = None):
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
    # 第十一輪 P2-1:**來源是事實,不是問題。** 原本兩者被串成同一個清單,
    # 呼叫端只好用 `"沒有設定" in m` 把它們拆回來 —— 訊息一改字就會把
    # 「走預設」誤判成降級。現在來源進 `snap["sources"]`(結構化、可逐鍵提問),
    # `issues` 只留真正的設定錯誤。
    snap["sources"] = config_sources(raw_vars, resolved)
    issues = validate_llm_config(
        provider=provider, extractor_provider=extractor_provider,
        shadow_provider=shadow_provider, has_key=has_key,
        efforts={"primary": primary_effort, "extractor": extractor_effort},
        models={"primary": model, "extractor": extractor_model})
    return snap, issues

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

# ── 設定來源遙測(第十一輪 P2-1)────────────────────────────────────────
#: **每一個會改變 LLM 行為的開關,以及 workflow 怎麼供應它。**
#:
#: `("variable", 預設)` = `${{ vars.X || '預設' }}`,使用者可覆寫;
#: `("fixed", 值)`      = workflow 寫死,repo variable **改不動**。
#:
#: 為什麼需要這張表。workflow 一律在 YAML 就用 `|| 預設` 補齊,程式看到的
#: 永遠是最終值 —— 分辨不出「使用者明設」與「走預設」。批#118 把 DeepSeek
#: 預設改成 max 之後,manifest 仍答不出「max 是誰決定的」。
#:
#: 原本只有一條分號字串帶四個鍵,而真正會改變行為的有十七個。兩個具體後果
#: 在寫這張表時才浮出來:`DEEPSEEK_MODEL` 是**寫死**的(設 repo variable 沒有
#: 用),而 `LLM_SHADOW_REASONING_EFFORT` 程式讀了、workflow 卻沒傳 ——
#: 設了也**靜默無效**。這正是「只記四個鍵」看不見的那一類。
#:
#: 這張表由 `tests/test_workflow_contract.py` 與 workflow 的 env 區塊做
#: **雙向**比對:少一個、多一個、預設值漂移,都會紅。
CONFIG_SOURCE_SPEC = {
    # 2026-08-08 單一模型架構:主分析 = deepseek-v4-flash(特化結構化路徑,
    # 失敗落回 legacy prompt,再落回 Gemini 備援)。影子比較與 Luna/OpenAI
    # 實驗已整批拆除 —— 開關表跟著縮,而不是留一排永遠為空的鍵。
    "LLM_PROVIDER":               ("variable", "deepseek"),
    "EXTRACTOR_PROVIDER":         ("variable", ""),
    "DEEPSEEK_REASONING_EFFORT":  ("variable", "max"),
    "LLM_TOTAL_TIMEOUT_SECONDS":  ("variable", ""),
    "LLM_REQUEST_TIMEOUT_SECONDS": ("variable", ""),
    #: 空 = 依 provider 自動選(deepseek → 特化;見 _DEFAULT_PROFILE_BY_PROVIDER)。
    #: 留著它是**逃生門**:設 deepseek_legacy_v1 即回舊路,不必 revert 程式碼。
    "LLM_PRIMARY_PROMPT_PROFILE":  ("variable", ""),
    # 2026-08-13:主分析改 v4-pro(使用者指定;V4-Pro-0813)。抽取器仍 flash。
    "DEEPSEEK_MODEL":             ("fixed", "deepseek-v4-pro"),
    "DEEPSEEK_EXTRACTOR_MODEL":   ("fixed", "deepseek-v4-flash"),
    "DEEPSEEK_BASE_URL":          ("fixed", "https://api.deepseek.com"),
    "LLM_EVENT_EXTRACTION":       ("fixed", "1"),
    "GEMINI_MODEL":               ("fixed", "gemini-2.5-flash"),
    "CLAUDE_MODEL":               ("fixed", "claude-sonnet-4-6"),
}

#: 只有 `variable` 的鍵需要把**原始值**傳進來(`fixed` 的來源已經寫在上表)。
CONFIG_RAW_KEYS = tuple(k for k, (kind, _) in CONFIG_SOURCE_SPEC.items()
                        if kind == "variable")


def parse_config_raw(raw: str) -> dict:
    """把 workflow 傳來的 `k=v;k=v` 拆成 dict(值可能是空字串)。

    r1(Codex,#2):**值不得 strip。** GitHub Actions 的 `${{ vars.X || '預設' }}`
    把 whitespace-only 視為 truthy —— `LLM_PROVIDER=" "` 走的是 repo variable
    那條路。這裡若順手 strip,`" "` 就和「真的沒設」變成同一個值,manifest
    會回報 `workflow_default`,而實際上使用者設了一個壞掉的值。

    鍵仍然要 strip:YAML 的 `>-` 折行會在每個 `;` 之後補一個空格,而那個空格
    落在**鍵**前面(值後面緊接著就是 `;`),所以值本身是完整的。
    """
    out: dict = {}
    for chunk in str(raw or "").split(";"):
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            out[k.strip()] = v
    return out


def config_sources(raw: str, resolved: Optional[dict] = None) -> dict:
    """每個開關的 `{resolved, source, workflow_default}`(第十一輪 P2-1)。

    `source` 的四種值,**分得出「使用者明設成跟預設一樣」**:
      - `repo_variable`    :repo variable 有值 → 這是使用者的決定
      - `workflow_default` :variable 是**空字串** → 走 workflow 的 `|| 預設`
      - `workflow_fixed`   :workflow 寫死,repo variable 改不動
      - `unknown`          :workflow 沒把原始值傳進來(本機執行即如此)

    走文件化的預設**不是問題**,只是一個事實 —— 批#112 已經把它移出降級
    清單。這裡把事實記完整,取代原本那串人讀的訊息:訊息答不出
    「解析值是多少」,也沒有辦法對某一個鍵提問。
    """
    seen = parse_config_raw(raw)
    resolved = resolved or {}
    out: dict = {}
    for key, (kind, default) in CONFIG_SOURCE_SPEC.items():
        if kind == "fixed":
            source = "workflow_fixed"
        elif key in seen:
            # r1(Codex,#2):判準是「GitHub Actions 覺得它 truthy 嗎」,
            # 也就是**空字串**才算沒設。whitespace-only 是設了一個壞掉的值,
            # 那條路徑由 `validate_llm_config` 當成設定錯誤報出來。
            source = "repo_variable" if seen[key] != "" else "workflow_default"
        else:
            source = "unknown"
        entry = {"source": source}
        value = resolved.get(key)
        if value is not None:
            # 逾時是數值,其餘是字串;統一成字串好比對也好讀。
            entry["resolved"] = value if isinstance(value, str) else str(value)
        if source in ("workflow_default", "workflow_fixed"):
            entry["workflow_default"] = default
        out[key] = entry
    return out
