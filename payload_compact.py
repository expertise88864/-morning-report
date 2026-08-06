# -*- coding: utf-8 -*-
"""**第二層壓縮:不可裁區塊本身就超標時怎麼辦**(第二十四輪 P1-2)。

## 為什麼需要第二層

2026-08-06 生產:

    chars_before = 2,263,832
    limit        =   600,000
    chars_after  =   910,312      ← 背景區塊全裁光之後
    over_budget  = true

`payload_budget.trim()` 把九個背景區塊(HISTORY 776K、STRUCTURED_NEWS_EVENTS
504K…)整整 135 萬字元全裁掉,仍然超出 51.7%。原因是**剩下的都在它的
不可裁清單裡**:`news`、`tw_universe`、`signal_tensions`、行情核心。

於是硬閘門每天都正確地擋下請求,而特化路徑**沒有任何一天可能成功** ——
gate 安全地失敗,卻沒有路徑能通過。

## 量出來的組成(不是猜的)

`payload_budget.block_sizes()` 在此之前**從來沒有被呼叫過** —— 模組
docstring 寫著「先量再裁」,而量測函式是死程式碼,所以沒有人知道那 910K
是什麼。以 `state/model_history.json` 的同構快照實測:

    tw_universe   100 檔 × 4,179 字元  ≈ 418K   ← 最大
      其中 price_forecast 每檔 2,486  ≈ 250K   ← 佔單檔 59%
    news          220 則 + 全文        ≈ 280K
    其餘(行情核心/張力)               ≈ 210K

`price_forecast` 裝的是**我們自己模型的內部管線**:`model_version`、
`training_rows`、`model_method`、`quality{}`…重複一百次送進分析模型。
那不是證據,是機器的自言自語。

## 三個分層(只在仍超標時逐級啟動,每一級都量、都記)

1. **`price_forecast` 去管線**:留下標頭數字(預期價、預期報酬、區間),
   丟掉版本/訓練列數/方法字串。零證據損失。
2. **非分析標的降為骨架**:只有真正被分析的標的留完整欄位,其餘留
   代號/名稱/產業/收盤/漲跌。**列一定留著** —— `analysis_validate` 用
   `tw_universe` 的代號當「這個代號今天真的存在」的白名單,刪列會讓
   合法的個股分析被判成捏造代號。
3. **低重要性新聞的摘要縮短**:只縮**內容**,不刪則數 ——
   刪則會讓 `news_clusters` / `top_events` / event graph 的成員 ID 指空。

## 沒有靜默的截斷

每一級做了什麼、省下多少,全部進報告與 manifest。這個 repo 反覆栽的形狀是
「每一塊都有人負責,總和沒有人負責」;而它的近親是**壓縮完沒有人說壓了什麼**。
"""
from __future__ import annotations

import json
from typing import Optional

#: `price_forecast` 裡**留下來**的標頭欄位。其餘(model_version、
#: training_rows、model_method、quality、fallback_enabled…)是模型內部
#: 管線,分析模型引用不到也不該引用。
_FORECAST_KEEP = ("expected_price", "expected_return_pct", "lower", "upper",
                  "horizon_days", "label")
_FORECAST_TOP_KEEP = ("method", "regime", "confidence")

#: 非分析標的保留的骨架欄位。**代號一定在裡面**(白名單完整性)。
_SKELETON = ("code", "name", "industry", "close", "day_pct", "pct_5d",
             "market_cap", "ranking_score")

#: 保留完整欄位的標的數。二十檔涵蓋觀察名單與核心持股,
#: 其餘標的在信裡本來就只是背景分佈。
FULL_DETAIL_ROWS = 20

#: 低重要性新聞的摘要上限(完整版是 600)。
COMPACT_SUMMARY_CHARS = 200


def _size(node) -> int:
    try:
        return len(json.dumps(node, ensure_ascii=False, default=str))
    except (TypeError, ValueError):    # pragma: no cover - default=str 已保護
        return len(str(node))


def _thin_forecast(fc):
    """`price_forecast` 去掉模型內部管線,留標頭數字。"""
    if not isinstance(fc, dict):
        return fc
    out = {k: fc[k] for k in _FORECAST_TOP_KEEP if k in fc}
    for k, v in fc.items():
        if isinstance(v, dict) and any(x in v for x in _FORECAST_KEEP):
            out[k] = {kk: v[kk] for kk in _FORECAST_KEEP if kk in v}
    return out


def _analyzed_codes(packet: dict) -> set:
    """**真的會被分析的標的**:觀察名單前段 + 持股 + 新聞提到的代號。"""
    rows = [r for r in (packet.get("tw_universe") or []) if isinstance(r, dict)]
    ranked = sorted(rows, key=lambda r: -(_num(r.get("ranking_score"))),
                    )[:FULL_DETAIL_ROWS]
    keep = {str(r.get("code") or "") for r in ranked}
    # 新聞實體/標題提到的代號要留完整欄位 —— 那正是模型會逐檔談的
    for n in (packet.get("news") or []):
        if not isinstance(n, dict):
            continue
        blob = str(n.get("title") or "") + " " + " ".join(
            str(e) for e in (n.get("entities") or []))
        for r in rows:
            c = str(r.get("code") or "")
            if c and c in blob:
                keep.add(c)
    return keep - {""}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("-inf")


def compact(packet: Optional[dict], *, limit: int) -> tuple:
    """`(壓縮後的 packet, 報告)`。**不改變輸入**;只在超標時逐級啟動。

    回報 `{applied: [...], chars_before, chars_after, over_budget}` ——
    `applied` 是空的就表示沒超標、什麼都沒做。
    """
    pk = dict(packet or {})
    before = _size(pk)
    report = {"chars_before": before, "limit": limit, "applied": [],
              "chars_after": before, "over_budget": False}
    if before <= limit:
        return pk, report

    # ── 第一級:price_forecast 去管線(零證據損失)──
    rows = [r for r in (pk.get("tw_universe") or []) if isinstance(r, dict)]
    if rows and any("price_forecast" in r for r in rows):
        thinned = [dict(r, price_forecast=_thin_forecast(r["price_forecast"]))
                   if "price_forecast" in r else r for r in rows]
        saved = _size(rows) - _size(thinned)
        pk["tw_universe"] = thinned
        rows = thinned
        report["applied"].append(
            {"tier": "universe.price_forecast_plumbing", "chars_saved": saved,
             "detail": f"{len(thinned)} 檔只留預測標頭數字"})

    # ── 第二級:非分析標的降為骨架(**列全部保留**,代號白名單不受影響)──
    if _size(pk) > limit and rows:
        keep_full = _analyzed_codes(pk)
        skeletal = [r if str(r.get("code") or "") in keep_full
                    else {k: r[k] for k in _SKELETON if k in r}
                    for r in rows]
        saved = _size(rows) - _size(skeletal)
        if saved > 0:
            pk["tw_universe"] = skeletal
            report["applied"].append(
                {"tier": "universe.non_analyzed_to_skeleton",
                 "chars_saved": saved,
                 "detail": f"{len(rows) - len(keep_full)} 檔降為骨架、"
                           f"{len(keep_full)} 檔保留完整(代號全數保留)"})

    # ── 第三級:低重要性新聞的摘要縮短(**不刪則數**,ID 不會指空)──
    if _size(pk) > limit:
        news = [n for n in (pk.get("news") or []) if isinstance(n, dict)]
        info = pk.get("news_clusters") or {}
        need = set(info.get("required_cluster_ids") or ())
        keep_ids: set = set()
        for c in (info.get("clusters") or []):
            if str(c.get("cluster_id") or "") in need:
                keep_ids.update(str(m) for m in (c.get("member_source_ids") or ()))
        for t in (pk.get("top_events") or []):
            for m in ((t or {}).get("member_source_ids") or ()):
                keep_ids.add(str(m))
        shortened = []
        for n in news:
            sid = str(n.get("source_item_id") or "")
            s = str(n.get("summary") or "")
            if sid in keep_ids or len(s) <= COMPACT_SUMMARY_CHARS:
                shortened.append(n)
                continue
            shortened.append(dict(n, summary=s[:COMPACT_SUMMARY_CHARS],
                                  summary_truncated=True))
        saved = _size(news) - _size(shortened)
        if saved > 0:
            pk["news"] = shortened
            report["applied"].append(
                {"tier": "news.low_materiality_summary", "chars_saved": saved,
                 "detail": f"{len(news) - len(keep_ids & {str(n.get('source_item_id')) for n in news})}"
                           f" 則非必分析新聞的摘要縮到 {COMPACT_SUMMARY_CHARS} 字元"})

    if report["applied"]:
        # **壓過的東西要說出來** —— 與 trim 的缺口揭露同一個理由。
        pk["required_disclosures"] = dict(
            pk.get("required_disclosures") or {},
            **{"gap:payload_compacted":
               "今日輸入過大,已壓縮:"
               + "、".join(a["tier"] for a in report["applied"])
               + "(標的代號與新聞則數皆完整保留)"})
    report["chars_after"] = _size(pk)
    report["over_budget"] = report["chars_after"] > limit
    return pk, report
