# -*- coding: utf-8 -*-
"""Independent re-implementation of T5_Ridge_CV_SOX_TSM_EWT_QQQ for adversarial review.

Differences from original script (deliberate, to avoid copying bugs):
- ridge solved via augmented least-squares (np.linalg.lstsq) instead of normal equations
- loop/fold logic written from scratch from the protocol text
- t-test computed manually from the paired differences
"""
import numpy as np
import pandas as pd

PANEL = r"C:\Users\User\Desktop\程式\-morning-report-main\backtest_data\panel.csv"
ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]
FIT = 250

d = pd.read_csv(PANEL).sort_values("date").reset_index(drop=True)
d = d[d["us_gap_days"] <= 4].dropna(subset=["y_taiex", "sox_pct", "tsm_pct", "ewt_pct", "qqq_pct"]).reset_index(drop=True)
X = d[["sox_pct", "tsm_pct", "ewt_pct", "qqq_pct"]].values
y = d["y_taiex"].values
n = len(d)

def ridge_fit(A, b, alpha):
    k = A.shape[1]
    A_aug = np.vstack([A, np.sqrt(alpha) * np.eye(k)])
    b_aug = np.concatenate([b, np.zeros(k)])
    w, *_ = np.linalg.lstsq(A_aug, b_aug, rcond=None)
    return w

def ridge_cv_fit(A, b):
    m = len(b)
    edges = np.linspace(0, m, 6).astype(int)
    best = (None, np.inf)
    for a in ALPHAS:
        fold_maes = []
        for f in range(5):
            lo, hi = edges[f], edges[f + 1]
            tr = np.r_[np.arange(0, lo), np.arange(hi, m)]
            w = ridge_fit(A[tr], b[tr], a)
            fold_maes.append(np.mean(np.abs(A[lo:hi] @ w - b[lo:hi])))
        mu = np.mean(fold_maes)
        if mu < best[1]:
            best = (a, mu)
    return ridge_fit(A, b, best[0]), best[0]

def evaluate(eval_n):
    idx = np.arange(n - eval_n, n)
    assert idx[0] >= FIT
    pred_t5 = np.empty(eval_n)
    alphas_used = []
    for j, t in enumerate(idx):
        w, a = ridge_cv_fit(X[t - FIT:t], y[t - FIT:t])
        pred_t5[j] = X[t] @ w
        alphas_used.append(a)
    ye = y[idx]
    pred_base = 0.5714 * (X[idx, 0] * 1.05) + 0.4286 * X[idx, 1]

    # extra benchmarks
    pred_zero = np.zeros(eval_n)
    pred_scaled = np.empty(eval_n)  # k * baseline, k = no-intercept OLS on past 250
    base_all = 0.5714 * (X[:, 0] * 1.05) + 0.4286 * X[:, 1]
    for j, t in enumerate(idx):
        bw, yw = base_all[t - FIT:t], y[t - FIT:t]
        k = (bw @ yw) / (bw @ bw)
        pred_scaled[j] = k * base_all[t]

    dmask = np.abs(ye) >= 0.05
    out = {}
    for name, p in [("baseline", pred_base), ("T5", pred_t5), ("zero", pred_zero), ("scaled_base", pred_scaled)]:
        e = np.abs(p - ye)
        out[name] = dict(
            mae=float(e.mean()),
            rmse=float(np.sqrt(((p - ye) ** 2).mean())),
            hit=float((np.sign(p[dmask]) == np.sign(ye[dmask])).mean()),
            abs_err=e,
        )
    for name in ["T5", "zero", "scaled_base"]:
        diff = out["baseline"]["abs_err"] - out[name]["abs_err"]
        t_stat = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))
        from scipy import stats as st
        p_val = 2 * st.t.sf(abs(t_stat), len(diff) - 1)
        out[name]["t"] = float(t_stat)
        out[name]["p"] = float(p_val)
        out[name]["mae_imp_pct"] = (out["baseline"]["mae"] - out[name]["mae"]) / out["baseline"]["mae"] * 100

    print(f"=== eval_n={eval_n}  range {d['date'].iloc[idx[0]]} .. {d['date'].iloc[idx[-1]]}  n_dir={int(dmask.sum())} ===")
    va, ca = np.unique(alphas_used, return_counts=True)
    print("alpha dist:", dict(zip(va.tolist(), ca.tolist())))
    for name in ["baseline", "T5", "zero", "scaled_base"]:
        o = out[name]
        extra = f" t={o.get('t', float('nan')):.2f} p={o.get('p', float('nan')):.3g} imp={o.get('mae_imp_pct', float('nan')):.2f}%" if name != "baseline" else ""
        print(f"{name:12s} MAE={o['mae']:.4f} RMSE={o['rmse']:.4f} hit={o['hit']:.4f}{extra}")
    # T5 vs zero and T5 vs scaled baseline (does learning beat trivial fixes?)
    for ref in ["zero", "scaled_base"]:
        diff = out[ref]["abs_err"] - out["T5"]["abs_err"]
        t_stat = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))
        from scipy import stats as st
        p_val = 2 * st.t.sf(abs(t_stat), len(diff) - 1)
        imp = (out[ref]["mae"] - out["T5"]["mae"]) / out[ref]["mae"] * 100
        print(f"  T5 vs {ref:12s}: MAE imp={imp:.2f}%  t={t_stat:.2f}  p={p_val:.3g}")
    print()

evaluate(500)
evaluate(250)
