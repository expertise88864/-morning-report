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
MAX_SUMMARY_CHARS = 400


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


def normalize_news(news: Optional[list]) -> tuple:
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
        summary = str(n.get("summary") or "")
        items.append({
            "source_item_id": sid,
            "title": str(n.get("title") or "")[:300],
            "summary": summary[:MAX_SUMMARY_CHARS],
            "summary_truncated": len(summary) > MAX_SUMMARY_CHARS,
            "published": str(n.get("published") or ""),
            "source": str(n.get("source") or ""),
            "source_grade": _grade(n),
            "official": bool(n.get("official")),
            "entities": sorted({str(e) for e in (n.get("entities") or [])})[:12],
            "url": str(n.get("link") or n.get("url") or ""),
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
          trading_session: str = "") -> dict:
    """組出一份確定性的 EvidencePacket。

    兩個 profile 必須拿到**同一個物件**(或至少同一個 sha)。呼叫端只組一次。
    """
    kept_news, trunc = normalize_news(news)
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
    return packet


def canonical_json(packet: dict) -> str:
    """穩定序列化。**排序鍵、無空白、不逃逸非 ASCII、無法序列化的轉字串。**

    `default=str` 是刻意的:證據裡混進 datetime / Decimal 時,寧可得到一個
    穩定的字串,也不要讓整個 packet 拋例外 —— 那會讓當天完全沒有 sha,
    而沒有 sha 的那天就是不可比的一天。
    """
    return json.dumps(packet, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def evidence_sha(packet: dict) -> str:
    """證據指紋。兩邊不同就是那天不可比,不得計入十筆。"""
    return hashlib.sha256(canonical_json(packet).encode("utf-8")).hexdigest()[:16]


def evidence_ids(packet: dict) -> set:
    """packet 裡所有可被 claim 回指的證據 ID。

    Luna 的每個重大 claim 都要帶 evidence_ids,而「帶了一個不存在的 ID」
    與「沒帶」是兩種不同的失敗 —— 前者看起來有根據,更危險。
    """
    return {str(n.get("source_item_id")) for n in (packet.get("news") or [])
            if n.get("source_item_id")}
