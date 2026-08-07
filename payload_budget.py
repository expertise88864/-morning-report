# -*- coding: utf-8 -*-
"""**送得出去的 payload 有多大**(2026-08-05 實機根因)。

## 這個模組存在的理由

2026-08-05 的實機紀錄:

    estimated_input_tokens = 1,110,589
    error = "429 Client Error: Too Many Requests"
    stage = analysis
    elapsed = 2.7s

先前兩天的 `TypeError` 已經修掉了 —— packet 組得起來、sha 算得出來、
請求送得出去。**新的擋路石是請求本身太大**:約 2.0 MB 文字、
估算 111 萬 token。2.7 秒就被拒,是「這個請求連進佇列的資格都沒有」。

## 為什麼會這麼大

新聞側**有上限**(220 則 × 摘要 600 + 全文 1500 ≈ 46 萬字元),
而 `market` 的外部文字區塊 —— 公報全文、結構化事件、政策情報、歷史 ——
**一個上限都沒有**。它們每天照抓多少放多少,而沒有人量過總和。

那正是這個 repo 反覆栽的形狀:**每一塊都有人負責,總和沒有人負責。**

## 判準

按**每個區塊的字元成本**由大到小裁,而且只裁「診斷與背景」類 ——
行情數字、新聞、張力是分析的原料,裁掉它們等於改變結論。
裁掉什麼、裁了多少,全部記進 manifest:**靜默截斷會讓「今天沒有公報」
與「公報被裁掉了」長得一模一樣。**
"""
from __future__ import annotations

import json
from typing import Optional

#: 送出去的 payload 字元上限。**量出來的**:2026-08-05 實機約 2.0M 字元
#: (估 111 萬 token)被 429 拒收;而前一天成功送出的 legacy 請求約
#: 9.5 萬 token。原訂 600K 字元(保守起點)。
#:
#: 2026-08-07 放寬到 1M:主模型換 deepseek-v4-flash(context 1M token、
#: 費用約 $0.14/M input,使用者明示 token 成本可忽略)。實測字元→token
#: 比約 0.6,1M 字元 ≈ 60 萬 token,加上 112K 輸出額度仍在 1M context 內。
#: 在 600K 之下,公報/結構化事件/模型監控**每天**被整塊裁掉
#: (2026-08-07 E2E:2.26M 裁到 500K)—— 放寬後只有 HISTORY 這類
#: 巨型序列出局,其餘背景區塊進得了特化 prompt。
MAX_PAYLOAD_CHARS = 1_000_000

#: 可以裁的區塊,**由裁的先後順序排列**。全部是背景與診斷 ——
#: 行情數字(QQQ/TAIFEX/BREADTH/SECTOR_HEAT…)、新聞、張力**不在此列**,
#: 它們是分析的原料,裁掉等於改變結論而不是縮小輸入。
TRIMMABLE_BLOCKS = (
    "HISTORY",                  # 歷史序列:最大、對當日判斷最間接
    "GAZETTE_RECORDS",          # 公報全文
    "STRUCTURED_NEWS_EVENTS",   # 結構化事件(新聞側已另有全文)
    "EVENT_TIMELINE",
    "POLICY_NEW_KEYWORDS",
    "SEC_FILINGS",
    "MODEL_WALK_FORWARD",       # 模型回測序列
    "MODEL_MONITORING",
)


def _size(node) -> int:
    """一個節點序列化後佔多少字元。"""
    try:
        return len(json.dumps(node, ensure_ascii=False, default=str))
    except (TypeError, ValueError):      # pragma: no cover - default=str 已保護
        return len(str(node))


def block_sizes(packet: Optional[dict]) -> dict:
    """`{區塊: 字元數}`,由大到小。**先量再裁** —— 這個 repo 的規矩。"""
    market = ((packet or {}).get("market") or {})
    out = {f"market.{k}": _size(v) for k, v in market.items()}
    for key in ("news", "signal_tensions", "news_clusters", "tw_universe"):
        if key in (packet or {}):
            out[key] = _size(packet[key])
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def trim(packet: Optional[dict], *, limit: int = MAX_PAYLOAD_CHARS) -> tuple:
    """`(裁過的 packet, 裁切報告)`。**不改變輸入**(回新的 dict)。

    只裁 `TRIMMABLE_BLOCKS`,而且**整塊拿掉**而不是截斷內容 ——
    半截的公報比沒有公報更糟:模型會照著半句話推論,而它讀不出那是半句。
    每一塊被拿掉時留下 `{"omitted_for_size": 字元數}`,
    讓「今天沒有」與「被裁掉了」在下游分得開。
    """
    pk = dict(packet or {})
    before = _size(pk)
    report = {"chars_before": before, "limit": limit, "trimmed": [],
              "chars_after": before, "over_budget": False}
    if before <= limit:
        return pk, report

    market = dict(pk.get("market") or {})
    # 第二十一輪 P2-3:**先量再排,不是照固定順序砍。** 上一版依
    # `TRIMMABLE_BLOCKS` 的宣告順序,可能先刪三個小而有用的區塊,
    # 最後才碰到那個真正巨大的 —— 而模組說明自己寫著「由大到小裁」。
    # **宣稱與實作又差一層。** 語意優先序留作同大小時的決勝。
    order = sorted(
        [n for n in TRIMMABLE_BLOCKS if n in market],
        key=lambda n: (-_size(market[n]), TRIMMABLE_BLOCKS.index(n)))
    for name in order:
        if _size(pk) <= limit:
            break
        cost = _size(market[name])
        # 第二十一輪 P1-3:**裁切標記不能留在 `market` 裡。**
        # registry 會把 `market.*` 的數字葉節點全部註冊成可引用、
        # 可推論的證據 —— 於是「裁掉了 185,230 字元」變成一個合法的
        # **量化錨點**。診斷資訊放診斷區,不放證據區。
        market.pop(name, None)
        pk["market"] = market
        report["trimmed"].append({"block": f"market.{name}", "chars": cost})
    pk["market"] = market
    if report["trimmed"]:
        # 被裁掉的區塊要變成**必須揭露的缺口** —— 不然收件人會以為
        # 今天沒有公報,而不是「公報沒有進到分析裡」。
        pk["required_disclosures"] = dict(
            pk.get("required_disclosures") or {},
            **{f"gap:payload_omitted:{t['block'].split('.', 1)[-1]}":
               f"這塊資料今天太大({t['chars']:,} 字元),沒有進到分析輸入"
               for t in report["trimmed"]})
    # 第二十二輪 P1-2:**`chars_after` 要在所有改動之後量** —— 上一版在
    # 加 disclosure 之前算,於是「599,950 + 300 字缺口 = 600,250」會被
    # 標成 over_budget=False,gate 錯誤放行,manifest 記的也不是真實大小。
    report["chars_after"] = _size(pk)
    report["over_budget"] = report["chars_after"] > limit
    return pk, report


#: 最終 request 的上限。packet 之外還有 developer instructions、
#: strict schema 與 API body 框架 —— **gate 擋的要是 provider 真正
#: 收到的東西**,packet 沒超不代表 request 沒超。
#: 2026-08-07 隨 MAX_PAYLOAD_CHARS 放寬(flash 1M context):
#: 1.1M 字元 ≈ 66 萬 token,加輸出額度仍留有餘裕。
MAX_REQUEST_CHARS = 1_100_000


def request_gate(body: dict, *, manifest=None,
                 limit: int = MAX_REQUEST_CHARS) -> None:
    """對**真正要送出去的 request body** 做最終檢查(外審 P1-4)。

    量的是整個 body 序列化之後的字元 —— 包含 `model` / `store` /
    `reasoning` / `text` / `max_output_tokens` / prompt-cache 欄位、
    外層 JSON 結構,以及**巢狀字串內的逃逸**(user payload 自己是一個
    JSON 字串,放進外層時引號、反斜線、換行都會再逃逸一次)。
    上一版只加總三段長度,這些全部漏算。

    **刻意不用 `ensure_ascii=True`**,雖然 `requests` 上線時是那樣送:
    這道閘門是 **token 的代理**(2026-08-05 的 429 訊息是
    `estimated_input_tokens = 1,110,589`),而 token 算在**解碼後**的
    內容上。中文在 ASCII 逃逸下會膨脹六倍(實測 5 萬中文字:
    100,030 → 600,033),照 wire 長度設限會讓晨報**每天**被自家閘門擋掉,
    擋的還不是真正的限制。分隔符沿用 `requests` 的預設。

    超標直接 `PayloadBudgetExceeded`(呼叫端落回 legacy,信照樣寄)。
    """
    try:
        chars = len(json.dumps(body or {}, ensure_ascii=False, default=str))
    except (TypeError, ValueError):     # pragma: no cover - default=str 已保護
        chars = len(str(body))
    if manifest is not None:
        manifest.setdefault("llm", {}).setdefault(
            "payload_budget", {})["final_request_chars"] = chars
    if chars > limit:
        import sys as _sys
        print(f"[llm] 最終 request {chars:,} 字元超過 {limit:,},"
              "放棄特化路徑", file=_sys.stderr)
        raise PayloadBudgetExceeded(
            f"final request over budget: {chars} > {limit}")


def apply(packet: Optional[dict], manifest: Optional[dict] = None) -> dict:
    """**預算政策的單一入口**:裁背景 → 第二層壓縮 → 記錄 → 硬閘門。

    收在這裡而不是攤在主模組,理由與 `trim` 的「總和沒有人負責」同一個:
    預算是一件事,不是散在呼叫端的四個步驟(那樣下一個呼叫端只會抄一半)。

    超標時 raise `PayloadBudgetExceeded`,呼叫端落回 legacy(晨報不可斷)。
    """
    import sys as _sys
    import payload_compact as _pc
    packet, budget = trim(packet)
    if manifest is not None:
        manifest.setdefault("llm", {})["payload_budget"] = budget
    if budget["trimmed"]:
        print(f"[llm] payload {budget['chars_before']} 字元超出預算,裁掉 "
              f"{len(budget['trimmed'])} 個背景區塊 → {budget['chars_after']}",
              file=_sys.stderr)
    # **第二層**(第二十四輪 P1-2):2026-08-06 裁完仍 910K,而剩下的全在
    # 不可裁清單裡 —— gate 每天正確地擋,特化路徑卻沒有一天可能成功。
    packet, cmp_rep = _pc.compact(packet, limit=budget["limit"])
    if manifest is not None:
        manifest.setdefault("llm", {})["payload_compact"] = cmp_rep
        # **量測要是活的**:`block_sizes()` 先前從未被呼叫 —— 模組寫著
        # 「先量再裁」而沒有人知道剩下那 910K 是什麼。
        manifest["llm"]["block_sizes"] = dict(
            list(block_sizes(packet).items())[:12])
    if cmp_rep["applied"]:
        budget = dict(budget, chars_after=cmp_rep["chars_after"],
                      over_budget=cmp_rep["over_budget"])
        if manifest is not None:
            manifest["llm"]["payload_budget"] = budget
        print(f"[llm] 第二層壓縮 → {cmp_rep['chars_after']:,} 字元", file=_sys.stderr)
    gate(budget)
    return packet


class PayloadBudgetExceeded(ValueError):
    """裁完仍超標 —— 這個請求在結構上不可能成功,不得送出。"""


def gate(report: dict) -> None:
    """**硬閘門**(第二十一輪 P1-2)。裁完仍超標就放棄特化路徑 ——
    2026-08-05 那次 2.7 秒就被 429 拒收;放它出去只會讓退避機制把
    同一個無效請求重送四次,燒掉時間預算又什麼都沒得到。
    (呼叫端會落回 legacy,信照樣寄得出去。)"""
    import sys as _sys
    if report.get("over_budget"):
        print(f"[llm] payload 裁完仍有 {report['chars_after']:,} 字元"
              f"(上限 {report['limit']:,}),放棄特化路徑", file=_sys.stderr)
        raise PayloadBudgetExceeded(
            f"payload over budget: {report['chars_after']} > {report['limit']}")
