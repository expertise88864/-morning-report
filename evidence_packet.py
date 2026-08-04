# -*- coding: utf-8 -*-
"""**EvidencePacket v1** —— provider 中立的證據包(Luna 特化實驗的公平性基礎)。

## 這個模組要解決什麼

十天實驗要比較的是「Luna xhigh + Luna 專用 prompt」對上「DeepSeek V4 Pro max +
既有 prompt」。兩邊的 prompt **刻意不同**(那正是「深度特化」的意思),所以
公平性不可能建立在「同一份 prompt 字串」上 —— 它只能建立在:

    兩邊看到的**證據**完全相同,而且證明得出來。

因此本模組把 `(quotes, fair, predictions, news, tw0050, calibration)` 正規化成
一份確定性的 dict,算出 `evidence_sha`,兩個 profile 都從**同一個 packet** 出發。
某一天兩邊的 sha 不同,那天就不得計入十筆有效樣本 —— 而不是事後才發現不可比。

## 為什麼是「投影」而不是重寫 prompt 組裝

`morning_report._build_prompt` 有 1,355 行。把它拆成結構化欄位再重組,是一次
會動到 DeepSeek 產出的大手術 —— 而使用者明說要保留 DeepSeek 的現有設計,
`tests/test_deepseek_legacy_golden.py` 也已經把它逐位元組凍結。

所以 packet 是同一組輸入的**正規化投影**:
  - `deepseek_legacy_v1` 仍走既有的 `_build_prompt`(輸出逐位元組不變)
  - `luna56_xhigh_v1` 從 packet 組自己的 prompt
兩者的證據同源、sha 同值,而 prompt 各自最佳化。

## 隱私

持股**明細不得進來**。packet 會進 prompt、也會被算 sha 記進 state,
而 state 是 commit 進公開 repo 的。這裡只收「彙總曝險」,不收代號與股數 ——
`portfolio_summary()` 是唯一入口,並由測試盯住。
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

#: schema 版本。**改欄位就要進版**:cohort 以它為身分的一部分,
#: 悄悄改欄位等於把不同定義的樣本混進同一個平均。
EVIDENCE_SCHEMA_VERSION = 1

#: 新聞來源等級的排序權重(小的優先)。官方 > A > B > C > 未知。
#: 截斷時依此排序,**不是依抓取順序** —— 抓取順序沒有語意,
#: 而「今天剛好排在後面所以被丟掉」會讓兩天的證據品質不可比。
_GRADE_RANK = {"OFFICIAL": 0, "A": 1, "B": 2, "C": 3}

#: 進 prompt 的新聞上限。超過就依 materiality 截斷,並把被丟掉的**數量與等級**
#: 記進 `truncation` —— 靜默截斷會讓「證據不足」看起來像「模型沒看到」。
MAX_NEWS_ITEMS = 220

#: 每則新聞摘要的字元上限(截斷同樣要記)。
#:
#: r1(Codex,#2):原本是 400,而 legacy prompt 用 **600**、另外還帶最多 1,500
#: 字的 `fulltext`。也就是說 Luna 看到的證據比 DeepSeek **少**,而兩邊卻蓋同一個
#: `evidence_sha` —— 那個 sha 因此是**假的保證**,而整個實驗的公平性建立在它上面。
#: 對齊 legacy 的深度。
MAX_SUMMARY_CHARS = 600

#: 全文上限。與 `morning_report._format_news_block` 的 `with_full` 分支一致。
MAX_FULLTEXT_CHARS = 1500

#: **外部文字的消毒函式**,由呼叫端注入。
#:
#: r1(Codex,#1):`morning_report._external_text` 是前一輪外審立的 P0 控制
#: (「所有 RSS/新聞/事件標題與摘要進 prompt 的唯一入口」)。第一版的 packet
#: 直接複製原始字串,等於**替注入內容開了一條繞過那個控制的旁路** ——
#: 而 strict JSON 只約束輸出形狀,約束不了 prompt 裡的指令。
#:
#: 用注入而不是 import:本模組刻意不相依主模組(它才能單獨測)。
#: 預設是**恆等函式**,但 `build()` 會在沒有拿到消毒器時拒絕組裝 ——
#: 「忘了傳」不得靜默退化成「沒有消毒」。
def _identity(text: str) -> str:
    return text


def sanitize_tree(node, clean):
    """遞迴把消毒器套用到**每一個字串葉節點**,數值型別原樣保留。

    r3(Codex,#1):我 r1 只消毒了 `news` 的五個欄位,而 `market` 區塊裡的
    `GAZETTE_RECORDS`、`STRUCTURED_NEWS_EVENTS`、`EVENT_CALENDAR`、
    `TW_DAILY_INTELLIGENCE`、`HISTORY` **同樣是抓來的外部文字** ——
    它們被原樣序列化進 payload,公報裡一個偽造的 `</UNTRUSTED_SOURCE_DATA>`
    就能提前關掉圍欄,讓後面的內容被當成指令。legacy 路徑對這些是逐欄呼叫
    `_external_text` 的;我只補了一半。

    **改成整棵樹一次掃完**,而不是繼續維護一份「哪些欄位要消毒」的清單 ——
    那份清單正是這次漏掉的東西,而且每加一個 quotes 鍵就會再漏一次。
    """
    if isinstance(node, str):
        return clean(node)
    if isinstance(node, dict):
        # 鍵也可能來自外部(例如以公司名當鍵),一起消毒。
        return {clean(k) if isinstance(k, str) else k: sanitize_tree(v, clean)
                for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [sanitize_tree(v, clean) for v in node]
    return node


def _sid(item: dict, index: int) -> str:
    """新聞的穩定識別碼。

    優先用上游已有的 `source_item_id`;沒有就用 (來源, 標題, 發布時間) 的雜湊。
    **不用陣列索引** —— 索引會隨當日抓取數量漂移,而 claim 要靠它回指證據,
    索引一變,昨天的 claim 就指到今天的另一則新聞。
    """
    existing = str(item.get("source_item_id") or "").strip()
    if existing:
        return existing[:16]
    raw = "|".join(str(item.get(k) or "") for k in ("source", "title", "published"))
    if not raw.strip("|"):
        raw = f"__empty__{index}"
    return "n" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:11]


def _grade(item: dict) -> str:
    if item.get("official"):
        return "OFFICIAL"
    g = str(item.get("source_grade") or "").strip().upper()
    return g if g in _GRADE_RANK else "C"


def normalize_news(news: Optional[list], sanitize=None) -> tuple:
    """(正規化後的新聞, 截斷摘要)。確定性排序、確定性截斷。

    排序鍵刻意是 (等級, 發布時間倒序, source_item_id) —— 最後一項是
    **決勝子句**:少了它,兩則同等級同時間的新聞順序會依賴 dict 的插入順序,
    而那會讓 evidence_sha 在無關的上游變動下抖動。
    """
    items, seen = [], set()
    for i, n in enumerate(news or []):
        if not isinstance(n, dict):
            continue
        sid = _sid(n, i)
        if sid in seen:          # 上游已去重,這裡只是防守
            continue
        seen.add(sid)
        # **每一個外部字串都要過消毒器。** 標題、摘要、全文、來源名、
        # 實體、URL 全部是抓來的,任何一個都可能帶注入內容。
        clean = sanitize or _identity
        summary = clean(str(n.get("summary") or ""))
        fulltext = clean(str(n.get("fulltext") or ""))
        items.append({
            "source_item_id": sid,
            "title": clean(str(n.get("title") or ""))[:300],
            "summary": summary[:MAX_SUMMARY_CHARS],
            "summary_truncated": len(summary) > MAX_SUMMARY_CHARS,
            "fulltext": fulltext[:MAX_FULLTEXT_CHARS],
            "fulltext_truncated": len(fulltext) > MAX_FULLTEXT_CHARS,
            "published": str(n.get("published") or ""),
            "source": clean(str(n.get("source") or "")),
            "source_grade": _grade(n),
            "official": bool(n.get("official")),
            "entities": sorted({clean(str(e)) for e in (n.get("entities") or [])})[:12],
            "url": clean(str(n.get("link") or n.get("url") or "")),
        })
    items.sort(key=lambda x: (_GRADE_RANK[x["source_grade"]],
                             _neg_time(x["published"]), x["source_item_id"]))
    kept, dropped = items[:MAX_NEWS_ITEMS], items[MAX_NEWS_ITEMS:]
    trunc = {"news_total": len(items), "news_kept": len(kept),
             "news_dropped": len(dropped),
             "news_dropped_by_grade": _count_by_grade(dropped),
             "summaries_truncated": sum(1 for x in kept if x["summary_truncated"])}
    return kept, trunc


def _neg_time(published: str) -> str:
    """讓「新的排前面」可以用單一 sort key 表示(字串反轉不可行,改用補數位)。"""
    # ISO 時間字串:用固定長度的補數,確保新的排前面且完全確定性。
    s = (published or "")[:32].ljust(32, "0")
    return "".join(chr(0x10FFFD - ord(c)) if ord(c) < 0x10FFFD else c for c in s)


def _count_by_grade(items: list) -> dict:
    out: dict = {}
    for x in items:
        out[x["source_grade"]] = out.get(x["source_grade"], 0) + 1
    return dict(sorted(out.items()))


def portfolio_summary(quotes: dict) -> dict:
    """**只有彙總曝險,沒有代號、沒有股數。**

    packet 會進 prompt、會被算 sha、sha 會進 commit 到公開 repo 的 state。
    持股明細一旦進來就再也拿不回去,所以入口只有這一個,而且刻意不接受
    「順便帶一下代號」的參數。由 `tests/` 盯住。
    """
    actual = (quotes or {}).get("PORTFOLIO_ACTUAL") or {}
    if not isinstance(actual, dict):
        return {"available": False}
    out: dict = {}
    for slot in ("p1", "p2"):
        block = actual.get(slot)
        if not isinstance(block, dict):
            continue
        pct = block.get("gain_pct")
        if not isinstance(pct, (int, float)):
            continue
        # **只放百分比與檔數。** 刻意不放 gain_amount / prev_value /
        # last_value —— 那三個是絕對金額,等於淨值訊號。信件裡顯示金額是
        # 使用者看自己的信;packet 會進 prompt、它的 sha 會進 commit 到
        # 公開 repo 的 state,標準要更嚴。也刻意不放倉位名稱。
        out[slot] = {"change_pct": pct,
                     "holdings": int(block.get("n_holdings") or 0),
                     "priced": int(block.get("n_priced") or 0)}
    return {"available": bool(out), "slots": out}


#: packet 從 `quotes` 取哪些鍵。**明列**,不是 `dict(quotes)` ——
#: quotes 是主流程的萬用袋子,裡面有持股明細、有渲染用的中間物、
#: 也有未來會被加進去的東西。全部倒進 packet 等於讓 evidence_sha 對
#: 「與證據無關的改動」敏感,十天樣本會莫名其妙分裂。
EVIDENCE_QUOTE_KEYS = (
    "QQQ", "TSM", "SPY", "USDTWD", "USDTWD_prev", "MACRO", "MACRO_VINTAGE",
    "EX_DIV_TODAY", "TAIFEX_OI", "TAIFEX_LARGE", "TAIFEX_PCR", "NIGHT_TXF",
    "TAIEX_PRED", "BREADTH", "MARGIN", "FOREIGN_TOP10_TOTAL", "SECTOR_HEAT",
    "MARKET_REGIME", "MA200_STATUS", "ANALYST_MOMENTUM", "SEC_FILINGS",
    "STRUCTURED_NEWS_EVENTS", "EVENT_TIMELINE", "EVENT_CALENDAR",
    "GAZETTE_RECORDS", "POLICY_NEW_KEYWORDS", "TW_DAILY_INTELLIGENCE",
    "MODEL_WALK_FORWARD", "MODEL_MONITORING", "MIDTERM", "ABSORPTION",
    "DATA_QUALITY", "SOURCE_HEALTH", "SOURCE_DATA_CHECKS", "HEALTH_WARNINGS",
    "ALERTS", "LAST_TRADING_SESSION", "HISTORY", "STANCE_PY",
)


def build(quotes: dict, fair: dict, predictions: dict, news: Optional[list],
          tw0050: Optional[list], calibration: Optional[dict], *,
          as_of: str = "", target_session_date: str = "",
          trading_session: str = "", sanitize=None) -> dict:
    """組出一份確定性的 EvidencePacket。

    兩個 profile 必須拿到**同一個物件**(或至少同一個 sha)。呼叫端只組一次。
    """
    if sanitize is None:
        # **忘了傳不得靜默退化成「沒有消毒」。** 這是前一輪外審立的 P0 控制,
        # 而它最可能的失效方式就是「新的呼叫端沒有接上」——
        # 那時沒有任何東西會變紅,只有注入內容會靜靜進 prompt。
        raise ValueError("evidence_packet.build 需要 sanitize —— "
                         "外部文字進 prompt 必須經過消毒器")
    kept_news, trunc = normalize_news(news, sanitize)
    packet = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "as_of": str(as_of or ""),
        "target_session_date": str(target_session_date or ""),
        "trading_session": str(trading_session or ""),
        "market": {k: (quotes or {}).get(k) for k in EVIDENCE_QUOTE_KEYS
                   if (quotes or {}).get(k) is not None},
        "valuation_00662": fair or {},
        "predictions_2330": predictions or {},
        "tw_universe": list(tw0050 or []),
        "calibration": calibration or {},
        "news": kept_news,
        "portfolio": portfolio_summary(quotes or {}),
        "truncation": trunc,
    }
    # r3(Codex,#1):**整棵樹消毒。** `market` 區塊裡的公報、結構化事件、
    # 政策情報、歷史全都是外部文字,先前被原樣序列化進 payload。
    # 在算 sha **之前**做 —— 指紋要對應真正送出去的內容。
    packet = sanitize_tree(packet, sanitize)
    # r2(Codex,#2):可比性判準與深度揭露一起放進 packet ——
    # 兩者都必須進實驗帳本,事後才分得出「模型差異」與「餵進去的東西不同」。
    packet["core_sha"] = core_evidence_sha(news, target_session_date)
    packet["coverage"] = coverage(packet, news)
    return packet


def _key_order(k):
    """混型別的鍵也要排得出先後。**先比型別名,再比字串形式。**

    `sorted()` 對 `{2026: …, "QQQ": …}` 會拋
    `TypeError: '<' not supported between instances of 'int' and 'str'`。
    全部是字串鍵時,`str(k) == k`,所以排序結果與 `sorted(keys)` 完全相同
    —— 這是「修了不改變既有指紋」的依據。
    """
    return (type(k).__name__, str(k))


def _sorted_tree(node):
    """把整棵樹的 dict 依 `_key_order` 重建。**只改順序,不改內容。**"""
    if isinstance(node, dict):
        return {k: _sorted_tree(node[k]) for k in sorted(node, key=_key_order)}
    if isinstance(node, (list, tuple)):
        return [_sorted_tree(v) for v in node]
    return node


def nonstring_key_paths(node, path: str = "") -> list:
    """哪些位置的 dict 鍵不是字串(給診斷用,不影響序列化)。

    2026-08-04 實機:Luna 特化路徑連兩天失敗,而第二天終於記到例外是
    `TypeError: '<' not supported between instances of 'int' and 'str'`。
    知道「是鍵的型別」還不夠 —— 要知道**是哪個上游欄位**才修得到源頭,
    否則下次換一個欄位又會重來一次。
    """
    out: list = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else str(k)
            if not isinstance(k, str):
                out.append(f"{path or '(root)'}:{k!r}({type(k).__name__})")
            out += nonstring_key_paths(v, here)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            out += nonstring_key_paths(v, f"{path}[{i}]")
    return out


def canonical_json(packet: dict) -> str:
    """穩定序列化。**排序鍵、無空白、不逃逸非 ASCII、無法序列化的轉字串。**

    `default=str` 是刻意的:證據裡混進 datetime / Decimal 時,寧可得到一個
    穩定的字串,也不要讓整個 packet 拋例外 —— 那會讓當天完全沒有 sha,
    而沒有 sha 的那天就是不可比的一天。

    2026-08-04 實機:**上面那句話是這個函式沒有做到的事。** `default=str`
    保護的是**值**,而 `sort_keys=True` 在**鍵**混型別時照樣拋 ——
    Luna 特化路徑連兩天在這裡掛掉(`build()` 只對 news 算 core_sha 所以沒事,
    `build_luna_bundle` 對整個 packet 算 evidence_sha 才炸),實驗 0/10。
    改成先用型別感知的順序重建整棵樹,再以 `sort_keys=False` 輸出:
    **全字串鍵時輸出逐位元組相同**,混型別時不再拋。
    """
    return json.dumps(_sorted_tree(packet), sort_keys=False, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def evidence_sha(packet: dict) -> str:
    """**這個 packet 物件**的指紋。

    ⚠ 它證明的是「兩邊拿到同一個 packet」,**不是**「兩邊看到同樣的東西」——
    legacy profile 走的是 `_build_prompt`,那份 prompt 有自己的 bucket 配額、
    自己的全文取捨、也消費了幾個不在 `EVIDENCE_QUOTE_KEYS` 裡的欄位。
    可比性請用 `core_evidence_sha`,理由見它的 docstring。
    """
    return hashlib.sha256(canonical_json(packet).encode("utf-8")).hexdigest()[:16]


def core_evidence_sha(news: Optional[list], target_session_date: str = "") -> str:
    """**兩邊都確實看得到的核心證據**的指紋(r2 外審 #2 的折衷)。

    ## 為什麼不能用整個 packet 的 sha 當可比性判準

    兩份 prompt 是**各自獨立組出來的**:Luna 從 packet 渲染,DeepSeek 走既有的
    `_build_prompt`。兩者的深度與欄位取捨本來就不同(那正是「各自最佳化」),
    所以「同一個 packet 物件」證明不了「同樣的證據」。拿它當可比性判準,
    是一個聽起來很硬、實際上是空的保證。

    要讓那個保證為真只有兩條路,而兩條都牴觸既有約束:
      (a) 讓 DeepSeek 也從 packet 渲染 → 改變它的 prompt,違反「保留原設計」,
          逐位元組凍結會紅;
      (b) 對兩份 prompt 各自算真實內容指紋、不同就判不可比 → 誠實,
          但幾乎每天都不可比,十配對湊不滿。

    ## 折衷:指紋只涵蓋「來源池」

    這個 sha 算的是**上游那份 `news` 的 source_item_id 集合 + 目標交易日**,
    也就是兩條路徑共同的**輸入**,在任何截斷與渲染之前。它證明得了:

        兩邊今天是從同一批新聞、同一個交易日出發的。

    它**證明不了**兩邊看到同樣的深度 —— 那個差異由 `coverage` 逐側記錄,
    在最終報告裡當作**已揭露的 profile 差異**,而不是假裝不存在。
    這是這份實驗誠實能給的最強保證,不是最漂亮的那個。
    """
    ids = sorted({_sid(n, i) for i, n in enumerate(news or [])
                  if isinstance(n, dict)})
    raw = str(target_session_date or "") + "|" + ",".join(ids)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def coverage(packet: dict, news: Optional[list]) -> dict:
    """這個 packet 涵蓋了來源池的多少 —— **深度差異要被記錄,不是被隱藏**。

    `included / available` 就是「Luna 這一側看到多少」。legacy 那一側的對應
    數字由它自己的 bucket 邏輯決定,兩者不同是預期的;把它記下來,
    十配對的結論才說得出「這是模型差異還是餵進去的東西不同」。
    """
    avail = sum(1 for n in (news or []) if isinstance(n, dict))
    kept = len((packet or {}).get("news") or [])
    full = sum(1 for n in ((packet or {}).get("news") or []) if n.get("fulltext"))
    return {"available": avail, "included": kept,
            "with_fulltext": full,
            "rate": round(kept / avail, 3) if avail else None}


def evidence_ids(packet: dict) -> set:
    """packet 裡所有可被 claim 回指的證據 ID。

    Luna 的每個重大 claim 都要帶 evidence_ids,而「帶了一個不存在的 ID」
    與「沒帶」是兩種不同的失敗 —— 前者看起來有根據,更危險。
    """
    return {str(n.get("source_item_id")) for n in (packet.get("news") or [])
            if n.get("source_item_id")}
