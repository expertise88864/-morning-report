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

import re
from typing import Optional

# 計價已抽成獨立模組;這裡 re-export 維持既有呼叫端不變。
from llm_pricing import (  # noqa: F401
    CACHE_WRITE_MULTIPLIER, LONG_CONTEXT_TIERS, MODEL_PRICING,
    PRICING_AS_OF, PRICING_SCHEMA, PRICING_SOURCE,
    PRICING_SOURCE_BY_PREFIX, cache_write_tokens_of,
    cached_tokens_of, estimate_cost, long_context_tier,
    price_of, pricing_source_for)

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
    # Gemini。出處:Google AI for Developers 的 Models 頁(2026-08-17 查),
    # **文件值不是實測值** —— 這裡收的是「不要送超過它會接受的數字」,
    # 用途是把請求夾在上限內,不是宣稱模型一定吐得出這麼多。
    # `gemini-2.5-flash-lite` 由 `startswith` 落在下面這一列 —— **這是刻意的**
    # (同族的輸出上限,文件同一頁),不是前綴巧合。
    # `gemini-2.0-flash` 已**連同這張表一起移除**(repo-wide 外審 P3):
    # 端點 2026-06-01 退役、runtime 降級鏈早已拿掉 —— registry 留著它,
    # 未來有人看到表裡「支援」就可能重新放回 runtime。真要復活必須連同
    # 出處一起重新查證(本表自己的規則)。
    "gemini-2.5-flash": {"max_output": 65_536},
    "gpt-5.6-sol": {"max_output": 128_000, "context": 1_050_000},
    "gpt-5.6-terra": {"max_output": 128_000, "context": 1_050_000},
    # `efforts` 是**實測**,不是抄文件(批#105)。官方 Models 頁面把 luna 的
    # 推理強度列成 none…max,但 2026-08-01 金絲雀在 chat/completions 上實測:
    #     Unsupported value: 'reasoning_effort' does not support 'max' with
    #     this model. Supported values are: 'none','low','medium','high','xhigh'.
    # 生產那一班因此靜默退回 provider 預設(reasoning 379 vs xhigh 的 23,095),
    # 使用者以為在測 max、其實在測預設。**文件是宣稱,端點才是事實。**
    "gpt-5.6-luna": {"max_output": 128_000, "context": 1_050_000,
                     "efforts": ("none", "low", "medium", "high", "xhigh")},
    # DeepSeek 官方「思考模式」文件(2026-08-01 使用者提供)。
    # **請求值與實際生效值不是同一件事** —— v4-pro 的映射是:
    #     low → high、high → high、xhigh → max、max → max
    # 也就是說先前一直用的 `high` 其實只到中段;要最高推理必須送 `xhigh`/`max`。
    # (文件另註:2026 年 8 月初會更新 v4-pro 的映射 —— 這張表要盯著。)
    # **只收 `efforts`** —— 那份文件講的是思考模式,沒有給 max output 與
    # context。我第一版順手填了 8,192 / 128,000,那是推測不是出處,
    # 違反本表自己的規則(「說得出數字從哪來」)。沒有的就不填。
    # `max_output` 出處:DeepSeek Models & Pricing 文件(2026-08-02 查證)——
    # v4-pro 最大輸出 384,000 token、context 1M。
    # **先前這裡刻意留空**(「說得出數字從哪來」),而留空的代價在 2026-08-02
    # 那班具體化了:`_call_deepseek` 送寫死的 7,000,批#118 把推理改成 max 之後
    # 6,757 個 token 進了推理,答案只剩 243 個 —— 政策解析寫到一半就斷。
    "deepseek-v4-pro": {"max_output": 384_000, "context": 1_000_000,
                        "efforts": ("none", "low", "medium", "high",
                                    "xhigh", "max")},
    # `max_output`/`context` 出處:DeepSeek Models & Pricing 頁(2026-08-07
    # 使用者提供)—— flash 最大輸出 384K、context 1M、Responses API(僅 flash)、
    # 思考模式預設開啟。先前這裡刻意留空,於是 `max_output_for` 回保守的
    # 16,000;flash 在 max 推理下 reasoning 就可能吃掉上萬 token,16K 的
    # 上限會讓答案被截斷 —— 與批#118 在 v4-pro 上踩過的是同一型。
    "deepseek-v4-flash": {"max_output": 384_000, "context": 1_000_000,
                          # medium 是合法設定(映為 high)—— 驗設定的那關不得擋它
                          "efforts": ("none", "low", "medium", "high",
                                      "xhigh", "max")},
}

#: 沒收錄的模型用**保守**上限,不是樂觀的。理由不對稱:
#: 額度給得比真實上限低,最壞是輸出被截斷(有 `finish_reason=length` 可偵測,
#: 而且有減量重試);給得比真實上限高,是當場 400、整份分析作廢。
#: 要放寬就把那個模型連同出處加進 `MODEL_LIMITS` —— 那是一個要有人查過的動作。
UNKNOWN_MODEL_MAX_OUTPUT = 16_000

#: 舊常數保留給沒有指名模型的呼叫端(它是**粗略上界**,不是契約)。
DEFAULT_MAX_OUTPUT = 128_000


def supported_efforts(model: str) -> Optional[tuple]:
    """這個模型**實測**支援哪些推理強度;沒量過就回 None(不猜)。"""
    m = (model or "").strip().lower()
    for name, spec in MODEL_LIMITS.items():
        if (m == name or m.startswith(name)) and spec.get("efforts"):
            return spec["efforts"]
    return None


def max_output_for(model: str) -> tuple:
    """(輸出上限, 出處)。未收錄的模型回保守值並明說沒有出處。"""
    m = (model or "").strip().lower()
    for name, spec in MODEL_LIMITS.items():
        # 一個模型可以有**文件化的推理強度**卻沒有文件化的輸出上限
        # (DeepSeek 的思考模式文件就是如此)—— 那時仍走保守值,不猜。
        if (m == name or m.startswith(name)) and "max_output" in spec:
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


#: 請求在**被處理之前**就遭拒的狀態碼 → 原因代號。
#: 402 帳戶餘額不足、401/403 金鑰無效或沒有權限 —— server 沒有做任何
#: 推理,所以**不會計費**,而且**重試不會好**:要有人去儲值或換金鑰。
#: 逾時與連線中斷刻意不在此列:那些是 server 已經收下請求之後才斷,
#: 照樣計費(`billable_unmeasured` 正是為它們而存在)。
#: **只放 provider 契約推得出來的**(2026-08-26 外審 P2)。
#: 這張表的下游做四件事:不重試、不換模型、不計費、叫使用者去處理帳號。
#: 401(Authentication Fails)與 402(Insufficient Balance)在 DeepSeek 的
#: 官方錯誤表裡有明確語意;**403 不在那張表上**(400/401/402/422/429/
#: 500/503)。把 403 當成「金鑰失效」等於宣告一件證明不了的事:它也可能
#: 是某個 endpoint 或模型的權限問題 —— 那時金鑰完全有效,而系統會停掉
#: 所有還能用的模型,並告訴使用者去換一把沒有壞的金鑰。
#: 未知的 403 走一般失敗路徑(可重試、可換模型),與其他沒見過的狀態碼
#: 一視同仁 —— 那是**保守**的方向:多試一次的代價,遠小於誤停整條路徑。
_REFUSED_BEFORE_WORK = {401: "auth", 402: "payment"}

#: 訊息裡的狀態碼寫法:requests 的 `402 Client Error: ...`、
#: 本專案 chat 路徑自己組的 `HTTP 402: {...}`。
_STATUS_IN_TEXT = re.compile(r"(?:HTTP\s+|\b)(\d{3})(?=\s*(?::|Client Error|"
                             r"Server Error))")


def refusal_reason(exc_or_text) -> str:
    """這次失敗是「請求被拒」還是「做到一半斷掉」→ `"payment"`/`"auth"`/`""`。

    兩者**處置完全不同**:被拒要有人去處理帳號(重試幾次都一樣),
    斷線隔天多半自己好;而且被拒**不會計費**,記成計費會讓成本帳
    憑空多出一筆(2026-08-15 生產:DeepSeek 402 餘額不足,manifest
    卻寫「已送出但沒有 usage —— 那些仍會計費」)。
    """
    exc = exc_or_text
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(code, int):
        m = _STATUS_IN_TEXT.search(str(exc or ""))
        code = int(m.group(1)) if m else 0
    return _REFUSED_BEFORE_WORK.get(code, "")


#: 成本紀錄要帶的計價中繼資料。**只記總額,對不上帳單時就分不出原因。**
_PRICING_FIELDS = ("pricing_tier", "pricing_schema", "effective_input_rate",
                   "effective_cached_rate", "effective_output_rate",
                   "pricing_source")


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
        # r1(Codex,#3):**生效費率要真的進到紀錄裡。** 我在上一個 commit
        # 寫下「只記總額的話,對不上帳單時分不出是用錯費率還是漏了呼叫」,
        # 然後把那些欄位留在 `estimate_cost()` 的回傳值裡沒有帶出來 ——
        # 宣稱與實作又差一層,而差的那一層正好是宣稱要解決的問題。
        rec.update({k: cost[k] for k in _PRICING_FIELDS if k in cost})
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
    # **混合費率的總額不得掛單一標籤**(外審 r1,P2):同一角色的兩次
    # 呼叫可以跨過 DeepSeek 的峰谷邊界,而其餘欄位一律「取最新」——
    # 於是一筆尖峰+離峰的合計會整個被標成其中一種,對帳時分不出來。
    # 逐時段的金額留一份;`pricing_tier` 在混合時明說 `mixed`。
    _prev_tier = str(prev.get("pricing_tier") or "")
    _now_tier = str(out.get("pricing_tier") or "")
    if _prev_tier and _now_tier:
        _by = dict(prev.get("cost_by_tier") or {})
        if not _by and prev.get("estimated_cost_usd") is not None:
            _by[_prev_tier] = prev["estimated_cost_usd"]
        _mine = record.get("estimated_cost_usd")
        if _mine is not None:
            _by[_now_tier] = round(_by.get(_now_tier, 0.0) + _mine, 6)
        if _by:
            out["cost_by_tier"] = _by
        if _prev_tier != _now_tier or _prev_tier == "mixed":
            out["pricing_tier"] = "mixed"
    return out


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
    # 第十輪 P1-2:**失敗但有 usage 的嘗試同樣計費。**
    # `finish_reason=length`、結構化輸出不合格、報告層驗收未過 —— 這些呼叫
    # API 都已經生成內容並收費,原本完全不進總額,成本因此系統性低估。
    failed_measured = 0.0
    for a in (slot.get("attempts") or []):
        if isinstance(a, dict) and isinstance(a.get("estimated_cost_usd"),
                                              (int, float)):
            failed_measured += a["estimated_cost_usd"]
            measured += 1
    total += failed_measured
    billed_unknown = [a for a in (slot.get("attempts") or [])
                      if isinstance(a, dict) and a.get("billable_unmeasured")]
    out = {"total_usd": round(total, 6), "measured_calls": measured,
           "failed_attempt_cost_usd": round(failed_measured, 6),
           "unmeasured_billable_calls": len(billed_unknown),
           "pricing_as_of": PRICING_AS_OF, "pricing_schema": PRICING_SCHEMA}
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


# ── DeepSeek 思考模式的 provider contract(第十一輪 P1-2 / P2-2)──────────
# 官方文件把**兩件事分成兩個欄位**,而本 repo 原本混成一個:
#   思考模式開關:{"thinking": {"type": "enabled"/"disabled"}} —— **預設 enabled**
#   思考強度    :{"reasoning_effort": "low"/"high"/"max"}
#
# 後果是實際的語意錯誤:設 `off` 時我們**兩個都不送**,而思考模式預設是開的
# —— 所以「關閉思考」根本沒有關閉。要關必須明確送 `disabled`。
#
# 官方映射表(2026-08-13 文件改版查證,flash 與 pro **同一張表**):
#   low → low、medium → high、high → high、xhigh → high、max → max。
# 合法送出值是 low/high/max;`medium`/`xhigh` 不是送出值,依官方表翻成
# high 再送。**舊表把 low 升成 high、xhigh 升成 max** —— 那會默默放大
# 使用者的成本設定;新文件有真的 low 檔、xhigh 也只到 high。
#
# 這是**唯一定義處**:workflow 註解與 README 不再各自手寫一份規格
# (原本寫在兩處,批#118 只改了其中一份,另一份還留著「high/medium/low」)。
_DEEPSEEK_OFF = ("off", "none", "disabled")
_DEEPSEEK_TO_LOW = ("low",)
_DEEPSEEK_TO_HIGH = ("medium", "high", "xhigh")
_DEEPSEEK_TO_MAX = ("max",)


def deepseek_thinking(raw: str) -> dict:
    """把設定值翻成 DeepSeek 實際要送的兩個欄位。

    回 `{"thinking": dict|None, "reasoning_effort": str|None,
         "canonical": str, "known": bool}`。
    `known=False` 代表文件沒列這個值 —— 呼叫端該把它當成設定問題,
    而不是安靜地送出去。
    """
    v = (raw or "").strip().lower()
    if v in _DEEPSEEK_OFF:
        # **必須明確送 disabled。** 不送 = 沿用預設 = 思考仍然開著。
        return {"thinking": {"type": "disabled"}, "reasoning_effort": None,
                "canonical": "none", "known": True}
    if v in _DEEPSEEK_TO_LOW:
        return {"thinking": {"type": "enabled"}, "reasoning_effort": "low",
                "canonical": "low", "known": True}
    if v in _DEEPSEEK_TO_HIGH:
        return {"thinking": {"type": "enabled"}, "reasoning_effort": "high",
                "canonical": "high", "known": True}
    if v in _DEEPSEEK_TO_MAX:
        return {"thinking": {"type": "enabled"}, "reasoning_effort": "max",
                "canonical": "max", "known": True}
    return {"thinking": {"type": "enabled"}, "reasoning_effort": "high",
            "canonical": "high", "known": False}
