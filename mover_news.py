# -*- coding: utf-8 -*-
"""市值前百大裡**當日大漲大跌**的個股,補查自家新聞(2026-10-30 使用者同意)。

起因:10/30 晨報類股熱度表寫著大立光 -8.2%,新聞池卻一則大立光都沒有 ——
固定 12 檔權值股、爆發力前 20 檔候選、熱門非科技類股領先股三條查詢都沒排到它,
於是信裡只有跌幅、沒有原因。

做法比照既有的逐股查詢(擴充新聞**輸入**,不改任何計分係數):挑出漲跌幅達
`THRESHOLD` 的個股,交給呼叫端傳入的 `fetcher`(`morning_report.
fetch_candidate_company_news`,同一套 Google News 查詢、貼標前先確認文章真的
提到該公司)。已被固定清單或爆發力候選查過的不重查。本模組不做網路呼叫,
任何失敗回空清單(晨報不可斷)。
"""
from __future__ import annotations

import sys

#: 單日漲跌幅門檻(%),絕對值。台股漲跌停 ±10%,7% 以上幾乎都有個別原因。
THRESHOLD = 7.0
#: 一次最多補查幾檔(依漲跌幅絕對值由大到小)。
LIMIT = 8
#: 與 `fetch_candidate_company_news(top_n=20)` 同一個數:那些已經查過,不重查。
CANDIDATE_TOP_N = 20


def _pct(row: dict):
    value = row.get("day_pct")
    return value if isinstance(value, (int, float)) and value == value else None


def picks(snapshot, exclude=(), threshold: float = THRESHOLD,
          limit: int = LIMIT) -> list[dict]:
    """回要補查的個股(淺拷貝)。`breakout.score` 改填 |漲跌幅|,
    好讓 `fetch_candidate_company_news` 依同一欄位排序、且不會因分數 0 而略過。"""
    rows = [r for r in (snapshot or []) if isinstance(r, dict) and r.get("code")]
    candidates = sorted(
        (r for r in rows if ((r.get("breakout") or {}).get("score") or 0) > 0),
        key=lambda r: (r.get("breakout") or {}).get("score") or 0, reverse=True)
    skip = {str(c) for c in (exclude or ())}
    skip |= {str(r.get("code")) for r in candidates[:CANDIDATE_TOP_N]}
    movers = [r for r in rows
              if str(r.get("code")) not in skip
              and _pct(r) is not None and abs(_pct(r)) >= threshold]
    movers.sort(key=lambda r: abs(_pct(r)), reverse=True)
    return [dict(r, breakout={"score": abs(_pct(r))}) for r in movers[:limit]]


def fetch(snapshot, fetcher, exclude=()) -> list[dict]:
    """挑出大漲大跌股 → 交給 `fetcher(rows, top_n=…, exclude_codes=…)` 查新聞。"""
    try:
        rows = picks(snapshot, exclude)
        if not rows:
            return []
        print("[mover_news] 補查大漲大跌股:" + "、".join(
            f"{r['code']}{r.get('name') or ''} {_pct(r):+.1f}%" for r in rows))
        return list(fetcher(rows, top_n=len(rows), exclude_codes=set(exclude or ())) or [])
    except Exception as e:      # noqa: BLE001
        print(f"[mover_news] 補查失敗(不影響晨報): {type(e).__name__}: {e}", file=sys.stderr)
        return []
