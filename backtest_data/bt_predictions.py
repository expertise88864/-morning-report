"""離線回測:開盤預測(加權/2330/0050/00662)準確度 + us_beta / conflict_shrink 參數掃描。

資料源:
- state/history.json:每日「美股輸入」(sox/tsm/qqq/night_txf%、vix、外資台指期 OI)+ 當日「儲存的預測」。
- TWSE(本機可連;Yahoo 在本機被 geo-block):實際開盤
    * 加權指數:indicesReport/MI_5MINS_HIST(開盤指數/收盤指數)
    * 2330/0050/00662:exchangeReport/STOCK_DAY(開盤價/收盤價)

方法:用儲存的美股輸入「重放」calc_taiex_prediction(忠實複製 morning_report 公式),
比對 TWSE 實際開盤,計算 MAE/RMSE/平均帶號偏誤/方向命中率;並掃描 us_beta 與 conflict_shrink 開關。
n≈23 偏小:結論只當「方向性證據」,真要改係數仍須更長窗(GitHub Actions 可抓 Yahoo 長史)複驗。
"""
import json
import sys
from pathlib import Path

import requests

HIST = Path(__file__).resolve().parent.parent / "state" / "history.json"
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ---- morning_report 公式常數(與 calc_taiex_prediction 同步) ----
US_BETA_PRIOR = 0.31


def _roc_to_iso(s):
    # "115/06/01" -> "2026-06-01"
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


def _months_in_range(dates):
    ms = sorted({d[:7] for d in dates if d})
    return [m.replace("-", "") + "01" for m in ms]


def fetch_taiex_ohlc(months):
    """{iso_date: (open, close)} for 加權指數 via MI_5MINS_HIST(逐月)。"""
    out = {}
    for ym in months:
        try:
            r = requests.get("https://www.twse.com.tw/indicesReport/MI_5MINS_HIST",
                             params={"response": "json", "date": ym}, timeout=20, headers=H)
            for row in r.json().get("data") or []:
                iso = _roc_to_iso(row[0])
                if iso:
                    out[iso] = (_num(row[1]), _num(row[4]))   # 開盤, 收盤
        except Exception as e:
            print(f"[taiex] {ym} 失敗: {e}", file=sys.stderr)
    return out


def fetch_stock_ohlc(stock_no, months):
    """{iso_date: (open, close)} for 個股 via STOCK_DAY(逐月)。"""
    out = {}
    for ym in months:
        try:
            r = requests.get("https://www.twse.com.tw/exchangeReport/STOCK_DAY",
                             params={"response": "json", "date": ym, "stockNo": stock_no},
                             timeout=20, headers=H)
            for row in r.json().get("data") or []:
                iso = _roc_to_iso(row[0])
                if iso:
                    out[iso] = (_num(row[3]), _num(row[6]))   # 開盤價, 收盤價
        except Exception as e:
            print(f"[{stock_no}] {ym} 失敗: {e}", file=sys.stderr)
    return out


def prev_close_map(ohlc):
    """{iso_date: prev_trading_day_close}"""
    days = sorted(ohlc)
    out = {}
    for i, d in enumerate(days):
        if i > 0:
            out[d] = ohlc[days[i - 1]][1]
    return out


# ---- 重放 calc_taiex_prediction(%-空間) ----
def replay_taiex_gap_pct(rec, us_beta, apply_shrink):
    sox = rec.get("sox_pct")
    tsm = rec.get("tsm_pct")
    night = rec.get("night_txf_pct")
    parts = []
    if sox is not None:
        parts.append((sox * 1.05, 0.40))
    if tsm is not None:
        parts.append((tsm, 0.30))
    tw = sum(w for _, w in parts)
    combo = (sum(v * w for v, w in parts) / tw) if parts else None
    us_pred = us_beta * combo if combo is not None else None
    if us_pred is not None and night is not None:
        wp = 0.70 * us_pred + 0.30 * night
    elif us_pred is not None:
        wp = us_pred
    elif night is not None:
        wp = night
    else:
        return None
    if apply_shrink and wp:
        wp *= _shrink(rec, wp)
    return wp


def _shrink(rec, wp):
    """近似 _taiex_conflict_adjustment(以 history 可得欄位:sox/vix/foreign_oi;WTI/VIX9D 缺則略)。"""
    pen = 0.0
    foi = rec.get("taifex_foreign_oi") or 0
    if foi <= -20000 and wp > 0:
        pen += min(0.35, abs(foi) / 120000 * 0.35)
    elif foi >= 30000 and wp < 0:
        pen += min(0.25, abs(foi) / 140000 * 0.25)
    sox = rec.get("sox_pct")
    if sox is not None and sox >= 3.5 and wp > 0:
        pen += 0.10
    # signal_std:用 us 訊號(sox*1.05, tsm)與 night 的離散度近似
    vals = [v for v in (rec.get("sox_pct"), rec.get("tsm_pct"), rec.get("night_txf_pct"))
            if v is not None]
    if len(vals) >= 2:
        mu = sum(vals) / len(vals)
        std = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5
        if std >= 2.0:
            pen += min(0.12, std / 40)
    return max(0.55, min(1.0, 1.0 - pen))


def _stats(errs):
    if not errs:
        return None
    n = len(errs)
    mae = sum(abs(e) for e in errs) / n
    bias = sum(errs) / n
    rmse = (sum(e * e for e in errs) / n) ** 0.5
    return {"n": n, "mae": mae, "bias": bias, "rmse": rmse}


def main():
    hist = json.load(open(HIST, encoding="utf-8"))
    dates = [r.get("target_session_date") or r.get("date") for r in hist]
    months = _months_in_range(dates)
    print(f"history n={len(hist)} dates {dates[0]}..{dates[-1]} months={months}\n")

    taiex = fetch_taiex_ohlc(months)
    s2330 = fetch_stock_ohlc("2330", months)
    s0050 = fetch_stock_ohlc("0050", months)
    s00662 = fetch_stock_ohlc("00662", months)
    pc_taiex = prev_close_map(taiex)

    # === 1) 現行儲存預測的準確度(預測開盤 vs TWSE 實際開盤) ===
    print("=== 1) 現行預測準確度(誤差 = (實際開盤 − 預測)/預測 ×100%) ===")
    cur = {"加權": [], "2330": [], "0050": [], "00662": []}
    dirhit = {"加權": [], "2330": [], "0050": []}
    for rec in hist:
        d = rec.get("target_session_date") or rec.get("date")
        # 加權
        p = rec.get("pred_taiex")
        a = (taiex.get(d) or (None, None))[0]
        pc = pc_taiex.get(d)
        if p and a:
            cur["加權"].append((a - p) / p * 100)
            if pc:
                dirhit["加權"].append((a >= pc) == (p >= pc))
        # 2330(weighted_final)
        p = rec.get("weighted_final_2330")
        a = (s2330.get(d) or (None, None))[0]
        pc2 = (s2330.get(sorted(s2330)[max(0, sorted(s2330).index(d) - 1)])[1]
               if d in s2330 and sorted(s2330).index(d) > 0 else None)
        if p and a:
            cur["2330"].append((a - p) / p * 100)
            if pc2:
                dirhit["2330"].append((a >= pc2) == (p >= pc2))
        # 0050
        p = rec.get("pred_0050")
        a = (s0050.get(d) or (None, None))[0]
        if p and a:
            cur["0050"].append((a - p) / p * 100)
        # 00662(公允價,非開盤;預期偏差較大)
        p = rec.get("fair_00662")
        a = (s00662.get(d) or (None, None))[0]
        if p and a:
            cur["00662"].append((a - p) / p * 100)
    for k, errs in cur.items():
        st = _stats(errs)
        if st:
            dh = dirhit.get(k)
            dhs = f", 方向命中 {sum(dh)/len(dh)*100:.0f}% (n={len(dh)})" if dh else ""
            print(f"  {k:5}: n={st['n']:2d}  MAE={st['mae']:.2f}%  平均偏誤={st['bias']:+.2f}%  "
                  f"RMSE={st['rmse']:.2f}%{dhs}")
        else:
            print(f"  {k:5}: 無配對資料")

    # === 2) 加權 us_beta 掃描(重放 vs 實際開盤跳空%) ===
    print("\n=== 2) 加權 us_beta 掃描(目標:最小化 MAE;實際跳空% = (開盤/前收−1)) ===")
    print("  beta | shrink |  MAE%  | 平均偏誤% | 方向命中%")
    for beta in (0.15, 0.20, 0.23, 0.27, 0.31, 0.40, 0.50, 0.80):
        for shrink in (True, False):
            errs, dh = [], []
            for rec in hist:
                d = rec.get("target_session_date") or rec.get("date")
                a = (taiex.get(d) or (None, None))[0]
                pc = pc_taiex.get(d)
                if not (a and pc):
                    continue
                actual_gap = (a / pc - 1) * 100
                pred_gap = replay_taiex_gap_pct(rec, beta, shrink)
                if pred_gap is None:
                    continue
                errs.append(actual_gap - pred_gap)
                dh.append((actual_gap >= 0) == (pred_gap >= 0))
            st = _stats(errs)
            if st:
                print(f"  {beta:.2f} | {'on ' if shrink else 'off'}    | "
                      f"{st['mae']:.2f}  |  {st['bias']:+.2f}   | "
                      f"{sum(dh)/len(dh)*100:.0f}% (n={st['n']})")


if __name__ == "__main__":
    main()
