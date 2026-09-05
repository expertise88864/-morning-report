# -*- coding: utf-8 -*-
"""回測過度配適檢測:Deflated Sharpe Ratio(DSR)+ Probability of Backtest Overfitting(PBO)。

借鏡 esvhd/pypbo(AGPL — 僅參考演算法、未引用原碼)與 Bailey & López de Prado(2014,
"The Deflated Sharpe Ratio" / "The Probability of Backtest Overfitting")之公式,clean-room 自寫。
純自用、離線研究工具,**不進每日信**;零 scipy 依賴(常態 CDF/PPF 自寫,僅用既有 numpy/pandas)。

兩個核心:
  A. deflated_sharpe_ratio:多重檢定(試了 N 組)+ 非常態(偏態/峰態)haircut 後,
     觀測 Sharpe 真正 > 0 的機率。DSR>0.95 才算在多重測試後仍顯著。
  B. pbo_cscv:Combinatorially Symmetric Cross-Validation 估「樣本內最佳策略在樣本外
     掉到中位數以下」的機率。PBO 高(>0.5)= 該選法嚴重過度配適。

用途:對 factor_ic.py 的多因子(每個因子=一個策略,每日 IC=該期績效)做 haircut,
看「挑出來表現最好的因子」是不是多重測試挑出來的假象。

用法:python overfit_check.py
"""
from __future__ import annotations

import math
import sys
from itertools import combinations

import numpy as np

from model_history_store import load_model_history

EULER = 0.5772156649015329       # Euler–Mascheroni 常數
FACTORS = ["pct_5d", "ma20_dist_pct", "day_pct", "vol_ratio_20d",
           "daily_vol_pct", "market_cap", "slippage_bps"]
HORIZON = 5
MIN_NAMES = 15


def _norm_cdf(x: float) -> float:
    """標準常態 CDF(用 math.erf,零相依)。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """標準常態反函數(Acklam 有理逼近;|誤差| < 1.15e-9),零相依、不需 scipy。"""
    if not (0.0 < p < 1.0):
        return float("nan")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def sharpe_ratio(returns) -> float:
    """每期 Sharpe(未年化;mean/std,ddof=1)。std=0 回 0。"""
    r = np.asarray([x for x in returns if x == x], dtype=float)
    if r.size < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 1e-12 else 0.0


def deflated_sharpe_ratio(returns, n_trials: int,
                          all_trial_sharpes=None,
                          var_trials_sr: float | None = None) -> dict:
    """Deflated Sharpe Ratio。

    returns: 入選策略的每期報酬序列。n_trials: 一共試過幾組策略(多重測試數)。
    all_trial_sharpes: 各組 Sharpe(用來估 trial Sharpe 的變異);或直接給 var_trials_sr。
    回 {sr, sr0(期望最大 Sharpe under null), dsr, n_obs, skew, kurt, significant}。
    """
    r = np.asarray([x for x in returns if x == x], dtype=float)
    n = r.size
    if n < 8 or n_trials < 1:
        return {}
    sr = sharpe_ratio(r)
    mu, sd = r.mean(), r.std(ddof=1)
    if sd <= 1e-12:
        return {}
    z = (r - mu) / sd
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))                 # 非超額峰態(常態=3)
    if var_trials_sr is None:
        if all_trial_sharpes is not None and len(all_trial_sharpes) >= 2:
            var_trials_sr = float(np.var(np.asarray(all_trial_sharpes, float), ddof=1))
        else:
            var_trials_sr = 0.0
    # 期望最大 Sharpe under null(Bailey-LdP):用兩個極值近似
    if n_trials >= 2 and var_trials_sr > 0:
        sr0 = math.sqrt(var_trials_sr) * (
            (1 - EULER) * _norm_ppf(1 - 1.0 / n_trials)
            + EULER * _norm_ppf(1 - 1.0 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4.0 * sr * sr))
    dsr = _norm_cdf(((sr - sr0) * math.sqrt(n - 1)) / denom)
    return {"sr": round(sr, 4), "sr0": round(sr0, 4), "dsr": round(dsr, 4),
            "n_obs": n, "n_trials": n_trials, "skew": round(skew, 3),
            "kurt": round(kurt, 3), "significant": bool(dsr > 0.95)}


def pbo_cscv(perf, n_splits: int = 16) -> dict:
    """PBO via CSCV。perf: shape (T, N) — N 個策略各 T 期的績效(報酬)。

    把 T 切成 n_splits 個等長子塊,列舉所有「一半當 IS、一半當 OS」的組合;每組找 IS 最佳策略,
    看它在 OS 的相對排名(rank∈(0,1)),logit=ln(rank/(1-rank))。PBO = logit≤0 的比例
    (= IS 最佳在 OS 掉到中位數以下的機率)。PBO 高 → 過度配適。
    回 {pbo, n_combos, median_logit, n_strategies}。
    """
    M = np.asarray(perf, dtype=float)
    if M.ndim != 2 or M.shape[1] < 2:
        return {}
    T, N = M.shape
    s = n_splits - (n_splits % 2)            # 確保偶數
    if s < 4 or T < s * 2:
        return {}
    block = T // s                            # T 非 s 整數倍時,尾端 T%s 期不納入(可接受)
    blocks = [np.arange(i * block, (i + 1) * block) for i in range(s)]
    half = s // 2
    logits, below = [], 0
    for is_sel in combinations(range(s), half):
        is_idx = np.concatenate([blocks[i] for i in is_sel])
        os_idx = np.concatenate([blocks[i] for i in range(s) if i not in is_sel])
        is_perf = np.array([sharpe_ratio(M[is_idx, n]) for n in range(N)])
        os_perf = np.array([sharpe_ratio(M[os_idx, n]) for n in range(N)])
        n_star = int(np.argmax(is_perf))
        # OS 排名:argsort 由差到好 → rank=N 為 OS 最佳、rank=1 為最差(連續 Sharpe 幾無同值,
        # 同值時由 argsort 穩定序決定,對連續資料影響可忽略)。
        order = os_perf.argsort()
        ranks = np.empty(N, float)
        ranks[order] = np.arange(1, N + 1)
        rel = ranks[n_star] / (N + 1)        # ∈(0,1);IS 最佳在 OS 也好→rel 高→logit>0
        rel = min(max(rel, 1e-6), 1 - 1e-6)
        lg = math.log(rel / (1 - rel))
        logits.append(lg)
        if lg <= 0:
            below += 1
    if not logits:
        return {}
    return {"pbo": round(below / len(logits), 4),
            "n_combos": len(logits),
            "median_logit": round(float(np.median(logits)), 3),
            "n_strategies": N}


def _build_factor_return_matrix() -> tuple[np.ndarray, list[str]]:
    """從 model_history 建「每日 × 各因子」的 IC 矩陣(每個因子當一個策略,每日 IC 當該期績效)。"""
    snaps = [s for s in load_model_history(strict=True)
             if s.get("session_date") and isinstance(s.get("stocks"), dict)]
    snaps.sort(key=lambda s: s["session_date"])
    n = len(snaps)
    if n < HORIZON + 30:
        return np.empty((0, 0)), []
    closes = [{c: v.get("close") for c, v in s["stocks"].items() if v.get("close")} for s in snaps]

    def _ic(xs, ys):
        import pandas as pd
        if len(xs) < MIN_NAMES:
            return np.nan
        rx = pd.Series(xs).rank().to_numpy(copy=True)
        ry = pd.Series(ys).rank().to_numpy(copy=True)
        rx -= rx.mean()
        ry -= ry.mean()
        den = math.sqrt(float((rx * rx).sum()) * float((ry * ry).sum()))
        return float((rx * ry).sum() / den) if den else np.nan

    rows = []
    for i in range(n - HORIZON):
        fwd = closes[i + HORIZON]
        cur = snaps[i]["stocks"]
        day_ics = []
        ok = True
        for fac in FACTORS:
            xs, ys = [], []
            for code, sv in cur.items():
                fv = sv.get(fac)
                c0, c1 = sv.get("close"), fwd.get(code)
                if fv is None or not c0 or not c1:
                    continue
                xs.append(float(fv))
                ys.append(c1 / c0 - 1.0)
            ic = _ic(xs, ys)
            if np.isnan(ic):
                ok = False
                break
            day_ics.append(ic)
        if ok:
            rows.append(day_ics)
    return (np.asarray(rows, dtype=float) if rows else np.empty((0, 0))), FACTORS


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore
        except Exception:
            pass
    M, facs = _build_factor_return_matrix()
    if M.size == 0:
        print("資料不足(需 model_history 合併面板累積足夠交易日)")
        return 1
    T, N = M.shape
    print(f"因子報酬矩陣:{T} 期 × {N} 因子(每因子每日 IC 當該期績效)\n")
    sharpes = [sharpe_ratio(M[:, n]) for n in range(N)]
    best = int(np.argmax(sharpes))
    print("各因子 IC-Sharpe(未年化):")
    for n, fac in enumerate(facs):
        mark = "  ← 樣本內最佳" if n == best else ""
        print(f"  {fac:<16}{sharpes[n]:+.3f}{mark}")
    dsr = deflated_sharpe_ratio(M[:, best], n_trials=N, all_trial_sharpes=sharpes)
    pbo = pbo_cscv(M, n_splits=16)
    print(f"\nDeflated Sharpe(最佳因子 {facs[best]}，試了 {N} 組):")
    print(f"  SR={dsr.get('sr')}  期望最大SR(null)={dsr.get('sr0')}  "
          f"DSR={dsr.get('dsr')}  → {'多重測試後仍顯著' if dsr.get('significant') else '多重測試後不顯著(疑似挑出來的)'}")
    print(f"\nPBO(過度配適機率):{pbo.get('pbo')}  "
          f"(中位 logit {pbo.get('median_logit')}、{pbo.get('n_combos')} 組 CSCV)")
    print("  ※ PBO>0.5 = 嚴重過度配適;DSR>0.95 = 多重測試 + 非常態 haircut 後仍顯著。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
