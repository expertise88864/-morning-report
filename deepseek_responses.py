# -*- coding: utf-8 -*-
"""**DeepSeek Responses API adapter**(外審 P1-2)。

## 為什麼不能繼續共用 `openai_responses.py`

2026-08-07 換主模型時,特化路徑的做法是「拿 OpenAI 的 adapter 換 base URL
與金鑰」。它**跑得起來**(本機實測 HTTP 200),但那不等於有契約:

  * DeepSeek 官方文件公開的是 **ChatCompletions / Anthropic** 兩種介面,
    思考模式用 top-level `thinking` 與 `reasoning_effort`,JSON 輸出用
    `response_format={"type":"json_object"}`,答案在
    `choices[].message.content`、思考在 `reasoning_content`;
    `/v1/responses` **不在那份文件裡**。
  * 那個 adapter 的說明白紙黑字寫著「Luna 專用」,而它的解析邏輯是照
    OpenAI 的 `phase` 語意寫的。
  * 唯一的路由測試自己造一個 OpenAI 形狀的假回應 —— 它證明不了
    DeepSeek 的生產契約。

所以這個模組把「DeepSeek 實際回什麼」變成**一份寫下來、測得到的東西**。

## 契約來源:實機捕獲,不是抄文件

`tests/fixtures/deepseek_responses_v1.json` 是 2026-08-08 用生產同一條
`build_payload`(含 32K strict schema、`effort=max`)對
`api.deepseek.com/v1/responses` 送出後**真實回應的去識別化版本**
(去掉 id / 時間戳 / instructions 原文 / schema 內容,並把答案文字縮短;
形狀、鍵名、巢狀結構、usage 欄位一字未改)。

實測到的形狀與 OpenAI 的差異:

  1. `output[0]` 是 `type="reasoning"`,內容在 `content[].type="reasoning_text"`
     —— OpenAI 那邊思考不會這樣進 `output`。這一塊**不是答案**,
     混進去就等於把思考過程當成 JSON 去解析。
  2. `output[1]` 是 `type="message"` 且**帶 `phase="final_answer"`**。
  3. `usage` 的欄位名與 OpenAI Responses 相同
     (`input_tokens` / `output_tokens` / `*_details`)。
  4. `reasoning.effort` 會回報**實際生效**的強度 —— 要求值與生效值分開看
     的規矩在這裡一樣適用。
  5. **strict schema 是指引不是保證**:官方 JSON 模式只保證「合法 JSON
     字串」,而且明說偶爾可能回空 content。實測也看過答案被
     ```json 圍欄包起來。這兩件事在這裡當成**契約的一部分**處理,
     不是當成意外。

## 這個模組刻意不碰網路

只做組裝與解析,純函式,離線測得到底。`requests.post` 留在主模組
(那裡才有金鑰、逾時預算與 manifest)。
"""
from __future__ import annotations

from typing import Optional

#: 端點路徑(**不含 base url**;base 由呼叫端設定決定)。
RESPONSES_PATH = "/v1/responses"

#: 收到指名它們的 400 時逐一移除重試,而不是整個請求作廢。
#: 這幾個都只影響可觀測性與成本,不影響分析品質 ——
#: 為了它們讓晨報斷掉是明顯錯誤的取捨。
#: `reasoning.context` 也列入:那是 OpenAI 的欄位,DeepSeek 未文件化,
#: 被拒時退掉即可(每天獨立一次判斷,沒有跨輪脈絡要保留)。
OPTIONAL_FIELDS = ("reasoning.summary", "reasoning.context",
                   "prompt_cache_options", "safety_identifier")

#: 不含任何個人資料的穩定識別碼(不得用收件者信箱/使用者名稱)。
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
    """組出請求主體。**形狀以 2026-08-08 實測會被接受的那一份為準。**

    `instructions` 與 `user_input` 分開是刻意的:前者是每天不變的穩定前綴
    (快取判準),後者是當日證據。串成一段會讓 cached input 永遠打不中。
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
        # **`text.format` 必須帶 `type`**:2026-08-08 實測少了它是 400
        # (`missing field 'type'`),而那個 400 不指名任何選配欄位,
        # 退讓迴圈救不了 —— 整份分析當場作廢。
        fmt = dict(response_format)
        fmt.setdefault("type", "json_schema")
        text["format"] = fmt
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
    """移除一個選配欄位,回**新的** payload(不就地改)。

    就地改會讓「重試前的 payload」與「送出的」變成同一個物件 ——
    manifest 記到的就會是退讓後的形狀,而不是原本要送的那個。
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


def strip_json_fence(text: str) -> str:
    """剝掉 ```json … ``` 圍欄(沒有就原樣回)。

    **這是契約的一部分,不是意外。** OpenAI 的 strict 模式保證裸 JSON;
    DeepSeek 官方 JSON 模式只保證「合法 JSON 字串」,而 schema 是指引。
    實測看過兩種都出現 —— 包裝問題不該被當成內容不合格。
    """
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    t = t.split("\n", 1)[1] if "\n" in t else t[3:]
    if t.rstrip().endswith("```"):
        t = t.rstrip()[:-3]
    return t.strip()


def extract_output(response: Optional[dict]) -> dict:
    """取出**最終答案**、拒答、狀態與未完成原因。

    回 `{text, refusal, status, incomplete_reason, had_commentary,
    empty_content}`。

    兩個 DeepSeek 專屬的坑:

      * `output[0]` 是 `type="reasoning"`(內容 `reasoning_text`)——
        它**不是答案**,混進去就是拿思考過程當 JSON 解析。
        這裡只認 `type="message"`,與那一項天然分開。
      * `empty_content`:官方明說 JSON 模式偶爾回空 content。
        「回了但沒東西」與「沒回」對呼叫端是不同的處置(前者該修補、
        後者是傳輸問題),所以分成獨立旗標而不是讓它長得像解析失敗。

    `phase` 的處理與 OpenAI 同語意:有 `final_answer` 就只取它,
    沒有標記才退回全部串接 —— 把旁白混進 JSON 會讓解析失敗,
    而失敗的樣子是「模型不聽話」,實際上是我們讀錯了。
    """
    out = {"text": "", "refusal": "", "status": "",
           "incomplete_reason": "", "had_commentary": False,
           "empty_content": False}
    if not isinstance(response, dict):
        return out
    out["status"] = str(response.get("status") or "")
    inc = response.get("incomplete_details")
    if isinstance(inc, dict):
        out["incomplete_reason"] = str(inc.get("reason") or "")

    # 第二十五輪 P2-1:**commentary 永遠不能當 final 的替補。**
    # 上一版是 `finals if finals else others`,而 `others` 裡混著
    # commentary —— 「有 commentary、final 是空字串」時 `finals` 是空的,
    # 於是 commentary 被當成答案送下去,`empty_content` 也跟著變 false。
    # 判準改成看**有沒有出現過 final_answer 這個階段**,不是看它有沒有字。
    finals, unphased, commentary, refusals = [], [], [], []
    saw_message = False
    saw_final_phase = False
    for item in (response.get("output") or []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        saw_message = True
        phase = str(item.get("phase") or "")
        if phase == "commentary":
            out["had_commentary"] = True
            bucket = commentary
        elif phase == "final_answer":
            saw_final_phase = True
            bucket = finals
        else:
            bucket = unphased          # 沒標階段的 message 才是合法替補
        for part in (item.get("content") or []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and part.get("text"):
                bucket.append(str(part["text"]))
            elif part.get("type") == "refusal" and part.get("refusal"):
                refusals.append(str(part["refusal"]))

    raw = "".join(finals) if saw_final_phase else "".join(unphased)
    out["text"] = strip_json_fence(raw)
    out["refusal"] = "\n".join(refusals)
    # 有 message 但一個字都沒有 —— 官方點名過的情況,要能與「沒有 message」
    # (通常是 incomplete/被截斷)分辨開。
    out["empty_content"] = bool(saw_message and not raw.strip() and not refusals)
    return out


def reasoning_text(response: Optional[dict]) -> str:
    """思考內容(**只供遙測,不進信件**)。取不到回空字串。

    DeepSeek 把它放進 `output[].type="reasoning"` 的
    `content[].type="reasoning_text"` —— 與答案是不同的 output 項。
    """
    if not isinstance(response, dict):
        return ""
    bits = []
    for item in (response.get("output") or []):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        for part in (item.get("content") or []):
            if isinstance(part, dict) and part.get("type") == "reasoning_text":
                bits.append(str(part.get("text") or ""))
    return "\n".join(b for b in bits if b)


def applied_effort(response: Optional[dict]) -> str:
    """回應**實際套用**的推理強度(從回應讀,不是回報我們送的那個)。

    2026-08-01 的教訓:模型拒絕某個強度時會靜默退回預設,而 manifest
    顯示的是我們要求的值 —— 看起來像有生效。要求值與生效值必須分開。
    """
    if not isinstance(response, dict):
        return ""
    r = response.get("reasoning")
    if isinstance(r, dict) and r.get("effort"):
        return str(r["effort"])
    return ""


def normalize_usage(usage: Optional[dict]) -> dict:
    """把 usage 轉成既有下游看得懂的形狀(欄位名與 OpenAI Responses 相同,
    實測 2026-08-08 確認)。

        input_tokens                            → prompt_tokens
        output_tokens                           → completion_tokens
        input_tokens_details.cached_tokens      → prompt_tokens_details.cached_tokens
        output_tokens_details.reasoning_tokens  → completion_tokens_details.reasoning_tokens

    **沒有的欄位不要造。** 缺 `cached_tokens` 與「cached_tokens = 0」是兩件
    事:前者是這個回應沒說,後者是真的沒命中。填 0 會讓成本看起來精確,
    而它其實是猜的。
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
    """可見輸出 = 總輸出 − 推理;兩者都缺回 None(不猜)。

    只看總輸出的話,「推理很多但答案很短」與「推理很少但答案很長」
    長得一模一樣,而那是兩種完全不同的模型行為 ——
    2026-08-02 那天正是前者(6,757 推理 / 243 答案),政策解析寫到一半斷掉。
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
