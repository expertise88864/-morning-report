# -*- coding: utf-8 -*-
"""財報品質評分:Piotroski F-score(9 分)+ Altman Z-score(破產風險)。

資料源:FinMind 三表(免費 token 可抓)。損益/現金流為「單季值」→ TTM = 近 4 季加總;
F-score 比較「本 TTM vs 去年 TTM」(需近 8 季)。資產負債表為時點值,取最新與約 4 季前。

定義參考 zoharbabin/edgar_analytics(MIT)之演算法,改吃 FinMind 欄位。純函式、可單獨測;
任何欄位缺漏 → 該準則略過(F-score 標可得分母 denom),絕不丟例外。教育/自用,非投資建議。

用法(測試):python fz_score.py 2330 2510   # 代號 收盤價
"""
from __future__ import annotations

import sys
import requests

BASE = "https://api.finmindtrade.com/api/v4/data"


def _to_float(v):
    try:
        f = float(v)
        return f if f == f else None     # 排除 nan
    except (TypeError, ValueError):
        return None


def finmind(dataset: str, sid: str, start: str, token: str) -> list:
    p = {"dataset": dataset, "data_id": sid, "start_date": start}
    if token:
        p["token"] = token
    r = requests.get(BASE, params=p, timeout=(5, 8), headers={"User-Agent": "Mozilla/5.0"})
    return (r.json() or {}).get("data") or []


def _series(rows: list, typ: str) -> list[tuple[str, float]]:
    """某 type 的 (date,value) 由舊到新,去除空值。"""
    xs = [(str(r["date"]), _to_float(r.get("value")))
          for r in rows if r.get("type") == typ and _to_float(r.get("value")) is not None]
    return sorted(xs, key=lambda x: x[0])


def _ttm(rows: list, typ: str, end: int = 0) -> float | None:
    """單季值近 4 季加總(end=0 最近 TTM、end=4 去年同期 TTM)。不足 4 季回 None。"""
    s = _series(rows, typ)
    seg = s[-(4 + end):len(s) - end] if end else s[-4:]
    return sum(v for _, v in seg) if len(seg) == 4 else None


def _bs_at(rows: list, typ: str, back: int = 0) -> float | None:
    """資產負債表時點值:back=0 最新、back=4 約一年前(4 季)。"""
    s = _series(rows, typ)
    idx = -1 - back
    return s[idx][1] if len(s) > abs(idx) - 1 and len(s) >= abs(idx) else None


def compute(code: str, price: float | None, token: str) -> dict:
    fs = finmind("TaiwanStockFinancialStatements", code, "2023-07-01", token)
    bs = finmind("TaiwanStockBalanceSheet", code, "2023-07-01", token)
    cf = finmind("TaiwanStockCashFlowsStatement", code, "2023-07-01", token)
    out: dict = {"code": code}

    TA = _bs_at(bs, "TotalAssets"); TA_p = _bs_at(bs, "TotalAssets", 4)
    CA = _bs_at(bs, "CurrentAssets"); CL = _bs_at(bs, "CurrentLiabilities")
    CA_p = _bs_at(bs, "CurrentAssets", 4); CL_p = _bs_at(bs, "CurrentLiabilities", 4)
    NCL = _bs_at(bs, "NoncurrentLiabilities"); NCL_p = _bs_at(bs, "NoncurrentLiabilities", 4)
    RE = _bs_at(bs, "RetainedEarnings"); TL = _bs_at(bs, "Liabilities")
    shares_now = _bs_at(bs, "CapitalStock"); shares_p = _bs_at(bs, "CapitalStock", 4)

    ni = _ttm(fs, "IncomeAfterTaxes"); ni_p = _ttm(fs, "IncomeAfterTaxes", 4)
    rev = _ttm(fs, "Revenue"); rev_p = _ttm(fs, "Revenue", 4)
    gp = _ttm(fs, "GrossProfit"); gp_p = _ttm(fs, "GrossProfit", 4)
    ebit = _ttm(fs, "OperatingIncome")
    cfo = _ttm(cf, "CashFlowsFromOperatingActivities")

    # ---------- Piotroski F-score(9 準則)----------
    f = 0; denom = 0; comp = {}
    def crit(name, ok):
        nonlocal f, denom
        if ok is None:
            comp[name] = None; return
        denom += 1; f += 1 if ok else 0; comp[name] = bool(ok)
    roa = (ni / TA) if (ni is not None and TA) else None
    roa_p = (ni_p / TA_p) if (ni_p is not None and TA_p) else None
    crit("ROA>0", (roa > 0) if roa is not None else None)
    crit("CFO>0", (cfo > 0) if cfo is not None else None)
    crit("ROA↑", (roa > roa_p) if (roa is not None and roa_p is not None) else None)
    crit("CFO>NI(應計品質)", (cfo > ni) if (cfo is not None and ni is not None) else None)
    crit("槓桿↓", (NCL / TA < NCL_p / TA_p) if (NCL is not None and TA and NCL_p is not None and TA_p) else None)
    crit("流動比↑", (CA / CL > CA_p / CL_p) if (CA and CL and CA_p and CL_p) else None)
    crit("未增資", (shares_now <= shares_p * 1.001) if (shares_now and shares_p) else None)
    gm = (gp / rev) if (gp is not None and rev) else None
    gm_p = (gp_p / rev_p) if (gp_p is not None and rev_p) else None
    crit("毛利率↑", (gm > gm_p) if (gm is not None and gm_p is not None) else None)
    crit("資產周轉↑", (rev / TA > rev_p / TA_p) if (rev and TA and rev_p and TA_p) else None)
    if denom:
        out["fscore"] = f
        out["fscore_denom"] = denom        # 可得分母(<9 表部分準則資料缺)
        out["fscore_components"] = comp

    # ---------- Altman Z-score(製造業原版)----------
    mktcap = (price * (shares_now / 10.0)) if (price and shares_now) else None   # 面額 10 → 股數=股本/10
    if (TA and TL and ebit is not None and rev is not None and RE is not None
            and mktcap and CA is not None and CL is not None):   # 缺營運資金欄位就不產 Z(勿用 0 充當)
        A = (CA - CL) / TA
        B = RE / TA
        C = ebit / TA
        D = mktcap / TL
        E = rev / TA
        z = 1.2 * A + 1.4 * B + 3.3 * C + 0.6 * D + 1.0 * E
        out["zscore"] = round(z, 2)
        out["zscore_zone"] = ("安全" if z > 2.99 else ("灰色" if z >= 1.81 else "危險"))
    return out


def main() -> int:
    import os, pathlib
    code = sys.argv[1] if len(sys.argv) > 1 else "2330"
    price = float(sys.argv[2]) if len(sys.argv) > 2 else None
    token = os.getenv("FINMIND_TOKEN", "")
    if not token:
        p = pathlib.Path("gooaye_study/_secrets.txt")
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("FINMIND_TOKEN"):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
    r = compute(code, price, token)
    print(f"代號 {code}(price={price})")
    print(f"  F-score: {r.get('fscore')}/{r.get('fscore_denom')}")
    for k, v in (r.get("fscore_components") or {}).items():
        print(f"     {k}: {v}")
    print(f"  Z-score: {r.get('zscore')}  ({r.get('zscore_zone')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
