"""長窗回測(GitHub Actions 跑;那裡 Yahoo 與 TAIFEX 都連得到,本機 Yahoo 被 geo-block)。

三段:
  A. US-only us_beta:US-implied 開盤跳空對 us_combo 的 OLS 有效 beta + MAE 掃描(複驗先驗)。
  B. 全合成(US+夜盤)模型:照產線 pred = w×us_beta×us_combo + (1−w)×night_pct,
     用 TAIFEX 台指期夜盤史 + Yahoo,對 (us_beta, 混合權重 w) 做網格,找最小 MAE 的最終預測設定
     —— 解掉「US-only 回測未含夜盤」的但書,並順便檢驗 0.70/0.30 混合權重是否最佳。
  C. 長抱策略 bake-off:對股利還原(adjusted)0050/00662/2330(~5 年)比較
     買進持有 / 趨勢過濾(MA200)(同基準,CAGR/最大回撤/年化波動/Sharpe/在市比例),
     另以資金加權比較 定期定額 vs 價值加碼DCA,看哪種對長抱者較佳。

對齊:台股 d 日開盤 ← 前一美股交易日 us_combo + 前一台股交易日「盤後」夜盤(皆 overnight),無前視。
執行:python backtest_runner.py(workflow: .github/workflows/backtest.yml)
"""
import csv
import io
import sys
import datetime as dt

import requests

US_BETA_PRIOR = 0.23
H = {"User-Agent": "Mozilla/5.0"}


# ---------- Yahoo (Actions 可連) ----------
def _yf():
    import yfinance as yf
    return yf


def y_close(ticker, period="2y", adjust=False):
    try:
        h = _yf().Ticker(ticker).history(period=period, interval="1d", auto_adjust=adjust)
    except Exception as e:
        print(f"[{ticker}] 抓取失敗 {type(e).__name__}", file=sys.stderr)
        return {}
    if h is None or h.empty:
        return {}
    return {d.strftime("%Y-%m-%d"): float(r["Close"])
            for d, r in h.iterrows() if r.get("Close") == r.get("Close")}


def y_open_close(ticker, period="2y"):
    try:
        h = _yf().Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    except Exception as e:
        print(f"[{ticker}] 抓取失敗 {type(e).__name__}", file=sys.stderr)
        return {}
    if h is None or h.empty:
        return {}
    return {d.strftime("%Y-%m-%d"): (float(r["Open"]), float(r["Close"]))
            for d, r in h.iterrows()
            if r.get("Open") == r.get("Open") and r.get("Close") == r.get("Close")}


def pct_by_date(close_map):
    days = sorted(close_map)
    return {days[i]: (close_map[days[i]] / close_map[days[i - 1]] - 1) * 100
            for i in range(1, len(days)) if close_map[days[i - 1]]}


# ---------- TAIFEX 台指期夜盤史(TW 來源) ----------
def taifex_night_pct(years=2):
    """{台股交易日: 該日盤後(夜盤)相對日盤收盤 %}。與 morning_report 定義一致:
    night_pct = (盤後收盤 − 一般收盤)/一般收盤 ×100,近月(跳過週選 W)。逐季抓避免單次過大。"""
    out = {}
    today = dt.date(2026, 6, 16)   # 由 Actions 當日覆寫無妨;此處僅決定抓取起點
    try:
        today = dt.datetime.now().date()
    except Exception:
        pass
    start = today - dt.timedelta(days=365 * years + 10)
    cur = start
    while cur < today:
        nxt = min(cur + dt.timedelta(days=90), today)
        try:
            r = requests.post("https://www.taifex.com.tw/cht/3/futDataDown", timeout=30, headers=H,
                              data={"down_type": "1", "commodity_id": "TX",
                                    "queryStartDate": cur.strftime("%Y/%m/%d"),
                                    "queryEndDate": nxt.strftime("%Y/%m/%d")})
            text = r.content.decode("big5", errors="replace")
            rows = list(csv.reader(io.StringIO(text)))
            if rows:
                hdr = rows[0]
                def col(*names):
                    return next((i for i, c in enumerate(hdr) if any(n in c for n in names)), None)
                di, si, mi = col("交易日期"), col("交易時段", "盤別"), col("到期月份", "契約月份")
                ci = next((i for i, c in enumerate(hdr) if "收盤" in c and "結算" not in c), None)
                if None in (di, ci, si, mi):
                    cur = nxt + dt.timedelta(days=1)
                    continue
                # 依「交易日 × 合約月份」分組,再取前月(排序最前、同時有日盤+盤後者)算 night_pct,
                # 確保日盤與盤後是同一口合約(換倉/結算日不會張冠李戴)。
                per_date = {}
                for row in rows[1:]:
                    if not row or len(row) <= max(di, ci, si, mi):
                        continue
                    mon = row[mi].strip()
                    if "W" in mon:
                        continue
                    try:
                        close_v = float(row[ci].replace(",", ""))
                    except Exception:
                        continue
                    d = row[di].strip().replace("/", "-")
                    sess = row[si].strip()
                    key = "night" if ("盤後" in sess or "夜盤" in sess) else "day"
                    per_date.setdefault(d, {}).setdefault(mon, {}).setdefault(key, close_v)
                for d, months in per_date.items():
                    for mon in sorted(months):
                        s = months[mon]
                        if s.get("day") and s.get("night"):
                            out[d] = (s["night"] - s["day"]) / s["day"] * 100
                            break
        except Exception as e:
            print(f"[taifex] {cur} 失敗 {type(e).__name__}", file=sys.stderr)
        cur = nxt + dt.timedelta(days=1)
    return out


def _mae_stats(errs):
    n = len(errs)
    if not n:
        return None
    return n, sum(abs(e) for e in errs) / n, sum(errs) / n


# ---------- A. US-only ----------
def section_a():
    print("=== A) US-only us_beta(~2 年)===")
    sox, tsm = pct_by_date(y_close("^SOX")), pct_by_date(y_close("TSM"))
    twii = y_open_close("^TWII")
    if not (sox and tsm and twii):
        print("Yahoo 不可用(本機 geo-block 屬正常,請在 Actions 跑)。")
        return None
    td = sorted(twii)
    us_days = sorted(set(sox) | set(tsm))
    rows = []
    for i in range(1, len(td)):
        d, pc, op = td[i], twii[td[i - 1]][1], twii[td[i]][0]
        u = next((x for x in reversed(us_days) if x < d), None)
        if not (pc and u):
            continue
        parts = [(sox[u] * 1.05, 0.40)] if u in sox else []
        if u in tsm:
            parts.append((tsm[u], 0.30))
        if not parts:
            continue
        combo = sum(v * w for v, w in parts) / sum(w for _, w in parts)
        rows.append((combo, (op / pc - 1) * 100))
    if len(rows) < 30:
        print(f"樣本不足 n={len(rows)}")
        return None
    sxx = sum(x * x for x, _ in rows)
    ols = sum(x * y for x, y in rows) / sxx if sxx else float("nan")
    print(f"n={len(rows)}  OLS 經驗 beta={ols:.3f}(現行先驗 {US_BETA_PRIOR})")
    print("  beta |  MAE% | 偏誤%")
    for b in (0.20, 0.23, 0.27, 0.31, 0.40):
        st = _mae_stats([y - b * x for x, y in rows])
        print(f"  {b:.2f} | {st[1]:.3f} | {st[2]:+.3f}")
    return rows


# ---------- B. 全合成(US + 夜盤)----------
def section_b():
    print("\n=== B) 全合成模型 pred = w×beta×us_combo + (1−w)×night(~2 年,含 TAIFEX 夜盤)===")
    sox, tsm = pct_by_date(y_close("^SOX")), pct_by_date(y_close("TSM"))
    twii = y_open_close("^TWII")
    night = taifex_night_pct(2)
    if not (sox and tsm and twii and night):
        print(f"資料不齊(twii={len(twii)} night={len(night)} sox={len(sox)});Yahoo 需在 Actions。")
        return
    td = sorted(twii)
    us_days = sorted(set(sox) | set(tsm))
    samples = []   # (us_combo, night_pct, actual_gap)
    for i in range(1, len(td)):
        d, pc, op = td[i], twii[td[i - 1]][1], twii[td[i]][0]
        prev_tw = td[i - 1]
        u = next((x for x in reversed(us_days) if x < d), None)
        if not (pc and u and prev_tw in night):
            continue
        parts = [(sox[u] * 1.05, 0.40)] if u in sox else []
        if u in tsm:
            parts.append((tsm[u], 0.30))
        if not parts:
            continue
        combo = sum(v * w for v, w in parts) / sum(w for _, w in parts)
        samples.append((combo, night[prev_tw], (op / pc - 1) * 100))
    if len(samples) < 30:
        print(f"配對樣本不足 n={len(samples)}")
        return
    print(f"配對 n={len(samples)}(US+夜盤+實際開盤齊全)")
    print("  找最小 MAE 的 (beta, 夜盤權重 1−w):")
    best = None
    for w_us in (0.70, 0.60, 0.50, 0.40, 0.30):
        for beta in (0.20, 0.23, 0.27, 0.31, 0.40):
            errs = [g - (w_us * beta * c + (1 - w_us) * n) for c, n, g in samples]
            st = _mae_stats(errs)
            tag = "  ← 現行" if (abs(w_us - 0.70) < 1e-9 and abs(beta - US_BETA_PRIOR) < 1e-9) else ""
            if best is None or st[1] < best[2]:
                best = (w_us, beta, st[1])
            if w_us in (0.70, 0.50, 0.30) and beta in (0.23, 0.31):
                print(f"  US權重 {w_us:.2f}/夜盤 {1-w_us:.2f}, beta {beta:.2f}: MAE {st[1]:.3f}%  偏誤 {st[2]:+.3f}%{tag}")
    print(f"  → 最小 MAE:US權重 {best[0]:.2f}/夜盤 {1-best[0]:.2f}, beta {best[1]:.2f}(MAE {best[2]:.3f}%)")
    print("  (若最小 MAE 仍接近 現行 0.70/0.30+beta0.23,代表現行設定已好;夜盤權重明顯較高才考慮調整。)")


# ---------- C. 長抱策略 bake-off ----------
def _metrics(equity):
    """equity: 依日的部位市值序列(已含現金)。回 CAGR/最大回撤/年化波動/Sharpe。"""
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1]]
    if not rets:
        return None
    import statistics
    days = len(equity)
    cagr = (equity[-1] / equity[0]) ** (252 / max(days, 1)) - 1
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    vol = statistics.pstdev(rets) * (252 ** 0.5)
    sharpe = (statistics.mean(rets) * 252) / vol if vol else 0
    return cagr * 100, mdd * 100, vol * 100, sharpe


def section_c():
    print("\n=== C) 長抱策略 bake-off(股利還原 adjusted,~5 年)===")
    for tk, name in (("00662.TW", "00662 富邦NASDAQ"), ("0050.TW", "0050 元大台灣50"),
                     ("2330.TW", "2330 台積電")):
        cm = y_close(tk, period="5y", adjust=True)
        if len(cm) < 250:
            print(f"  {name}: 資料不足(n={len(cm)})")
            continue
        days = sorted(cm)
        px = [cm[d] for d in days]
        n = len(px)
        # MA200:不足 200 日回 None(暖身期不交易訊號,維持持有,避免用短均誤判)
        ma200 = [(sum(px[i - 199:i + 1]) / 200 if i >= 199 else None) for i in range(n)]

        # --- 同基準(期初 $1 一次投入,時間加權)---
        bh = [px[i] / px[0] for i in range(n)]
        tf, pos, time_in = [1.0], 1.0, 0       # 趨勢過濾:收 > MA200 持有否則空手(暖身期持有)
        for i in range(1, n):
            tf.append(tf[-1] * px[i] / px[i - 1] if pos else tf[-1])
            if pos:
                time_in += 1
            m = ma200[i]
            pos = 1.0 if (m is None or px[i] > m) else 0.0
        bh_m, tf_m = _metrics(bh), _metrics(tf)
        print(f"\n  【{name}】({days[0]}→{days[-1]})  [同基準:期初一次投入]")
        print(f"    買進持有  : CAGR {bh_m[0]:+.1f}%  最大回撤 {bh_m[1]:.1f}%  年化波動 {bh_m[2]:.1f}%  Sharpe {bh_m[3]:.2f}")
        print(f"    趨勢過濾  : CAGR {tf_m[0]:+.1f}%  最大回撤 {tf_m[1]:.1f}%  年化波動 {tf_m[2]:.1f}%  "
              f"Sharpe {tf_m[3]:.2f}  在市 {time_in/n*100:.0f}%")

        # --- 不同基準(每月定額投入,資金加權;只在 DCA 與 價值加碼DCA 間互比)---
        seen, dca_sh, dca_in, vdca_sh, vdca_in = set(), 0.0, 0.0, 0.0, 0.0
        for i, d in enumerate(days):
            if d[:7] in seen:
                continue
            seen.add(d[:7])
            dca_sh += 1.0 / px[i]
            dca_in += 1.0
            # 用「前一交易日」的價/MA 訊號決定加碼倍數(避免用當日收盤決定當日買入 → 前視)
            m = ma200[i - 1] if i > 0 else None
            ref = px[i - 1] if i > 0 else px[i]
            mult = 1.0 if m is None else (2.0 if ref < m else (0.5 if ref > m * 1.1 else 1.0))
            vdca_sh += mult / px[i]
            vdca_in += mult
        print("    [不同基準:每月定額,資金加權總報酬,與上面非同基準、僅 DCA 之間互比]")
        print(f"    定期定額    : {(dca_sh*px[-1]/dca_in-1)*100:+.1f}%(投 {int(dca_in)} 期)")
        print(f"    價值加碼DCA : {(vdca_sh*px[-1]/vdca_in-1)*100:+.1f}%(<MA200 加碼、過熱減碼)")


def main():
    try:
        import yfinance  # noqa: F401
    except Exception as e:
        print(f"需要 yfinance:{e}", file=sys.stderr)
        return 1
    a = section_a()
    section_b()
    section_c()
    if a is None:
        print("\n⚠ 核心校驗資料(Yahoo)不可用 → 本次未產出有效回測結果(在 Actions 應正常)。",
              file=sys.stderr)
        return 2     # 讓 Actions 顯示失敗,避免「綠燈卻無有效回測」誤導
    return 0


if __name__ == "__main__":
    sys.exit(main())
