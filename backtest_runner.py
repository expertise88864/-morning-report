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
import json
import sys
import time
import datetime as dt
from pathlib import Path

import requests

NIGHT_FILE = Path(__file__).resolve().parent / "taifex_night_history.json"

US_BETA_PRIOR = 0.31   # 與 morning_report.TAIEX_US_BETA_PRIOR 同步(全合成回測定案)
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


def y_ohlc(ticker, period="5y", adjust=True):
    """{日期: (Open, High, Low, Close)};供 D4 限價階梯用月內 Low 觸價判定。"""
    try:
        h = _yf().Ticker(ticker).history(period=period, interval="1d", auto_adjust=adjust)
    except Exception as e:
        print(f"[{ticker}] 抓取失敗 {type(e).__name__}", file=sys.stderr)
        return {}
    if h is None or h.empty:
        return {}
    out = {}
    for d, r in h.iterrows():
        o, hi, lo, c = r.get("Open"), r.get("High"), r.get("Low"), r.get("Close")
        if all(v == v for v in (o, hi, lo, c)):   # 排除 NaN
            out[d.strftime("%Y-%m-%d")] = (float(o), float(hi), float(lo), float(c))
    return out


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
        nxt = min(cur + dt.timedelta(days=28), today)   # TAIFEX futDataDown 範圍上限約 1 個月,逐月抓
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
        time.sleep(0.3)
        cur = nxt + dt.timedelta(days=1)
    return out


def load_night_pct(years=2):
    """夜盤序列:優先讀已入庫的 taifex_night_history.json(本機抓好、避免 Actions 對 TAIFEX
    的連通不確定),不足才即時抓。"""
    try:
        if NIGHT_FILE.exists():
            data = json.loads(NIGHT_FILE.read_text(encoding="utf-8"))
            if len(data) >= 100:
                return {k: float(v) for k, v in data.items()}
    except Exception as e:
        print(f"[night] 讀檔失敗 {e}", file=sys.stderr)
    return taifex_night_pct(years)


def _mae_stats(errs):
    n = len(errs)
    if not n:
        return None
    return n, sum(abs(e) for e in errs) / n, sum(errs) / n


# ---------- A. US-only ----------
def section_a(period="2y"):
    print(f"=== A) US-only us_beta(視窗 {period})===")
    sox, tsm = pct_by_date(y_close("^SOX", period)), pct_by_date(y_close("TSM", period))
    twii = y_open_close("^TWII", period)
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
    night = load_night_pct(2)
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
    print("  (若最小 MAE 仍接近 現行 0.70/0.30+beta0.31,代表現行設定已好;夜盤權重明顯較高才考慮調整。)")


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


def section_c(period="5y"):
    print(f"\n=== C) 長抱策略 bake-off(股利還原 adjusted,視窗 {period})===")
    print("  (00631L 為每日 2 倍槓桿,長抱有波動耗損;特別看『趨勢過濾 vs 買進持有』的回撤差。"
          "上市較晚者(00662~2016、00631L~2014)實際區間以其最早資料為準。)")
    for tk, name in (("00662.TW", "00662 富邦NASDAQ"), ("0050.TW", "0050 元大台灣50"),
                     ("2330.TW", "2330 台積電"), ("00631L.TW", "00631L 台灣50正2(2x槓桿)")):
        cm = y_close(tk, period=period, adjust=True)
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


# ---------- D. 逢低進場策略電池(現金流部署;公平、無前視、含成本)----------
# 設計+對抗驗證結論(2026-06):以下修掉了「天真版」的三個致命問題:
#   (1) 死碼陷阱:若平靜月也投滿 1x、逢跌才想加碼但沒存到錢,reserve 永遠是 0、階梯永不觸發、
#       退化成 flat DCA。修法:平靜/過熱月投 <1x(存彈藥),逢跌月才從 reserve 放出 >1x。
#   (2) 現金公平:所有 D 策略每月入金固定 C、共用「有上限 reserve + 期末掃入」,total_in 嚴格相等
#       (見 test_section_d 的 assert),差異純粹來自「同一桶錢落在哪些日子」,money-weighted 可直接比。
#   (3) 還原價前視:訊號(MA/RSI/回撤)一律用「未還原 raw 收盤」(=券商/使用者實際看到的),
#       損益(成交/估值)用「股利還原 adjusted 收盤」(總報酬),訊號只用前一交易日 i-1。
# 成本:買入手續費 max(最低 20 元, 金額×0.1425%×折數);D6 趨勢過濾另計賣出手續費+證交稅。
# 警語:樣本 2011–2026 為單一偏多 regime,結論為條件式;輸出 force-deploy 命中率讓「reserve 是否真動用」透明。
FEE_DISC = 0.28                       # 電子下單常見折數(約 2.8 折)
FEE_RATE = 0.001425 * FEE_DISC        # 買/賣手續費率
MIN_FEE = 20.0                        # 台股單筆最低手續費(元);小額定額會被它吃掉
CONTRIB = 10000.0                     # 每月供款(元);用真實金額才能反映最低手續費地板
SELL_TAX = {"2330.TW": 0.003, "00662.TW": 0.001, "0050.TW": 0.001, "00631L.TW": 0.001}
_LONGHOLD = (("00662.TW", "00662 富邦NASDAQ"), ("0050.TW", "0050 元大台灣50"),
             ("2330.TW", "2330 台積電"), ("00631L.TW", "00631L 台灣50正2(2x槓桿)"))


def _ma(px, win):
    """簡單移動平均;不足 win 回 None(暖身期)。"""
    return [(sum(px[i - win + 1:i + 1]) / win if i >= win - 1 else None) for i in range(len(px))]


def _rolling_high(px, win):
    """過去 win 日(含當日)滾動最高;呼叫端用 i-1 取『截至前一日』高點以免前視。"""
    return [max(px[max(0, i - win + 1):i + 1]) for i in range(len(px))]


def _rsi_wilder(px, win=14):
    """Wilder RSI;index k 僅用 px[0..k];前 win 日回 None。"""
    n = len(px)
    rsi = [None] * n
    if n <= win:
        return rsi
    gains = sum(max(px[i] - px[i - 1], 0.0) for i in range(1, win + 1))
    losses = sum(max(px[i - 1] - px[i], 0.0) for i in range(1, win + 1))
    ag, al = gains / win, losses / win
    rsi[win] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(win + 1, n):
        ch = px[i] - px[i - 1]
        ag = (ag * (win - 1) + max(ch, 0.0)) / win
        al = (al * (win - 1) + max(-ch, 0.0)) / win
        rsi[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return rsi


def _buy(amount, px):
    """投入 amount 元、扣含地板手續費後買到的股數。"""
    if amount <= 0:
        return 0.0
    fee = max(MIN_FEE, amount * FEE_RATE)
    return max(0.0, amount - fee) / px


def _deploy_reserve(days, exec_px, mult_fn, cap_months, contrib=CONTRIB, idle_daily=0.0):
    """每月首交易日入金 contrib 進 reserve;當月想投 mult×contrib,實投=min(want, reserve);
    平靜/過熱月 mult<1 → 把錢留 reserve;逢跌月 mult>1 → 從 reserve 放出。reserve 上限
    cap_months×contrib,溢出當月強制投入(force-deploy);期末殘餘 reserve 以末日收盤掃入。
    mult_fn(i) 用『前一交易日 i-1』訊號回傳倍數(暖身回 1.0)。
    回 (total_in, shares, force_hits, months)。total_in 對所有策略相等(=月數×contrib)。"""
    seen = set()
    reserve = shares = total_in = 0.0
    force = months = 0
    cap = cap_months * contrib
    for i, d in enumerate(days):
        if idle_daily:
            reserve *= (1 + idle_daily)
        if d[:7] in seen:
            continue
        seen.add(d[:7])
        months += 1
        reserve += contrib
        total_in += contrib
        want = mult_fn(i) * contrib
        spend = min(want, reserve)
        if spend > 0:
            shares += _buy(spend, exec_px[i])
            reserve -= spend
        if reserve > cap:
            over = reserve - cap
            shares += _buy(over, exec_px[i])
            reserve -= over
            force += 1
    if reserve > 1e-9:                       # 期末掃入 → total_in 與 flat 嚴格相等
        shares += _buy(reserve, exec_px[-1])
        reserve = 0.0
    return total_in, shares, force, months


def _mw_return(total_in, shares, final_px):
    """money-weighted 總報酬%(終值/總投入−1)。"""
    return (shares * final_px / total_in - 1) * 100 if total_in else float("nan")


def _aligned_raw_adj(tk, period):
    """回 (days, raw_close, adj_close):訊號用 raw、損益用 adj,取兩者共同日期。"""
    raw = y_close(tk, period=period, adjust=False)
    adj = y_close(tk, period=period, adjust=True)
    days = sorted(set(raw) & set(adj))
    return days, [raw[d] for d in days], [adj[d] for d in days]


def section_d(period="5y"):
    print(f"\n=== D) 逢低進場策略電池(現金流部署,公平·無前視·含成本,視窗 {period})===")
    print("  訊號用未還原 raw 收盤(=券商實際看到)、損益用股利還原 adj 收盤;每月供款 "
          f"{CONTRIB:.0f} 元,reserve 上限 6 期,期末掃入 → 各策略 total_in 相等,可直接比 money-weighted。")
    print("  ⚠ 樣本 2011–2026 為單一偏多 regime,結論為條件式;force% = reserve 被強制投入的月份比例"
          "(過高代表低點稀少、加碼機會有限)。")
    for tk, name in _LONGHOLD:
        days, raw, adj = _aligned_raw_adj(tk, period)
        if len(days) < 250:
            print(f"\n  【{name}】資料不足(n={len(days)})")
            continue
        ma200, ma50 = _ma(raw, 200), _ma(raw, 50)
        rh126 = _rolling_high(raw, 126)
        rsi = _rsi_wilder(raw, 14)

        def m_flat(i):
            return 1.0

        def m_drawdown(i):                    # D1:距 126 日高回撤分層
            if i == 0:
                return 1.0
            dd = raw[i - 1] / rh126[i - 1] - 1
            if dd <= -0.15:
                return 3.0
            if dd <= -0.10:
                return 2.0
            if dd <= -0.05:
                return 1.3
            return 0.7                        # 近高 → 少投存彈藥

        def m_dualma(i):                      # D2:距 MA200/MA50 分層
            if i == 0 or ma200[i - 1] is None:
                return 1.0
            ref, m200, m50 = raw[i - 1], ma200[i - 1], ma50[i - 1]
            if ref < m200:
                return 3.0
            if m50 is not None and ref < m50:
                return 1.5
            if m50 is not None and ref > m50 * 1.10:
                return 0.5                    # 過熱 → 減碼存彈藥
            return 0.8

        def m_rsi(i):                         # D3:RSI(14) 超賣分層
            if i == 0 or rsi[i - 1] is None:
                return 1.0
            r = rsi[i - 1]
            if r < 30:
                return 3.0
            if r < 35:
                return 2.0
            if r < 45:
                return 1.3
            if r >= 55:
                return 0.6                    # 強勢高檔 → 少投存彈藥
            return 1.0

        print(f"\n  【{name}】({days[0]}→{days[-1]})  [money-weighted,各策略 total_in 相等]")
        d0_ret, total_in_ref = 0.0, None
        for label, fn in (("D0 純定期定額(基準)", m_flat), ("D1 回撤分層加碼", m_drawdown),
                          ("D2 距MA200/50分層", m_dualma), ("D3 RSI<35加碼", m_rsi)):
            cap = 0 if fn is m_flat else 6
            tin, sh, force, mo = _deploy_reserve(days, adj, fn, cap)
            ret = _mw_return(tin, sh, adj[-1])
            avg_cost = tin / sh if sh else float("nan")
            if fn is m_flat:
                d0_ret, total_in_ref = ret, tin
                edge = ""
            else:
                edge = f"  (對 D0 {ret - d0_ret:+.1f}pp)"
                if abs(tin - total_in_ref) > 1.0:   # 公平性:total_in 必須與 D0 相等
                    edge += " ⚠total_in不符!"
            print(f"    {label:<16}: 報酬 {ret:+.1f}%  均價 {avg_cost:.1f}  "
                  f"force {force}/{mo}={force / mo * 100:.0f}%{edge}")
        # 健全性:所有策略 total_in 必須相等(公平性程式級保證)
        # (各次 _deploy_reserve 的 total_in 皆 = 月數×CONTRIB,已由 cap+期末掃入保證)

        # D6 趨勢過濾(MA200 in/out)稅後 vs 買進持有:time-weighted,口徑與上面 money-weighted 不可混比
        sell_cost = (1 - SELL_TAX.get(tk, 0.003)) * (1 - FEE_RATE)
        bh_eq = [1.0]
        for i in range(1, len(adj)):
            bh_eq.append(bh_eq[-1] * adj[i] / adj[i - 1])
        tf_eq, pos, switches = [1.0], 1.0, 0   # 訊號用 raw MA200,部位次日生效
        for i in range(1, len(adj)):
            tf_eq.append(tf_eq[-1] * adj[i] / adj[i - 1] if pos else tf_eq[-1])
            m = ma200[i]
            new_pos = 1.0 if (m is None or raw[i] > m) else 0.0
            if new_pos != pos:
                switches += 1
                tf_eq[-1] *= sell_cost if pos else (1 - FEE_RATE)  # 賣出計稅+費 / 買回計費
            pos = new_pos
        bh_m, tf_m = _metrics(bh_eq), _metrics(tf_eq)
        yrs = len(adj) / 252
        print(f"    [time-weighted,口徑另計] 買進持有: CAGR {bh_m[0]:+.1f}% 最大回撤 {bh_m[1]:.1f}% "
              f"Sharpe {bh_m[3]:.2f}")
        print(f"    [time-weighted,口徑另計] 趨勢過濾(稅後): CAGR {tf_m[0]:+.1f}% 最大回撤 {tf_m[1]:.1f}% "
              f"Sharpe {tf_m[3]:.2f}  切換 {switches} 次({switches/yrs:.1f}/年)")


def section_d_limit_ladder(period="5y"):
    """D4:每月限價階梯(月內 Low 觸 −X% 成交於 min(開盤,limit),否則月底市價)vs 月底市價買。"""
    print(f"\n=== D4) 每月限價階梯 vs 月底直接買(視窗 {period})===")
    print("  每月配 1 單位:limit=上月底×(1−X);月內 Low≤limit 首日成交於 min(開盤,limit),否則月底收盤。"
          "兩臂同金額、同節奏,純比『月內等低點 vs 月底直接買』。")
    print("  (用未還原 raw OHLC 模擬真實限價單;為純價格報酬、刻意忽略股利 —— 兩臂月內進場日不同,"
          "跨除息日時股利不必然抵銷,故限價 vs 月底的差值屬近似、僅看月內進場時點效果。)")
    for tk, name in _LONGHOLD:
        oh = y_ohlc(tk, period=period, adjust=False)   # 限價觸發/成交須用 raw(=實際掛單看到的價)
        days = sorted(oh)
        if len(days) < 250:
            print(f"  【{name}】資料不足(n={len(days)})")
            continue
        by_month = {}
        for d in days:
            by_month.setdefault(d[:7], []).append(d)
        months = sorted(by_month)
        for X in (0.02, 0.03, 0.05):
            lim_in = lim_sh = mkt_in = mkt_sh = 0.0
            filled = 0
            prev_close = None
            for ym in months:
                ds = by_month[ym]
                last_close = oh[ds[-1]][3]
                if prev_close is not None:
                    limit = prev_close * (1 - X)
                    fill_px = None
                    for d in ds:                       # 月內逐日找首個 Low≤limit
                        o, _h, lo, _c = oh[d]
                        if lo <= limit:
                            fill_px = min(o, limit)     # 跳空開低於 limit → 開盤成交
                            break
                    if fill_px is None:
                        fill_px = last_close            # 整月未觸 → 月底市價
                    else:
                        filled += 1
                    lim_in += CONTRIB
                    lim_sh += _buy(CONTRIB, fill_px)
                    mkt_in += CONTRIB
                    mkt_sh += _buy(CONTRIB, last_close)   # 對照:月底市價
                prev_close = last_close
            if mkt_sh and lim_sh:
                lim_ret = _mw_return(lim_in, lim_sh, oh[days[-1]][3])
                mkt_ret = _mw_return(mkt_in, mkt_sh, oh[days[-1]][3])
                n_mo = len(months) - 1
                print(f"  【{name}】X=-{X*100:.0f}%: 限價 {lim_ret:+.1f}% vs 月底 {mkt_ret:+.1f}% "
                      f"({lim_ret - mkt_ret:+.1f}pp);觸價月 {filled}/{n_mo}={filled/max(n_mo,1)*100:.0f}%")


def section_d_lump_vs_spread(period="5y"):
    """D5:一筆錢一次投入(Lump)vs 分 N 個月攤入(閒置現金 0% 下界 / 年化 1.5% 中性,兩版)。"""
    print(f"\n=== D5) 一筆錢:Lump vs 分批攤入(視窗 {period})===")
    print("  對所有起點月 t 各做一次:Lump 在 t 一次投 N 單位;攤入分 N 個月各投 1 單位(未投現金以 0%/年化1.5%持有)。"
          "持有到視窗末,跨起點平均;同起點集合(t..t+N−1 與持有期皆存在)。")
    for tk, name in _LONGHOLD:
        cm = y_close(tk, period=period, adjust=True)
        days = sorted(cm)
        if len(days) < 250:
            print(f"  【{name}】資料不足(n={len(days)})")
            continue
        first_days = []
        seen = set()
        for d in days:
            if d[:7] not in seen:
                seen.add(d[:7])
                first_days.append(d)
        final = cm[days[-1]]
        for tranche in (6, 3):
            for idle_ann in (0.0, 0.015):
                idle_m = (1 + idle_ann) ** (1 / 12) - 1
                lump_rets, spread_rets, lump_wins, n = [], [], 0, 0
                for s in range(len(first_days) - tranche + 1):
                    starts = first_days[s:s + tranche]
                    # Lump:t 一次投 tranche 單位
                    lump_sh = _buy(CONTRIB * tranche, cm[starts[0]])
                    lump_ret = lump_sh * final / (CONTRIB * tranche) - 1
                    # 攤入:每月 1 單位,未投現金以 idle 利率持有(機會成本)
                    cash = CONTRIB * tranche
                    sh = 0.0
                    for k, sd in enumerate(starts):
                        if k > 0:
                            cash *= (1 + idle_m) ** _months_between(starts[k - 1], sd)
                        sh += _buy(CONTRIB, cm[sd])
                        cash -= CONTRIB
                    # 終值 = 持股市值 + 攤付期間閒置現金所賺利息(否則 idle 利率版形同無作用)
                    spread_ret = (sh * final + cash) / (CONTRIB * tranche) - 1
                    lump_rets.append(lump_ret)
                    spread_rets.append(spread_ret)
                    lump_wins += 1 if lump_ret > spread_ret else 0
                    n += 1
                if n:
                    la = sum(lump_rets) / n * 100
                    sa = sum(spread_rets) / n * 100
                    worst_s = min(spread_rets) * 100
                    worst_l = min(lump_rets) * 100
                    print(f"  【{name}】分{tranche}月·閒置{idle_ann*100:.1f}%: "
                          f"Lump 均 {la:+.1f}%(最差 {worst_l:+.1f}%) vs 攤入 均 {sa:+.1f}%"
                          f"(最差 {worst_s:+.1f}%);Lump 勝率 {lump_wins/n*100:.0f}%(n={n})")


def _months_between(d1, d2):
    y1, m1 = int(d1[:4]), int(d1[5:7])
    y2, m2 = int(d2[:4]), int(d2[5:7])
    return max(1, (y2 - y1) * 12 + (m2 - m1))


def main():
    try:
        import yfinance  # noqa: F401
    except Exception as e:
        print(f"需要 yfinance:{e}", file=sys.stderr)
        return 1
    # A(us_beta)與 C/D(策略)各跑 5/10/15 年視窗;B(含夜盤)受 TAIFEX 夜盤史限制,固定 2 年。
    a_any = None
    for p in ("5y", "10y", "15y"):
        print(f"\n##################### 視窗 {p} #####################")
        r = section_a(p)
        a_any = a_any or r
        section_c(p)
        section_d(p)
        section_d_limit_ladder(p)
        section_d_lump_vs_spread(p)
    print("\n##################### B 全合成(2 年,夜盤史所限)#####################")
    section_b()
    if a_any is None:
        print("\n⚠ 核心校驗資料(Yahoo)不可用 → 本次未產出有效回測結果(在 Actions 應正常)。",
              file=sys.stderr)
        return 2     # 讓 Actions 顯示失敗,避免「綠燈卻無有效回測」誤導
    return 0


if __name__ == "__main__":
    sys.exit(main())
