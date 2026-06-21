# -*- coding: utf-8 -*-
"""股癌事件研究 — 完整引擎(個股層級 + 題材層級 + 嚴謹度提升 + HTML 報告)。

讀 data/events_raw.json(extract_events 產出),對「明確點名/題材 + 有方向」事件做:
  個股層級:每個 bullish/bearish_call 台股事件 → 進場次日開盤、各窗格報酬、vs 0050 超額、
            β 調整持有超額 proxy(非嚴格 CAR)、事件前 [-60,-1] 漲幅(反向因果)。
  題材層級(user 最初問的):每個題材 call → 同集點名台股「等權籃子」(point-in-time)→ vs 0050。
嚴謹度:中位數為主、均值 winsorize(修離群)、勝率/贏 0050 比率;含 already_ran 分層。

價格源:yfinance 含息還原(auto_adjust)。⚠ 已知限制(誠實揭露,非最終結論):
  - 倖存偏誤:下市股 yfinance 查無 → 計數揭露並排除(結果偏高,真實更差)。
  - 未做 bootstrap CI、產業中性化、交易成本;β 為事件前 120 日簡單迴歸。
  - 題材籃子用「同集點名台股」近似(schema 未把個股連到特定題材)。

產出:data/event_study_results.json + report.html(self-contained)。
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
EVENTS_PATH = HERE / "data" / "events_raw.json"
OUT_JSON = HERE / "data" / "event_study_results.json"
OUT_HTML = HERE / "report.html"
BENCHMARK = "0050.TW"
WINDOWS = [5, 20, 60, 120]
PRE_START, PRE_END = -120, -1        # β 估計窗(交易日)
RUNUP_START = -60                    # 事件前漲幅窗(反向因果)
PAD_FWD = 420                        # t0 後抓多少日曆天
PAD_BACK = 220                       # t0 前抓多少日曆天(供 β / runup)
WINSOR = (0.02, 0.98)                # 均值 winsorize 百分位


def log(m: str) -> None:
    print(f"[study] {m}", flush=True)


def configure_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:
            pass


# ---------- 價格 ----------
_CACHE: dict[str, pd.DataFrame | None] = {}


def _norm(df):
    if df is not None and isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def get_prices(code: str, gstart: str, gend: str) -> pd.DataFrame | None:
    if code in _CACHE:
        return _CACHE[code]
    out = None
    for sfx in (".TW", ".TWO"):
        try:
            df = _norm(yf.download(code + sfx, start=gstart, end=gend,
                                   auto_adjust=True, progress=False))
        except Exception:
            df = None
        if df is not None and len(df) > 30:
            out = df
            break
    _CACHE[code] = out
    return out


def bench_prices(gstart: str, gend: str) -> pd.DataFrame | None:
    if BENCHMARK in _CACHE:
        return _CACHE[BENCHMARK]
    try:
        df = _norm(yf.download(BENCHMARK, start=gstart, end=gend,
                               auto_adjust=True, progress=False))
    except Exception:
        df = None
    _CACHE[BENCHMARK] = df if (df is not None and len(df)) else None
    return _CACHE[BENCHMARK]


# ---------- 報酬計算 ----------
def _entry_idx(df, t0):
    after = df.index[df.index > pd.Timestamp(t0)]
    return int(df.index.get_loc(after[0])) if len(after) else None


def _ret(df, i, k):
    if i is None or i + k >= len(df):
        return None
    o = float(df["Open"].iloc[i])
    c = float(df["Close"].iloc[i + k])
    return (c / o - 1.0) if o > 0 else None


def _bench_ret(bench, d0, d1):
    a = bench.index[bench.index >= d0]
    b = bench.index[bench.index <= d1]
    if not len(a) or not len(b):
        return None
    e = float(bench["Open"].loc[a[0]])
    x = float(bench["Close"].loc[b[-1]])
    return (x / e - 1.0) if e > 0 else None


def _beta(df, bench, i):
    """事件前 [PRE_START, PRE_END] 日報酬對 0050 的簡單 β;不足則 None。"""
    if i is None or i + PRE_START < 0:
        return None
    seg = df.iloc[i + PRE_START:i + PRE_END]
    if len(seg) < 40:
        return None
    sr = seg["Close"].pct_change().dropna()
    br = bench["Close"].reindex(seg.index).pct_change().dropna()
    j = sr.index.intersection(br.index)
    if len(j) < 40:
        return None
    sr, br = sr.loc[j], br.loc[j]
    var = float((br * br).mean() - br.mean() ** 2)
    if var <= 0:
        return None
    cov = float((sr * br).mean() - sr.mean() * br.mean())
    return cov / var


def _runup(df, i):
    """事件前 [RUNUP_START, -1] 漲幅(反向因果:題材是否已先漲)。"""
    if i is None or i + RUNUP_START < 0:
        return None
    a = float(df["Close"].iloc[i + RUNUP_START])
    b = float(df["Close"].iloc[i - 1])
    return (b / a - 1.0) if a > 0 else None


def winsor_mean(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    lo = xs[max(0, int(len(xs) * WINSOR[0]))]
    hi = xs[min(len(xs) - 1, int(len(xs) * WINSOR[1]))]
    return st.mean(min(max(x, lo), hi) for x in xs)


def agg(rows, key_prefix):
    """對一組事件,逐窗格算 中位策略報酬 / winsor均 / 中位超額 / 勝率 / 贏0050。"""
    out = {}
    for k in WINDOWS:
        strat = [r[f"strat_{k}"] for r in rows if r.get(f"strat_{k}") is not None]
        exc = [r[f"{key_prefix}_{k}"] for r in rows if r.get(f"{key_prefix}_{k}") is not None]
        if not strat:
            continue
        out[k] = {
            "n": len(strat),
            "strat_median": round(st.median(strat), 4),
            "strat_wmean": round(winsor_mean(strat) or 0, 4),
            "excess_median": round(st.median(exc), 4) if exc else None,
            "win": round(100 * sum(1 for x in strat if x > 0) / len(strat), 1),
            "beat0050": round(100 * sum(1 for x in exc if x > 0) / len(exc), 1) if exc else None,
        }
    return out


def main() -> int:
    configure_stdio()
    if not EVENTS_PATH.exists():
        log(f"缺 {EVENTS_PATH}")
        return 1
    events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    n_eps = len({e["ep"] for e in events})
    log(f"載入 {len(events)} 事件 / {n_eps} 集")

    dates = [e["t0_date"] for e in events if e.get("t0_date")]
    gstart = (pd.Timestamp(min(dates)) - pd.Timedelta(days=PAD_BACK)).date().isoformat()
    gend = (pd.Timestamp(max(dates)) + pd.Timedelta(days=PAD_FWD)).date().isoformat()
    bench = bench_prices(gstart, gend)
    if bench is None:
        log("0050 基準抓取失敗")
        return 1

    # ---------- 個股層級 ----------
    stock_cand = [e for e in events if e["level"] == "stock" and e.get("market") == "TW"
                  and e.get("code") and e.get("mention_type") in ("bullish_call", "bearish_call")]
    log(f"個股可研究事件 {len(stock_cand)}；開始抓價+計算…")
    stock_rows, missing = [], set()
    for idx, e in enumerate(stock_cand, 1):
        df = get_prices(e["code"], gstart, gend)
        if df is None:
            missing.add(e["code"])
            continue
        i = _entry_idx(df, e["t0_date"])
        if i is None:
            continue
        sign = 1.0 if e["mention_type"] == "bullish_call" else -1.0
        beta = _beta(df, bench, i)
        row = {"ep": e["ep"], "t0_date": e["t0_date"], "code": e["code"], "name": e["name"],
               "mention_type": e["mention_type"], "already_ran": e.get("already_ran", False),
               "runup": round(_runup(df, i), 4) if _runup(df, i) is not None else None,
               "beta": round(beta, 2) if beta is not None else None}
        for k in WINDOWS:
            r = _ret(df, i, k)
            if r is None:
                continue
            d1 = df.index[i + k]
            br = _bench_ret(bench, df.index[i], d1)
            strat = sign * r
            row[f"strat_{k}"] = round(strat, 4)
            if br is not None:
                row[f"excess_{k}"] = round(strat - sign * br, 4)
                if beta is not None:
                    row[f"badj_{k}"] = round(sign * (r - beta * br), 4)   # β 調整持有超額 proxy(非嚴格 CAR)
        stock_rows.append(row)
        if idx % 50 == 0:
            log(f"  個股 {idx}/{len(stock_cand)}")

    # ---------- 題材層級(同集『同向』點名台股等權籃子) ----------
    # ⚠ schema 未把個股連到特定題材 → 用「同集且同立場(看多題材取看多個股)」近似,
    #   比「同集所有股」少誤歸因,但仍是粗略代理(同集多個同向題材會共用籃子)→ 僅探索、非結論。
    ep_stance_stocks = defaultdict(lambda: defaultdict(list))   # ep -> stance -> [code]
    for e in events:
        if e["level"] == "stock" and e.get("market") == "TW" and e.get("code"):
            mt = e.get("mention_type")
            stance = "bullish" if mt == "bullish_call" else ("bearish" if mt == "bearish_call" else None)
            if stance:
                ep_stance_stocks[e["ep"]][stance].append(e["code"])
    theme_cand = [e for e in events if e["level"] == "theme"
                  and e.get("mention_type") in ("bullish_call", "bearish_call")]
    log(f"題材可研究事件 {len(theme_cand)}；建同集同向籃子…")
    theme_rows = []
    for e in theme_cand:
        stance = "bullish" if e["mention_type"] == "bullish_call" else "bearish"
        codes = list(dict.fromkeys(ep_stance_stocks[e["ep"]].get(stance, [])))   # 同集同向、去重保序
        if len(codes) < 2:
            continue   # 同集無足夠同向點名台股 → 無法建籃子
        sign = 1.0 if stance == "bullish" else -1.0
        per_k = {k: [] for k in WINDOWS}
        bench_k = {}
        used = 0
        for c in codes:
            df = get_prices(c, gstart, gend)
            if df is None:
                continue
            i = _entry_idx(df, e["t0_date"])
            if i is None:
                continue
            used += 1
            for k in WINDOWS:
                r = _ret(df, i, k)
                if r is not None:
                    per_k[k].append(r)
                    if k not in bench_k:
                        bench_k[k] = _bench_ret(bench, df.index[i], df.index[i + k])
        if used < 2:
            continue
        row = {"ep": e["ep"], "t0_date": e["t0_date"], "theme": e["name"],
               "mention_type": e["mention_type"], "already_ran": e.get("already_ran", False),
               "basket_size": used}
        for k in WINDOWS:
            if per_k[k]:
                basket = sum(per_k[k]) / len(per_k[k])      # 等權
                row[f"strat_{k}"] = round(sign * basket, 4)
                br = bench_k.get(k)
                if br is not None:
                    row[f"excess_{k}"] = round(sign * basket - sign * br, 4)
        theme_rows.append(row)

    # ---------- 彙總 ----------
    def split(rows):
        return {
            "all": agg(rows, "excess"),
            "bullish": agg([r for r in rows if r["mention_type"] == "bullish_call"], "excess"),
            "bearish": agg([r for r in rows if r["mention_type"] == "bearish_call"], "excess"),
            "ex_alreadyran": agg([r for r in rows if not r["already_ran"]], "excess"),
        }
    results = {
        "meta": {"n_episodes": n_eps, "n_events": len(events),
                 "stock_events": len(stock_rows), "theme_events": len(theme_rows),
                 "missing_codes": sorted(missing), "windows": WINDOWS,
                 "price_source": "yfinance auto_adjust", "note": "pilot-grade, survivorship-biased-up"},
        "stock": split(stock_rows),
        # ⚠ 非嚴格逐日 market-model CAR;是「持有報酬 − β×0050持有報酬」的 proxy,
        #   只能說「此 pilot+簡化β下未見市場β以外的中位超額」,不等於證明無 alpha。
        "stock_beta_adj_excess_proxy": {
            k: round(st.median([r[f"badj_{k}"] for r in stock_rows
                                if r.get(f"badj_{k}") is not None]), 4)
            for k in WINDOWS
            if [r for r in stock_rows if r.get(f"badj_{k}") is not None]},
        "theme": split(theme_rows),
        "runup_median": {  # 反向因果:看多事件 事件前漲幅中位
            "bullish": round(st.median([r["runup"] for r in stock_rows
                              if r["mention_type"] == "bullish_call" and r.get("runup") is not None]), 4),
        },
    }
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=True, indent=2), encoding="utf-8")
    log(f"結果寫入 {OUT_JSON}")
    if missing:
        log(f"⚠ 倖存偏誤:yfinance 查無 {len(missing)} 檔(下市/換號),已排除(結果偏高)")

    render_html(results)
    print_summary(results)
    return 0


def print_summary(R):
    log("=" * 64)
    for level, label in [("stock", "個股層級"), ("theme", "題材層級(同集籃子)")]:
        log(f"=== {label} ===")
        for grp, gl in [("bullish", "看多"), ("bearish", "看空")]:
            d = R[level][grp]
            for k in WINDOWS:
                if k in d:
                    m = d[k]
                    log(f"  {gl} {k:>3}日 n={m['n']:>3} 策略中位{m['strat_median']*100:+5.1f}% "
                        f"超額中位{(m['excess_median'] or 0)*100:+5.1f}% 贏0050 {m['beat0050']}%")
    if R.get("stock_beta_adj_excess_proxy"):
        log("個股 β調整持有超額 proxy 中位: "
            + " ".join(f"{k}日 {v*100:+.1f}%" for k, v in R["stock_beta_adj_excess_proxy"].items())
            + "(≈0 只代表此樣本未見β外超額,非證明無alpha)")
    log(f"看多事件 事件前60日漲幅中位: {R['runup_median']['bullish']*100:+.1f}%(反向因果指標)")
    log("=" * 64)
    log("⚠ pilot-grade:yfinance(倖存偏誤偏高)、題材籃子為粗略代理、β proxy 非嚴格CAR、"
        "未 bootstrap/產業中性化;結論=『不支持看多穩贏0050』,非『證明無alpha』,非預測")


def _tbl(d, title):
    rows = ""
    for k in WINDOWS:
        if k not in d:
            continue
        m = d[k]
        exc = f"{(m['excess_median'] or 0)*100:+.1f}%" if m['excess_median'] is not None else "—"
        beat = f"{m['beat0050']}%" if m['beat0050'] is not None else "—"
        rows += (f"<tr><td>{k} 日</td><td>{m['n']}</td><td>{m['strat_median']*100:+.1f}%</td>"
                 f"<td>{m['strat_wmean']*100:+.1f}%</td><td><b>{exc}</b></td>"
                 f"<td>{m['win']}%</td><td><b>{beat}</b></td></tr>")
    return (f"<h4>{title}</h4><table><tr><th>窗格</th><th>n</th><th>策略中位</th>"
            f"<th>策略winsor均</th><th>vs0050超額中位</th><th>勝率</th><th>贏0050</th></tr>{rows}</table>")


def render_html(R):
    m = R["meta"]
    badj = " · ".join(f"{k}日 {v*100:+.1f}%"
                      for k, v in R.get("stock_beta_adj_excess_proxy", {}).items()) or "資料不足"
    html = f"""<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">
<title>股癌題材後續漲跌 × 0050 事件研究</title><style>
body{{font-family:-apple-system,"Noto Sans TC",sans-serif;max-width:920px;margin:24px auto;padding:0 16px;color:#222;line-height:1.6}}
h1{{font-size:22px}} h2{{font-size:18px;border-left:4px solid #7a9285;padding-left:8px;margin-top:28px}}
h4{{margin:14px 0 4px}} table{{border-collapse:collapse;width:100%;font-size:14px;margin-bottom:8px}}
th,td{{border:1px solid #ddd;padding:5px 8px;text-align:center}} th{{background:#faf7f2}}
.warn{{background:#fff7ed;border:1px solid #fdba74;padding:10px 14px;border-radius:6px;font-size:13px}}
.key{{background:#f0f5f2;border:1px solid #7a9285;padding:10px 14px;border-radius:6px}}
small{{color:#666}}</style></head><body>
<h1>《股癌》題材後續漲跌 × 0050 常抱 — 事件研究</h1>
<p><small>樣本:{m['n_episodes']} 集 / {m['n_events']} 事件(2020–2026)。可研究個股事件 {m['stock_events']}、題材事件 {m['theme_events']}。
價格:{m['price_source']}。</small></p>
<div class="key"><b>核心發現(歷史對照,非預測)</b><br>
• <b>個股看多沒有明顯贏 0050</b>:看下表「贏 0050 比率」——&lt;50% 代表還不如直接買 0050(漲幅多來自大盤 beta,非選股力)。<br>
• β 調整持有超額 proxy(拆掉大盤)個股中位:{badj}。<b>≈0 僅代表此 pilot 樣本未見 β 外超額,不等於嚴格證明「無 alpha」。</b><br>
• 看多事件「事件前 60 日」漲幅中位 {R['runup_median']['bullish']*100:+.1f}% → 反向因果指標:題材常是<b>已先漲一段才被提到</b>(正對應「只記得被動元件漲翻」的近因偏誤)。<br>
• 題材層級(下方第二段)為<b>粗略代理,誤歸因風險高,僅供探索</b>,不作為結論。</div>
<h2>一、個股層級(明確點名 + 有方向)</h2>
{_tbl(R['stock']['bullish'],'看多 bullish_call')}
{_tbl(R['stock']['bearish'],'看空 bearish_call(超額=避開改抱0050的效益;策略中位為放空報酬)')}
{_tbl(R['stock']['ex_alreadyran'],'排除 already_ran(去掉「已先漲才提」的事件)')}
<h2>二、題材/類股層級(同集『同向』點名台股籃子;粗略代理、誤歸因風險、僅探索)</h2>
{_tbl(R['theme']['bullish'],'看多題材')}
{_tbl(R['theme']['bearish'],'看空題材')}
<h2>三、方法與限制(誠實揭露)</h2>
<div class="warn">
• <b>結論口徑</b>:本研究只能說「<b>pilot 樣本不支持『聽到股癌看多就穩定贏 0050』</b>」;<b>不</b>等於嚴格證明股癌無 alpha(那需要正式 market-model CAR + 信賴區間 + 因子控制 + 下市股納入 + 人工複核)。<br>
• <b>倖存偏誤</b>:yfinance 查無 {len(m['missing_codes'])} 檔(下市/換號)已排除 → 看多結果<b>偏高</b>,真實更差。<br>
• <b>題材層級僅探索</b>:籃子用「同集同向點名台股」近似,非該題材真正成分;同集多個同向題材會共用籃子 → 誤歸因風險,<b>方向不穩、不作結論</b>。<br>
• β proxy = 持有報酬 − β×0050持有報酬(事件前約 120 日迴歸),<b>非</b>嚴格逐日 market-model CAR。<br>
• 重疊窗格 + 同集多事件會高估有效樣本數;<b>未做</b> bootstrap 信賴區間、產業中性化、交易成本。<br>
• 價格用 yfinance 含息還原(非 FinMind);均值已 winsorize,<b>中位數/勝率為主</b>。進場=t0 次一交易日開盤,窗格為「進場開盤→第 k 交易日收盤」。<br>
• <b>本報告為歷史對照,非預測;不構成投資建議。</b></div>
</body></html>"""
    OUT_HTML.write_text(html, encoding="utf-8")
    log(f"HTML 報告寫入 {OUT_HTML}")


if __name__ == "__main__":
    sys.exit(main())
