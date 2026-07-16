# -*- coding: utf-8 -*-
"""股癌事件研究 — pilot 探索版（Stage B dry-run）。

目的:把 events_raw.json 的「明確點名 + 有方向」個股事件,對映到 yfinance 還原價,
算各窗格前瞻報酬與「vs 0050」超額,證明端到端可行 + 看初步訊號。

⚠️ 這是 pilot 探索版,刻意簡化,**結論僅供可行性判斷,不可當研究結論**:
  - 進場:t0 次一交易日「開盤」(只用日期、保守次日);嚴謹的 09:00 規則待 P5。
  - 報酬:yfinance auto_adjust(含息近似);全量階段改 FinMind 還原價。
  - 窗格:5/20/60/120 交易日(主結論窗格 60)。
  - 倖存偏誤:下市股 yfinance 抓不到 → 標 missing 並計數揭露(不靜默丟棄)。
  - 尚未做:β/CAR、產業中性化、bootstrap CI、波動調整起漲、看空避險效益、成本。

只處理 market==TW、有 code、mention_type ∈ {bullish_call, bearish_call} 的個股事件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import median, mean

import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
EVENTS_PATH = HERE / "data" / "events_raw.json"
OUT_PATH = HERE / "data" / "pilot_event_study.json"
BENCHMARK = "0050.TW"
WINDOWS = [5, 20, 60, 120]
PRICE_PAD_DAYS = 420          # t0 後抓多少日曆天(>120 交易日 + buffer)


def configure_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:
            pass


def log(m: str) -> None:
    print(f"[study] {m}", flush=True)


_PRICE_CACHE: dict[str, pd.DataFrame | None] = {}


def get_prices(code: str, start: str, end: str) -> pd.DataFrame | None:
    """yfinance 還原 OHLC（auto_adjust）。上市 .TW 抓不到再試上櫃 .TWO。回 None=查無(疑下市)。"""
    if code in _PRICE_CACHE:
        return _PRICE_CACHE[code]
    out = None
    for suffix in (".TW", ".TWO"):
        try:
            df = yf.download(code + suffix, start=start, end=end,
                             auto_adjust=True, progress=False)
        except Exception:
            df = None
        if df is not None and len(df):
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            out = df
            break
    _PRICE_CACHE[code] = out
    return out


def _benchmark_prices(start: str, end: str) -> pd.DataFrame | None:
    if BENCHMARK in _PRICE_CACHE:
        return _PRICE_CACHE[BENCHMARK]
    try:
        df = yf.download(BENCHMARK, start=start, end=end, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
    except Exception:
        df = None
    _PRICE_CACHE[BENCHMARK] = df if (df is not None and len(df)) else None
    return _PRICE_CACHE[BENCHMARK]


def _entry_index(df: pd.DataFrame, t0_date: str) -> int | None:
    """t0 次一交易日(index 嚴格 > t0_date 的第一個)。"""
    t0 = pd.Timestamp(t0_date)
    after = df.index[df.index > t0]
    if len(after) == 0:
        return None
    return int(df.index.get_loc(after[0]))


def _fwd_return(df: pd.DataFrame, entry_idx: int, k: int) -> float | None:
    """進場開盤 → 第 k 交易日收盤 simple return。"""
    if entry_idx + k >= len(df):
        return None
    entry = float(df["Open"].iloc[entry_idx])
    exit_ = float(df["Close"].iloc[entry_idx + k])
    if entry <= 0:
        return None
    return exit_ / entry - 1.0


def _bench_return(bench: pd.DataFrame, entry_date, exit_date) -> float | None:
    """0050 同一日曆窗:進場日(或之後第一個交易日)開盤 → 出場日(或之前最後交易日)收盤。"""
    ein = bench.index[bench.index >= entry_date]
    eout = bench.index[bench.index <= exit_date]
    if len(ein) == 0 or len(eout) == 0:
        return None
    e = float(bench["Open"].loc[ein[0]])
    x = float(bench["Close"].loc[eout[-1]])
    return x / e - 1.0 if e > 0 else None


def main() -> int:
    configure_stdio()
    if not EVENTS_PATH.exists():
        log(f"缺 {EVENTS_PATH}，請先跑 extract_events.py")
        return 1
    events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    cand = [e for e in events if e["level"] == "stock" and e.get("market") == "TW"
            and e.get("code") and e.get("mention_type") in ("bullish_call", "bearish_call")]
    log(f"事件總數 {len(events)}；可研究(TW/有code/有方向call) {len(cand)}")
    if not cand:
        log("無可研究事件（pilot 樣本太少或多為中性/美股），先擴大 pilot 集數")
        return 0

    dates = [e["t0_date"] for e in cand if e.get("t0_date")]
    gstart = (pd.Timestamp(min(dates)) - pd.Timedelta(days=10)).date().isoformat()
    gend = (pd.Timestamp(max(dates)) + pd.Timedelta(days=PRICE_PAD_DAYS)).date().isoformat()
    bench = _benchmark_prices(gstart, gend)
    if bench is None:
        log("0050 基準價抓取失敗")
        return 1

    results, missing, no_entry = [], [], []
    for e in cand:
        code, t0 = e["code"], e.get("t0_date")
        if not t0:
            continue
        df = get_prices(code, gstart, gend)
        if df is None:
            missing.append(code)
            continue
        ei = _entry_index(df, t0)
        if ei is None:
            no_entry.append((code, t0))
            continue
        sign = 1.0 if e["mention_type"] == "bullish_call" else -1.0
        entry_date = df.index[ei]
        row = {"ep": e["ep"], "t0_date": t0, "code": code, "name": e["name"],
               "mention_type": e["mention_type"], "already_ran": e.get("already_ran", False),
               "conviction": e.get("conviction", "")}
        for k in WINDOWS:
            r = _fwd_return(df, ei, k)
            if r is None:
                row[f"strat_{k}"] = None
                row[f"excess_{k}"] = None
                continue
            exit_date = df.index[ei + k]
            br = _bench_return(bench, entry_date, exit_date)
            strat = sign * r
            row[f"strat_{k}"] = round(strat, 4)
            row[f"excess_{k}"] = round(strat - sign * br, 4) if br is not None else None
            # 看多:超額=策略-0050;看空 sign=-1 → 超額=(-asset)-(-0050)=0050多賠-asset多賠,即避險效益
        results.append(row)

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=True, indent=2), encoding="utf-8")
    log(f"逐事件結果寫入 {OUT_PATH}（{len(results)} 事件）")
    if missing:
        log(f"⚠ 倖存偏誤揭露:yfinance 查無價格(疑下市/換股號) {len(missing)} 檔:{sorted(set(missing))}")
    if no_entry:
        log(f"⚠ 無進場日(t0 太新,價格尚未涵蓋) {len(no_entry)} 筆")

    # 彙總(pilot)
    log("=" * 60)
    log(f"PILOT 事件研究結果（{len(results)} 事件，主結論窗格 60 日）")
    for label, subset in [("全部 call", results),
                          ("看多 bullish_call", [r for r in results if r["mention_type"] == "bullish_call"]),
                          ("看空 bearish_call", [r for r in results if r["mention_type"] == "bearish_call"]),
                          ("排除 already_ran", [r for r in results if not r["already_ran"]])]:
        log(f"--- {label}（n={len(subset)}）---")
        for k in WINDOWS:
            strat = [r[f"strat_{k}"] for r in subset if r.get(f"strat_{k}") is not None]
            exc = [r[f"excess_{k}"] for r in subset if r.get(f"excess_{k}") is not None]
            if not strat:
                continue
            win = 100 * sum(1 for x in strat if x > 0) / len(strat)
            beat = 100 * sum(1 for x in exc if x > 0) / len(exc) if exc else float("nan")
            log(f"  {k:>3}日 n={len(strat):>3} | 策略報酬 中位 {median(strat)*100:+5.1f}% "
                f"均 {mean(strat)*100:+5.1f}% | vs0050超額 中位 "
                f"{(median(exc)*100 if exc else float('nan')):+5.1f}% | 勝率 {win:.0f}% 贏0050 {beat:.0f}%")
    log("=" * 60)
    log("⚠ pilot 探索版:未做 β/產業中性化/bootstrap CI/成本,n 偏小,僅判可行性,非研究結論")
    return 0


if __name__ == "__main__":
    sys.exit(main())
