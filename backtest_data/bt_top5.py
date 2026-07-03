"""離線回測:Top5 觀察名單「實際選股」的前瞻報酬 vs 大盤 —— 獨立驗證「熔斷(淨報酬為負)」是否成立。

做法:用 state/history.json 每日實際產出的 breakout_candidates(前 5 名,含代號)當「選股」,
以 TWSE 實際價格計算「報告日當天開盤買進 → 持有到當日收盤 / 次一交易日收盤」的報酬,
扣掉同期大盤(加權指數)報酬得「超額(淨)報酬」。看平均淨報酬與勝率,驗證模型自我熔斷是否合理。

n≈23 偏小,僅作方向性佐證;但用的是「系統真的選出的股票 + 真實成交價」,無前視偏誤。
"""
import json
import sys
import time
from pathlib import Path

import requests

HIST = Path(__file__).resolve().parent.parent / "state" / "history.json"
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _roc_to_iso(s):
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


def fetch_stock(stock_no, months):
    out = {}
    for ym in months:
        try:
            r = requests.get("https://www.twse.com.tw/exchangeReport/STOCK_DAY",
                             params={"response": "json", "date": ym, "stockNo": stock_no},
                             timeout=20, headers=H)
            for row in (r.json().get("data") or []):
                iso = _roc_to_iso(row[0])
                o, c = _num(row[3]), _num(row[6])
                if iso and o and c:
                    out[iso] = (o, c)
        except Exception as e:
            print(f"[{stock_no}] {ym} 失敗: {e}", file=sys.stderr)
        time.sleep(0.4)   # 友善 TWSE,避免限流
    return out


def fetch_taiex(months):
    out = {}
    for ym in months:
        try:
            r = requests.get("https://www.twse.com.tw/indicesReport/MI_5MINS_HIST",
                             params={"response": "json", "date": ym}, timeout=20, headers=H)
            for row in (r.json().get("data") or []):
                iso = _roc_to_iso(row[0])
                o, c = _num(row[1]), _num(row[4])
                if iso and o and c:
                    out[iso] = (o, c)
        except Exception as e:
            print(f"[taiex] {ym} 失敗: {e}", file=sys.stderr)
        time.sleep(0.4)
    return out


def main():
    hist = json.load(open(HIST, encoding="utf-8"))
    dates = [r.get("target_session_date") or r.get("date") for r in hist]
    months = sorted({d[:7].replace("-", "") + "01" for d in dates if d})

    # 每日前 5 名選股(以 attention_score / ranking_score 由高到低)
    picks_by_date = {}
    codes = set()
    for r in hist:
        d = r.get("target_session_date") or r.get("date")
        cands = r.get("breakout_candidates") or []
        cands = sorted(cands, key=lambda c: c.get("attention_score",
                       c.get("ranking_score", c.get("score", 0))), reverse=True)[:5]
        cs = [str(c.get("code")) for c in cands if c.get("code")]
        if cs:
            picks_by_date[d] = cs
            codes.update(cs)
    print(f"history n={len(hist)}; 有選股的交易日={len(picks_by_date)}; 不重複個股={len(codes)}")

    taiex = fetch_taiex(months)
    px = {c: fetch_stock(c, months) for c in sorted(codes)}

    tdays = sorted(taiex)

    def next_day(d):
        i = tdays.index(d) if d in tdays else -1
        return tdays[i + 1] if 0 <= i < len(tdays) - 1 else None

    # 兩種持有:當日(開→收)、次日(開→次一交易日收)
    for horizon, label in (("intraday", "當日 開→收"), ("nextclose", "開→次一交易日收")):
        stock_rets, net_rets, wins = [], [], 0
        n_pairs = 0
        for d, cs in picks_by_date.items():
            if d not in taiex:
                continue
            t_o, t_c = taiex[d]
            if horizon == "intraday":
                t_exit = t_c
            else:
                nd = next_day(d)
                if not nd or nd not in taiex:
                    continue
                t_exit = taiex[nd][1]
            mkt = (t_exit / t_o - 1) * 100
            for c in cs:
                series = px.get(c) or {}
                if d not in series:
                    continue
                s_o, s_c = series[d]
                if horizon == "intraday":
                    s_exit = s_c
                else:
                    nd = next_day(d)
                    if not nd or nd not in series:
                        continue
                    s_exit = series[nd][1]
                ret = (s_exit / s_o - 1) * 100
                net = ret - mkt
                stock_rets.append(ret)
                net_rets.append(net)
                wins += 1 if net > 0 else 0
                n_pairs += 1
        if n_pairs:
            avg_ret = sum(stock_rets) / n_pairs
            avg_net = sum(net_rets) / n_pairs
            print(f"\n[{label}] 配對 n={n_pairs}")
            print(f"  平均個股報酬 {avg_ret:+.3f}%  |  平均淨報酬(扣大盤) {avg_net:+.3f}%  "
                  f"|  勝率(淨>0) {wins/n_pairs*100:.0f}%")
        else:
            print(f"\n[{label}] 無有效配對")


if __name__ == "__main__":
    main()
