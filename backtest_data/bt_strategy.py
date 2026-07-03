"""離線回測:長抱者真正該關心的「部位/進場」策略(非選股)。資料 = TWSE 日線(本機可連)。

涵蓋:
  C6  定期定額(DCA) vs 一次買進(Lump) vs 逢低布局(Dip:現金等跌破 MA20×(1−k) 才投)
  A1/C8 進場擇時:只在「價<MA20×(1−k)」進場 的前瞻報酬 vs 隨時進場(驗證『可分批買』帶)
  C7  2330/00662 五五再平衡(月再平衡) vs 各自買進持有

期間取 TWSE 可得的近 ~24 個月日線。長抱者結論通常是「DCA/buy-hold 難被擇時打敗」,用數據確認。
"""
import sys
import time

import requests

H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
START_YM = (2024, 7)   # 約 24 個月
END_YM = (2026, 6)


def _roc_iso(s):
    try:
        y, m, d = s.split("/")
        return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return None


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def months(a, b):
    out, y, m = [], a[0], a[1]
    while (y, m) <= b:
        out.append(f"{y}{m:02d}01")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def fetch_close(stock_no, ms):
    out = {}
    for ym in ms:
        try:
            r = requests.get("https://www.twse.com.tw/exchangeReport/STOCK_DAY",
                             params={"response": "json", "date": ym, "stockNo": stock_no},
                             timeout=20, headers=H)
            for row in (r.json().get("data") or []):
                iso, c = _roc_iso(row[0]), _num(row[6])
                if iso and c:
                    out[iso] = c
        except Exception as e:
            print(f"[{stock_no}] {ym}: {e}", file=sys.stderr)
        time.sleep(0.35)
    return out


def ma(series_sorted, idx, win=20):
    if idx + 1 < win:
        return None
    vals = [series_sorted[i][1] for i in range(idx + 1 - win, idx + 1)]
    return sum(vals) / win


def main():
    ms = months(START_YM, END_YM)
    print(f"抓 TWSE 日線 {ms[0]}..{ms[-1]} ({len(ms)} 月)…")
    c00662 = fetch_close("00662", ms)
    c2330 = fetch_close("2330", ms)
    print(f"00662 天數={len(c00662)}, 2330 天數={len(c2330)}")
    s = sorted(c00662.items())                  # [(date, close)]
    px = dict(s)
    if len(s) < 60:
        print("資料不足,結束")
        return
    first, last = s[0][1], s[-1][1]
    total_ret = (last / first - 1) * 100
    print(f"\n00662 期間買進持有(Lump)總報酬: {total_ret:+.1f}%  ({s[0][0]}→{s[-1][0]})")

    # === C6:DCA vs Lump vs Dip ===
    # 每月首個交易日投入固定金額(=1 單位現金)。Lump:期初一次投入全部單位。
    month_firsts = []
    seen = set()
    for d, _ in s:
        ym = d[:7]
        if ym not in seen:
            seen.add(ym)
            month_firsts.append(d)
    n_months = len(month_firsts)

    # DCA
    dca_shares = sum((1.0 / px[d]) for d in month_firsts)
    dca_val = dca_shares * last
    dca_ret = (dca_val / n_months - 1) * 100      # 投入 n_months 單位現金

    # Lump:期初一次投入 n_months 單位現金
    lump_shares = n_months / first
    lump_ret = (lump_shares * last / n_months - 1) * 100

    # Dip:每月配 1 單位現金進「現金池」,任一交易日若 收<MA20×0.98 就把現金池全買進
    cash, dip_shares = 0.0, 0.0
    contrib_dates = set(month_firsts)
    for i, (d, c) in enumerate(s):
        if d in contrib_dates:
            cash += 1.0
        m20 = ma(s, i, 20)
        if cash > 0 and m20 and c < m20 * 0.98:
            dip_shares += cash / c
            cash = 0.0
    dip_val = dip_shares * last + cash            # 未投出的現金以面值計
    dip_ret = (dip_val / n_months - 1) * 100

    print("\n=== C6 00662:定期定額 vs 一次買進 vs 逢低布局(投入 {} 個月、各 1 單位現金) ===".format(n_months))
    print(f"  一次買進 Lump : 總報酬 {lump_ret:+.1f}%")
    print(f"  定期定額 DCA  : 總報酬 {dca_ret:+.1f}%")
    print(f"  逢低布局 Dip  : 總報酬 {dip_ret:+.1f}%  (跌破 MA20×0.98 才把累積現金投入)")

    # === A1/C8:進場擇時(只在 收<MA20×(1−k) 進場)的「前瞻 20 日報酬」 vs 隨時進場 ===
    print("\n=== A1/C8 00662 進場擇時:各情境『進場後 20 交易日』平均報酬 ===")
    for k in (0.0, 0.01, 0.02, 0.03):
        rets_dip, rets_all = [], []
        for i in range(len(s) - 20):
            c = s[i][1]
            fwd = (s[i + 20][1] / c - 1) * 100
            rets_all.append(fwd)
            m20 = ma(s, i, 20)
            if m20 and c < m20 * (1 - k):
                rets_dip.append(fwd)
        avg_all = sum(rets_all) / len(rets_all) if rets_all else 0
        if rets_dip:
            avg_dip = sum(rets_dip) / len(rets_dip)
            print(f"  跌破 MA20×{1-k:.2f} 進場: 平均 20 日後 {avg_dip:+.2f}%  (n={len(rets_dip)})  "
                  f"| 隨時進場基準 {avg_all:+.2f}%  → 優勢 {avg_dip-avg_all:+.2f}%")
        else:
            print(f"  跌破 MA20×{1-k:.2f}: 無進場日")

    # === C7:2330/00662 五五再平衡(月) vs 各自買進持有 ===
    common = sorted(set(c00662) & set(c2330))
    if len(common) >= 60:
        cs2330 = {d: c2330[d] for d in common}
        cs662 = {d: c00662[d] for d in common}
        d0, dN = common[0], common[-1]
        bh_2330 = (cs2330[dN] / cs2330[d0] - 1) * 100
        bh_662 = (cs662[dN] / cs662[d0] - 1) * 100
        # 5050 再平衡:期初各 0.5 單位市值,每月首日重置回 50/50
        units_2330 = 0.5 / cs2330[d0]
        units_662 = 0.5 / cs662[d0]
        seen2 = {d0[:7]}
        for d in common[1:]:
            v2330 = units_2330 * cs2330[d]
            v662 = units_662 * cs662[d]
            if d[:7] not in seen2:
                seen2.add(d[:7])
                tot = v2330 + v662
                units_2330 = (tot / 2) / cs2330[d]
                units_662 = (tot / 2) / cs662[d]
        rebal_val = units_2330 * cs2330[dN] + units_662 * cs662[dN]
        rebal_ret = (rebal_val - 1) * 100
        print("\n=== C7 2330/00662 配置({}→{}) ===".format(d0, dN))
        print(f"  全 2330 買進持有 : {bh_2330:+.1f}%")
        print(f"  全 00662 買進持有: {bh_662:+.1f}%")
        print(f"  五五月再平衡     : {rebal_ret:+.1f}%  (vs 靜態 50/50 {(bh_2330+bh_662)/2:+.1f}%)")


if __name__ == "__main__":
    main()
