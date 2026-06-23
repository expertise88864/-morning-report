# -*- coding: utf-8 -*-
"""個股內在價值估算 — 移植 virattt/ai-hedge-fund 的 Valuation agent 邏輯,改吃 FinMind 台股財報。

三法(FinMind 可算者;原專案另有 EV/EBITDA,需歷史 EV 中位數,先略):
  A. Owner Earnings(Buffett):OE = 稅後淨利 + 折舊攤銷 − 資本支出;5 年折現(r=12%、終值 g=min(g,3%))×(1−25% 安全邊際)
  B. 多階段 DCF:FCF = CFO − CapEx;Yr1-3 高成長、Yr4-7 過渡降到終值成長、永續終值;折現率 WACC(台股 8.5%)
  C. Residual Income(EBO):RI = 淨利 − 股東權益成本×淨值;PV + 終值,加回淨值 ×(1−20% 安全邊際)
聚合:各法內在價值 vs 市值的 gap 加權(OE .40 / DCF .40 / RIM .20)→ 訊號(gap>+15% 偏低估/<−15% 偏高估)+ 信心。

⚠ 成長率/折現率為粗略假設(成長取營收 TTM YoY,大型股上限 10%);教育/自用,非投資建議、非精算。
用法:python valuation.py 2330 2510   # 代號 收盤價
"""
from __future__ import annotations

import sys

import fz_score as fz   # 重用 finmind / _ttm / _bs_at / _to_float

HORIZON = 5
# 折現率在地化為台股(無風險 ~1.5% + ERP ~5.5%×β≈1 ≈ 7%):ai-hedge-fund 原用美股 ~10.5% CoE,
# 套台股會把成長股一律判高估;以下為較公允的台股口徑(仍偏保守,保留價值投資安全邊際)。
R_OE = 0.12          # Owner Earnings 要求報酬(價值投資仍要折價)
WACC = 0.085         # DCF 折現率(台股大型股約 8-9%)
COE = 0.085          # Residual Income 股東權益成本
MOS_OE, MOS_RIM = 0.25, 0.20
WEIGHTS = {"owner_earnings": 0.40, "dcf": 0.40, "residual_income": 0.20}


def _est_growth(fs: list) -> float:
    """成長率 = 營收 TTM YoY,夾在 [0, 10%](大型股保守上限);取不到用 3%。"""
    rev, rev_p = fz._ttm(fs, "Revenue"), fz._ttm(fs, "Revenue", 4)
    if rev and rev_p and rev_p > 0:
        return max(0.0, min(rev / rev_p - 1.0, 0.10))
    return 0.03


def owner_earnings_value(ni, da, capex, g) -> float | None:
    if ni is None:
        return None
    oe = ni + (da or 0.0) - (capex or 0.0)
    if oe <= 0:
        return None
    tg = min(g, 0.03)
    pv = sum(oe * (1 + g) ** y / (1 + R_OE) ** y for y in range(1, HORIZON + 1))
    term = (oe * (1 + g) ** HORIZON * (1 + tg)) / (R_OE - tg) / (1 + R_OE) ** HORIZON
    return (pv + term) * (1 - MOS_OE)


def dcf_value(fcf, g) -> float | None:
    if fcf is None or fcf <= 0:
        return None
    tg = min(0.03, g * 0.6)
    pv, f = 0.0, fcf
    for y in range(1, 4):                       # Stage1 高成長
        f = fcf * (1 + g) ** y
        pv += f / (1 + WACC) ** y
    for y in range(4, 8):                        # Stage2 過渡:g 線性降到 tg
        gg = g + (tg - g) * ((y - 3) / 4)
        f = f * (1 + gg)
        pv += f / (1 + WACC) ** y
    term = (f * (1 + tg)) / (WACC - tg) / (1 + WACC) ** 7   # Stage3 永續
    return pv + term


def residual_income_value(ni, bv) -> float | None:
    if ni is None or bv is None or bv <= 0:
        return None
    g = 0.03
    ri = ni - COE * bv
    pv = sum(ri * (1 + g) ** y / (1 + COE) ** y for y in range(1, HORIZON + 1))
    term = (ri * (1 + g) ** HORIZON * (1 + g)) / (COE - g) / (1 + COE) ** HORIZON
    return (bv + pv + term) * (1 - MOS_RIM)


def compute(code: str, price: float | None, token: str, stmts: tuple | None = None) -> dict:
    fs, bs, cf = stmts if stmts is not None else fz.fetch_statements(code, token)
    out: dict = {"code": code}

    ni = fz._ttm(fs, "IncomeAfterTaxes")
    cfo = fz._ttm(cf, "CashFlowsFromOperatingActivities")
    da = (fz._ttm(cf, "Depreciation") or 0.0) + (fz._ttm(cf, "AmortizationExpense") or 0.0)
    # 資本支出:FinMind PPE 以投資現金流出呈現(負值)→ -ppe 為正即 capex;
    # 若該期為正(處分/回收),max(0,..)→0,不誤當 capex 扣減(保守)。
    capex = max(0.0, -(fz._ttm(cf, "PropertyAndPlantAndEquipment") or 0.0))
    fcf = (cfo - capex) if cfo is not None else None
    # RIM 用「母公司業主權益」優先(排除非控制權益),取不到再退回總權益。
    bv = fz._bs_at(bs, "EquityAttributableToOwnersOfParent") or fz._bs_at(bs, "Equity")
    shares = fz._bs_at(bs, "CapitalStock")
    shares = (shares / 10.0) if shares else None        # 面額 10 → 股數
    mktcap = (price * shares) if (price and shares) else None
    g = _est_growth(fs)
    out["growth_assumed_pct"] = round(g * 100, 1)

    vals = {
        "owner_earnings": owner_earnings_value(ni, da, capex, g),
        "dcf": dcf_value(fcf, g),
        "residual_income": residual_income_value(ni, bv),
    }
    out["methods"] = {k: (round(v / 1e8, 0) if v else None) for k, v in vals.items()}  # 億元
    if not mktcap:
        out["note"] = "缺市值,只列各法內在價值(億元)"
        return out
    out["mktcap_e"] = round(mktcap / 1e8, 0)

    # 加權 gap = Σ(weight × (內在價值−市值)/市值);只納可算的法、權重再正規化
    tw, wgap = 0.0, 0.0
    per = {}
    for k, v in vals.items():
        if v and v > 0:
            gap = (v - mktcap) / mktcap
            per[k] = round(gap * 100, 1)
            wgap += WEIGHTS[k] * gap
            tw += WEIGHTS[k]
    if tw == 0:
        out["note"] = "三法皆不可算(虧損/缺現金流)"
        return out
    wgap /= tw
    out["per_method_gap_pct"] = per
    out["weighted_gap_pct"] = round(wgap * 100, 1)
    out["signal"] = ("偏低估" if wgap > 0.15 else ("偏高估" if wgap < -0.15 else "合理"))
    out["confidence"] = round(min(abs(wgap) / 0.30 * 100, 100))
    return out


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore
        except Exception:
            pass
    if len(sys.argv) < 2:
        print("用法: python valuation.py <股號> [收盤價]")
        return 1
    code = sys.argv[1]
    price = float(sys.argv[2]) if len(sys.argv) > 2 else None
    import os
    import pathlib
    token = os.getenv("FINMIND_TOKEN", "")
    if not token:
        p = pathlib.Path("gooaye_study/_secrets.txt")
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("FINMIND_TOKEN"):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
    r = compute(code, price, token)
    print(f"=== {code}(price={price})內在價值估算 ===")
    print(f"  成長假設 {r.get('growth_assumed_pct')}% ・ 市值 {r.get('mktcap_e')} 億")
    print(f"  各法內在價值(億): {r.get('methods')}")
    print(f"  各法 gap%: {r.get('per_method_gap_pct')}")
    print(f"  加權 gap {r.get('weighted_gap_pct')}% → {r.get('signal')}(信心 {r.get('confidence')}%)")
    if r.get("note"):
        print(f"  註:{r['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
