"""離線回測:Podcast 個股觀點(看多/看空)的後續報酬 —— 驗證該區的參考價值。

對每集 digest.tickers 中「台股(market=TW、4 位代號)」的看多/看空標記,
以該集 processed_at 當「觀點日」,用 TWSE 計算其後 5 / 20 交易日報酬,
並依方向調整(看多取 +報酬、看空取 −報酬)→ 看「跟著做」是否有正期望值與勝率。
樣本小(podcast 集數有限),僅作參考。
"""
import json
import sys
import time
from pathlib import Path

import requests

PJ = Path(__file__).resolve().parent.parent / "state" / "podcast_digest.json"
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


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


def main():
    pj = json.load(open(PJ, encoding="utf-8"))
    calls = []   # (date_iso, code, direction)
    for show in pj.values():
        for ep in show.get("episodes", []):
            d = (ep.get("processed_at") or ep.get("published") or "")[:10]
            for t in (ep.get("digest") or {}).get("tickers", []) or []:
                code = str(t.get("code", "")).strip()
                mkt = str(t.get("market", "")).upper()
                direc = str(t.get("direction", "")).lower()
                if mkt == "TW" and code.isdigit() and len(code) == 4 and direc in ("bullish", "bearish"):
                    if d and d[:4].isdigit():
                        calls.append((d, code, direc))
    if not calls:
        print("無台股看多/看空 podcast 觀點可測")
        return
    codes = sorted({c for _, c, _ in calls})
    ms = sorted({d[:7].replace("-", "") + "01" for d, _, _ in calls})
    # 多抓兩個月以涵蓋前瞻窗
    extra = set()
    for ym in ms:
        y, m = int(ym[:4]), int(ym[4:6])
        for _ in range(2):
            m += 1
            if m > 12:
                y, m = y + 1, 1
            extra.add(f"{y}{m:02d}01")
    ms = sorted(set(ms) | extra)
    print(f"台股 podcast 觀點 n={len(calls)}, 不重複個股={len(codes)}, 月份={ms}")

    px = {c: fetch_close(c, ms) for c in codes}

    for hz in (5, 20):
        dir_rets, raw_rets, wins, n = [], [], 0, 0
        for d, code, direc in calls:
            series = sorted((px.get(code) or {}).items())
            days = [x[0] for x in series]
            # 觀點日後第一個交易日當進場,持有 hz 交易日
            entry_i = next((i for i, dd in enumerate(days) if dd > d), None)
            if entry_i is None or entry_i + hz >= len(days):
                continue
            e_px = series[entry_i][1]
            x_px = series[entry_i + hz][1]
            raw = (x_px / e_px - 1) * 100
            adj = raw if direc == "bullish" else -raw
            raw_rets.append(raw)
            dir_rets.append(adj)
            wins += 1 if adj > 0 else 0
            n += 1
        if n:
            print(f"\n[持有 {hz} 交易日] 配對 n={n}")
            print(f"  方向調整後平均報酬 {sum(dir_rets)/n:+.2f}%  |  勝率(對) {wins/n*100:.0f}%")
        else:
            print(f"\n[持有 {hz} 交易日] 無有效配對")


if __name__ == "__main__":
    main()
