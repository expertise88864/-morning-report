# -*- coding: utf-8 -*-
"""**OpenAI Responses API adapter**(Phase 3,Luna 專用路徑)。

## 為什麼要獨立一個 adapter

DeepSeek 與備援仍走 `chat/completions`;Luna 走 `responses`。兩者**不是換個
網址就好**,而是三處形狀都不同:

  1. 請求:`instructions` / `input` / `reasoning.effort` / `text.format`
     對上 `messages` / `reasoning_effort` / `response_format`
  2. 回應:文字埋在 `output[].content[].text`,而不是 `choices[0].message.content`
  3. **usage 欄位名完全不同** —— `input_tokens` / `output_tokens` /
     `input_tokens_details.cached_tokens` / `output_tokens_details.reasoning_tokens`

第 3 點是最危險的:既有的成本估算與紀錄組裝全部讀 chat/completions 的欄位名。
沿用它們的話,Luna 那一班的 token 會全部讀成 None → **成本靜默記成 0**,
而十天實驗的結論正是建立在成本數字上。所以本模組把 usage **正規化**成既有的
形狀,而不是去改五個下游解析器 —— 改下游要動到 DeepSeek 也在走的路徑。

## 本模組刻意不碰網路

只做 payload 組裝與回應解析,都是純函式。實際的 `requests.post` 留在主模組
(那裡才有金鑰、逾時預算與 manifest)。這樣這一整段可以離線測到底,
而 Responses API 的形狀錯誤本來就該在離線就被抓到 —— 它的症狀是 400,
而 400 在這條路徑上等於整份分析作廢。

出處:OpenAI API reference / Structured Outputs / Prompt caching / Reasoning
文件,2026-08-01 查證。
"""
from __future__ import annotations

from typing import Optional

#: 端點路徑。**不含 base url** —— base 由呼叫端的設定決定。
RESPONSES_PATH = "/v1/responses"

#: 這些是「加了會更好、但不保證每個帳號都能用」的選配欄位。
#: 收到指名它們的 400 時逐一移除重試,而不是整個請求作廢。
#:
#: `reasoning.summary` 官方明說**需要組織驗證**;`prompt_cache_options` 是
#: GPT-5.6+ 才有。兩者都不影響分析品質,只影響可觀測性與成本 ——
#: 為了它們讓晨報斷掉是明顯的錯誤取捨。
OPTIONAL_FIELDS = ("reasoning.summary", "prompt_cache_options", "safety_identifier")

#: 一個**不含任何個人資料**的穩定識別碼。
#: 官方建議帶 safety identifier 以改善濫用偵測的歸屬;它要穩定、又不能是
#: 收件者信箱或使用者名稱 —— 那會把個資送到第三方。這裡用固定的專案代號。
SAFETY_IDENTIFIER = "morning-report-tw"


def build_payload(*, model: str, instructions: str, user_input: str,
                  effort: str = "", verbosity: str = "high",
                  response_format: Optional[dict] = None,
                  max_output_tokens: Optional[int] = None,
                  store: bool = False,
                  reasoning_summary: str = "auto",
                  reasoning_context: str = "current_turn",
                  prompt_cache_key: str = "",
                  prompt_cache_ttl_seconds: Optional[int] = None) -> dict:
    """組出 Responses API 的請求主體。

    `instructions` 與 `user_input` 分開是刻意的:前者是每天不變的穩定前綴
    (快取判準),後者是當日證據。把它們串成一段會讓 cached input 永遠打不中,
    而 cached 與 uncached 的費率差十倍。

    `reasoning.context` 明設 `current_turn`:GPT-5.6 **預設是 `all_turns`**,
    而晨報每天是獨立的一次判斷,帶進前一天的推理只會多花錢又汙染結論。
    """
    payload: dict = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "store": bool(store),
        "safety_identifier": SAFETY_IDENTIFIER,
    }
    reasoning: dict = {}
    if effort:
        reasoning["effort"] = effort
    if reasoning_summary:
        reasoning["summary"] = reasoning_summary
    if reasoning_context:
        reasoning["context"] = reasoning_context
    if reasoning:
        payload["reasoning"] = reasoning

    text: dict = {}
    if verbosity:
        text["verbosity"] = verbosity
    if response_format:
        text["format"] = response_format
    if text:
        payload["text"] = text

    if max_output_tokens:
        payload["max_output_tokens"] = int(max_output_tokens)
    if prompt_cache_key:
        payload["prompt_cache_key"] = prompt_cache_key
        if prompt_cache_ttl_seconds:
            payload["prompt_cache_options"] = {"ttl": int(prompt_cache_ttl_seconds)}
    return payload


def drop_field(payload: dict, dotted: str) -> dict:
    """移除一個選配欄位,回傳**新的** payload(不就地改)。

    就地改會讓「重試前的 payload」與「送出的 payload」變成同一個物件 ——
    那時 manifest 記到的會是退讓後的形狀,而不是原本要送的那個。
    """
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in payload.items()}
    if "." in dotted:
        parent, child = dotted.split(".", 1)
        block = out.get(parent)
        if isinstance(block, dict):
            block.pop(child, None)
            if not block:
                out.pop(parent, None)
    else:
        out.pop(dotted, None)
    return out


def normalize_usage(usage: Optional[dict]) -> dict:
    """把 Responses 的 usage 轉成既有下游看得懂的形狀。

    對照表(官方文件,2026-08-01):
        input_tokens                              → prompt_tokens
        output_tokens                             → completion_tokens
        input_tokens_details.cached_tokens        → prompt_tokens_details.cached_tokens
        input_tokens_details.cache_write_tokens   → prompt_tokens_details.cache_write_tokens
        output_tokens_details.reasoning_tokens    → completion_tokens_details.reasoning_tokens

    **沒有的欄位就不要造。** 缺 `cached_tokens` 與「cached_tokens = 0」是兩件
    不同的事:前者是這個回應沒告訴我們,後者是真的沒命中快取。把缺失填成 0
    會讓成本看起來精確,而它其實是猜的。
    """
    if not isinstance(usage, dict):
        return {}
    out: dict = {}
    if isinstance(usage.get("input_tokens"), int):
        out["prompt_tokens"] = usage["input_tokens"]
    if isinstance(usage.get("output_tokens"), int):
        out["completion_tokens"] = usage["output_tokens"]
    if isinstance(usage.get("total_tokens"), int):
        out["total_tokens"] = usage["total_tokens"]

    idet = usage.get("input_tokens_details")
    if isinstance(idet, dict):
        pdet = {}
        for src in ("cached_tokens", "cache_write_tokens"):
            if isinstance(idet.get(src), int):
                pdet[src] = idet[src]
        if pdet:
            out["prompt_tokens_details"] = pdet

    odet = usage.get("output_tokens_details")
    if isinstance(odet, dict) and isinstance(odet.get("reasoning_tokens"), int):
        out["completion_tokens_details"] = {
            "reasoning_tokens": odet["reasoning_tokens"]}
    return out


def visible_output_tokens(usage: Optional[dict]) -> Optional[int]:
    """可見輸出 = 總輸出 − 推理。兩者都缺就回 None(不猜)。

    十天實驗要分開看「花在推理」與「花在答案」的 token ——
    只看總輸出的話,「推理很多但答案很短」與「推理很少但答案很長」
    會長得一模一樣,而那是兩種完全不同的模型行為。
    """
    if not isinstance(usage, dict):
        return None
    total = usage.get("output_tokens")
    det = usage.get("output_tokens_details")
    reasoning = det.get("reasoning_tokens") if isinstance(det, dict) else None
    if not isinstance(total, int):
        return None
    if not isinstance(reasoning, int):
        return total
    return max(0, total - reasoning)


def extract_output(response: Optional[dict]) -> dict:
    """從 Response 物件取出**最終答案**、拒答與未完成原因。

    `{text, refusal, status, incomplete_reason, had_commentary}`

    **`phase` 這個欄位是這裡的陷阱。** GPT-5.6 的 output message 可以標成
    `commentary` 或 `final_answer`;把所有 `output_text` 串起來會把旁白混進
    JSON,strict 解析就會失敗 —— 而失敗的樣子是「模型不聽話」,實際上是
    我們讀錯了。所以:有 `final_answer` 就只取它,沒有標記才退回全部串接。
    """
    out = {"text": "", "refusal": "", "status": "",
           "incomplete_reason": "", "had_commentary": False}
    if not isinstance(response, dict):
        return out
    out["status"] = str(response.get("status") or "")
    inc = response.get("incomplete_details")
    if isinstance(inc, dict):
        out["incomplete_reason"] = str(inc.get("reason") or "")

    finals, others, refusals = [], [], []
    for item in (response.get("output") or []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        phase = str(item.get("phase") or "")
        if phase == "commentary":
            out["had_commentary"] = True
        bucket = finals if phase == "final_answer" else others
        for part in (item.get("content") or []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and part.get("text"):
                bucket.append(str(part["text"]))
            elif part.get("type") == "refusal" and part.get("refusal"):
                refusals.append(str(part["refusal"]))

    out["text"] = "".join(finals) if finals else "".join(others)
    out["refusal"] = "\n".join(refusals)
    return out


def applied_effort(response: Optional[dict]) -> str:
    """回應**實際套用**的推理強度(從回應本身讀,不是回報我們送的那個)。

    2026-08-01 的教訓:`gpt-5.6-luna` 拒絕 `max` 時會靜默退回 provider 預設,
    manifest 卻顯示我們要求的值 —— 看起來像有生效。要求值與生效值必須分開。
    """
    if not isinstance(response, dict):
        return ""
    r = response.get("reasoning")
    if isinstance(r, dict) and r.get("effort"):
        return str(r["effort"])
    return ""


def reasoning_summary_text(response: Optional[dict]) -> str:
    """推理摘要(**只供遙測,不進信件**)。取不到就回空字串。

    這是 opt-in 且需要組織驗證的功能;拿不到不是錯誤。
    """
    if not isinstance(response, dict):
        return ""
    parts = []
    for item in (response.get("output") or []):
        if isinstance(item, dict) and item.get("type") == "reasoning":
            for s in (item.get("summary") or []):
                if isinstance(s, dict) and s.get("text"):
                    parts.append(str(s["text"]))
    return "\n".join(parts)
