#!/usr/bin/env python3
"""G7|§B2 歷史索引 + 本機查詢(純 stdlib、離線;不進晨報 pipeline、不上 Actions)。

資料源(皆已在 repo state/ 內,由每日晨報 push):
  * state/history.json           — 結構化每日紀錄(預測/實際/立場/critical_news…)
  * state/emails/<date>.html.gz  — 寄出信件的去識別 HTML 存檔

用法:
  python tools/query_history.py index
      重建 state/history_index.jsonl(每日一行:日期/立場/預測 vs 實際/重點事件/信件純文字)。
  python tools/query_history.py query --keyword 台積電 [--from 2026-07-01] [--to 2026-07-14]
      查索引;--keyword 同時搜事件與信件純文字,--code 2330 過濾提及該代號的日子,
      --deep 額外印出信件內含關鍵字的上下文行。無參數 → 列最近 10 天摘要。

索引內容全部來自「去識別後」的存檔與市場中性 history 欄位,不含任何持股資訊。
"""
from __future__ import annotations

import argparse
import gzip
import html as _html
import io
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HISTORY_JSON = REPO / "state" / "history.json"
EMAIL_DIR = REPO / "state" / "emails"
INDEX_JSONL = REPO / "state" / "history_index.jsonl"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _strip_html(raw: str) -> str:
    """HTML → 可搜尋純文字(去 style/script/標籤、解實體、壓空白)。"""
    txt = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", raw)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = _html.unescape(txt)
    return re.sub(r"[ \t　]+", " ", txt)


def _email_text(date: str) -> str:
    p = EMAIL_DIR / f"{date}.html.gz"
    if not p.exists():
        return ""
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return _strip_html(f.read())
    except OSError as e:
        print(f"[index] {p.name} 讀取失敗: {e}", file=sys.stderr)
        return ""


def build_index() -> int:
    """history.json + email 存檔 → history_index.jsonl。回傳筆數。"""
    try:
        hist = json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[index] 讀 history.json 失敗: {e}", file=sys.stderr)
        return 0
    rows = []
    for h in hist if isinstance(hist, list) else []:
        if not isinstance(h, dict):
            continue
        date = str(h.get("date") or "")[:10]
        if not _DATE_RE.match(date):
            continue
        text = _email_text(date)
        rows.append({
            "date": date,
            "target_session_date": h.get("target_session_date"),
            "weekday": h.get("weekday"),
            "stance_label": h.get("stance_label"),
            "stance_score": h.get("stance_score"),
            "pred_taiex": h.get("pred_taiex"),
            "actual_open_taiex": h.get("actual_open_taiex"),
            "pred_2330": h.get("weighted_final_2330"),
            "actual_open_2330": h.get("actual_open_2330"),
            "fair_00662": h.get("fair_00662"),
            "critical_news": h.get("critical_news") or [],
            "qqq_pct": h.get("qqq_pct"), "tsm_pct": h.get("tsm_pct"),
            "vix": h.get("vix"),
            "email_chars": len(text),
            # 信件純文字直接入索引(去識別後的存檔,無持股資訊);單日 ~2-4 萬字,
            # 42 天 ≈ 1-2MB jsonl,本機全文搜尋足夠快,免建倒排。
            "email_text": text,
        })
    rows.sort(key=lambda r: r["date"])
    with io.open(INDEX_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[index] 已寫 {INDEX_JSONL.name}:{len(rows)} 天"
          f"(含信件全文 {sum(1 for r in rows if r['email_chars'])} 天)")
    return len(rows)


def _load_index() -> list[dict]:
    if not INDEX_JSONL.exists():
        print("[query] 索引不存在,先建立…", file=sys.stderr)
        build_index()
    out = []
    try:
        for line in io.open(INDEX_JSONL, encoding="utf-8"):
            line = line.strip()
            if line:
                out.append(json.loads(line))
    except (OSError, ValueError) as e:
        print(f"[query] 讀索引失敗: {e}", file=sys.stderr)
    return out


def _summary_line(r: dict) -> str:
    st = r.get("stance_label") or "—"
    pt, at = r.get("pred_taiex"), r.get("actual_open_taiex")
    err = (f"{(at / pt - 1) * 100:+.2f}%" if (pt and at) else "—")
    crit = "、".join((r.get("critical_news") or [])[:2]) or "(無重大事件)"
    return (f"{r['date']} {r.get('weekday') or ''} 立場:{st} "
            f"加權誤差:{err}  {crit[:60]}")


def run_query(args) -> list[dict]:
    rows = _load_index()
    if args.date_from:
        rows = [r for r in rows if r["date"] >= args.date_from]
    if args.date_to:
        rows = [r for r in rows if r["date"] <= args.date_to]
    if args.code:
        rows = [r for r in rows
                if args.code in r.get("email_text", "")
                or any(args.code in str(c) for c in r.get("critical_news") or [])]
    if args.keyword:
        kw = args.keyword
        rows = [r for r in rows
                if kw in r.get("email_text", "")
                or any(kw in str(c) for c in r.get("critical_news") or [])
                or kw in str(r.get("stance_label") or "")]
    if not (args.keyword or args.code or args.date_from or args.date_to):
        rows = rows[-10:]                      # 無條件 → 最近 10 天摘要
    for r in rows:
        print(_summary_line(r))
        if args.deep and args.keyword and args.keyword in r.get("email_text", ""):
            text = r["email_text"]
            for m in list(re.finditer(re.escape(args.keyword), text))[:3]:
                s = max(0, m.start() - 40)
                print(f"    …{text[s:m.end() + 60].strip()}…")
    print(f"[query] 共 {len(rows)} 天符合")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="重建 history_index.jsonl")
    q = sub.add_parser("query", help="查詢索引")
    q.add_argument("--keyword", help="關鍵字(搜事件/立場/信件全文)")
    q.add_argument("--code", help="標的代號(如 2330;搜信件與事件)")
    q.add_argument("--from", dest="date_from", help="起日 YYYY-MM-DD")
    q.add_argument("--to", dest="date_to", help="迄日 YYYY-MM-DD")
    q.add_argument("--deep", action="store_true", help="印出關鍵字前後文")
    args = ap.parse_args(argv)
    if args.cmd == "index":
        return 0 if build_index() >= 0 else 1
    run_query(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
