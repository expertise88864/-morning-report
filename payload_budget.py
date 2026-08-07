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
#: 9.5 萬 token。訂 600K 字元(估 33 萬 token)—— 比成功過的大三倍多,
#: 仍遠低於被拒的那次。**這是保守的起點,不是精算的最適值。**
MAX_PAYLOAD_CHARS = 600_000

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
MAX_REQUEST_CHARS = 700_000


def request_gate(bundle: dict, *, manifest=None,
                 limit: int = MAX_REQUEST_CHARS) -> None:
    """對**組好的 bundle** 做最終檢查(第二十二輪 P1-2 問題 B)。

    量的是 instructions + user payload + response schema 的實際字元 ——
    這才是送出去的東西。超標直接 `PayloadBudgetExceeded`。
    """
    # 第二十三輪 P1-2:**`structured_output` 是布林旗標**,真正的 strict
    # schema 在 `response_schema`(實測序列化 32,080 字元)—— 上一版把
    # `True` 序列化成 4 個字元,32K 的 schema 整個漏算,而測試餵同一個
    # 錯鍵,兩邊一起錯、一起綠。
    chars = (len(str(bundle.get("developer_instructions") or ""))
             + len(str(bundle.get("user_payload") or ""))
             + len(json.dumps(bundle.get("response_schema") or {},
                              ensure_ascii=False, default=str)))
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
