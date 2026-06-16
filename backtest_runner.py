"""長窗回測(在 GitHub Actions 跑;那裡抓得到 Yahoo,本機 Yahoo 被 geo-block)。

目的:用 ~2 年歷史複驗晨報加權開盤預測的「美股有效 beta(us_beta,現行先驗 0.31)」是否仍最佳,
並輸出最小化 MAE 的 beta、OLS 經驗 beta、方向命中率。樣本數百日,統計力遠勝本機 23 日。

對齊:台股某日 d 的開盤,反映的是「d 之前最近一個美股交易日」的隔夜變動(美股約台北 04:00 收)。
us_combo% = (SOX%×1.05×0.4 + TSM_ADR%×0.3)/0.7;TW 開盤跳空% = (^TWII open[d]/^TWII close[d−1] − 1)×100。
close→close 算美股日變動,無前視(用 d 之前的美股、預測 d 的開盤)。

執行:python backtest_runner.py  (Actions workflow: .github/workflows/backtest.yml)
"""
import sys

US_BETA_PRIOR = 0.31   # 與 morning_report.TAIEX_US_BETA_PRIOR 同步


def _pct_change_by_date(hist):
    """{date: close} → {date: 當日收盤對前一交易日收盤的 % 變動}(依日期排序)。"""
    days = sorted(hist)
    out = {}
    for i in range(1, len(days)):
        p0, p1 = hist[days[i - 1]], hist[days[i]]
        if p0:
            out[days[i]] = (p1 / p0 - 1) * 100
    return out


def main():
    try:
        import yfinance as yf
    except Exception as e:
        print(f"需要 yfinance:{e}", file=sys.stderr)
        return 1

    def hist_close(ticker, period="2y"):
        try:
            h = yf.Ticker(ticker).history(period=period, interval="1d")
        except Exception as e:
            print(f"[{ticker}] 抓取失敗: {type(e).__name__}", file=sys.stderr)
            return {}
        if h is None or h.empty:
            return {}
        return {d.strftime("%Y-%m-%d"): float(r["Close"])
                for d, r in h.iterrows() if r.get("Close") == r.get("Close")}

    def hist_open_close(ticker, period="2y"):
        try:
            h = yf.Ticker(ticker).history(period=period, interval="1d")
        except Exception as e:
            print(f"[{ticker}] 抓取失敗: {type(e).__name__}", file=sys.stderr)
            return {}
        if h is None or h.empty:
            return {}
        return {d.strftime("%Y-%m-%d"): (float(r["Open"]), float(r["Close"]))
                for d, r in h.iterrows()
                if r.get("Open") == r.get("Open") and r.get("Close") == r.get("Close")}

    print("=== 長窗回測:加權開盤預測 us_beta(~2 年)===")
    sox = _pct_change_by_date(hist_close("^SOX"))
    tsm = _pct_change_by_date(hist_close("TSM"))
    twii = hist_open_close("^TWII")
    if not (sox and tsm and twii):
        print("Yahoo 抓取失敗(本機 geo-block 屬正常,請在 GitHub Actions 執行)。", file=sys.stderr)
        return 2

    twii_days = sorted(twii)
    us_days = sorted(set(sox) | set(tsm))

    def last_us_before(tw_date):
        cand = [u for u in us_days if u < tw_date]
        return cand[-1] if cand else None

    rows = []   # (us_combo%, tw_open_gap%)
    for i in range(1, len(twii_days)):
        d = twii_days[i]
        prev_close = twii[twii_days[i - 1]][1]
        open_d = twii[d][0]
        if not prev_close:
            continue
        gap = (open_d / prev_close - 1) * 100
        u = last_us_before(d)
        if not u:
            continue
        s_pct, t_pct = sox.get(u), tsm.get(u)
        parts = []
        if s_pct is not None:
            parts.append((s_pct * 1.05, 0.40))
        if t_pct is not None:
            parts.append((t_pct, 0.30))
        if not parts:
            continue
        tw = sum(w for _, w in parts)
        combo = sum(v * w for v, w in parts) / tw
        rows.append((combo, gap))

    n = len(rows)
    if n < 30:
        print(f"配對樣本不足(n={n})")
        return 3
    sxx = sum(x * x for x, _ in rows)
    sxy = sum(x * y for x, y in rows)
    ols_beta = sxy / sxx if sxx else float("nan")
    print(f"配對交易日 n={n}")
    print(f"OLS 經驗 beta(過原點 Σxy/Σx²)= {ols_beta:.3f}   (現行先驗 {US_BETA_PRIOR})")
    print("\n beta 掃描(最小化 |實際跳空 − beta×us_combo| 的 MAE):")
    print("  beta |  MAE%  | 平均偏誤% | 方向命中%")
    best = None
    for beta in (0.20, 0.25, 0.31, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00, round(ols_beta, 2)):
        errs = [y - beta * x for x, y in rows]
        mae = sum(abs(e) for e in errs) / n
        bias = sum(errs) / n
        dh = sum(1 for x, y in rows if (y >= 0) == (beta * x >= 0)) / n * 100
        print(f"  {beta:.2f} | {mae:.3f} | {bias:+.3f}   | {dh:.0f}%")
        if best is None or mae < best[1]:
            best = (beta, mae)
    print(f"\n→ 最小 MAE 的 beta = {best[0]:.2f}(MAE {best[1]:.3f}%)。"
          f"若與現行 0.31 相近,代表保守設定仍最佳;若顯著不同,才考慮調整(仍需再檢視穩定度)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
