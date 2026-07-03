# -*- coding: utf-8 -*-
"""
TAIEX learned-weights backtest (family: T2/T3/T4/T5) vs production baseline.

Unified protocol:
- Data: backtest_data/panel.csv (predictors = most recent US close strictly before TW day D)
- Stale rule: drop samples with us_gap_days > 4 (panel already enforces; re-applied defensively)
- Target: y_taiex = TAIEX open(D)/close(D-1) - 1 (%)
- Eval window: LAST 500 aligned samples (report actual N)
- Rolling refit: model for eval day t fitted ONLY on rows [t-250, t)  (strictly < t)
- Metrics: MAE(%), RMSE(%), direction hit (excl. |y| < 0.05%)
- Test: paired t-test on (|err_baseline| - |err_candidate|)
- Baseline: 0.5714*(SOX*1.05) + 0.4286*TSM

Strategies (all no-intercept, matching baseline structure):
  T2: NNLS weights, factors {SOX, TSM}
  T3: NNLS weights, factors {SOX, TSM, EWT}
  T4: NNLS weights, factors {SOX, TSM, EWT, QQQ}
  T5: Ridge (alpha via 5-fold CV inside [t-250,t) only), factors {SOX, TSM, EWT, QQQ}
"""
import json
import numpy as np
import pandas as pd
from scipy.optimize import nnls
from scipy import stats

PANEL = r"C:\Users\User\Desktop\程式\-morning-report-main\backtest_data\panel.csv"
EVAL_N = 500
FIT_WIN = 250
DIR_EPS = 0.05  # |y| < 0.05% excluded from direction hit
RIDGE_ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]

rng_seedless_note = "deterministic: contiguous 5-fold CV (no shuffling)"

# ---------------- data ----------------
df = pd.read_csv(PANEL).sort_values("date").reset_index(drop=True)
df = df[df["us_gap_days"] <= 4].reset_index(drop=True)  # stale rule (defensive)
df = df.dropna(subset=["y_taiex", "sox_pct", "tsm_pct", "ewt_pct", "qqq_pct"]).reset_index(drop=True)

n = len(df)
eval_n = min(EVAL_N, n - FIT_WIN)  # need >= FIT_WIN history before first eval row
eval_idx = np.arange(n - eval_n, n)
assert eval_idx[0] >= FIT_WIN, "not enough history for rolling fit"

y = df["y_taiex"].to_numpy()
X_all = df[["sox_pct", "tsm_pct", "ewt_pct", "qqq_pct"]].to_numpy()

# ---------------- baseline ----------------
# 0.5714*(SOX*1.05) + 0.4286*TSM
base_pred = 0.5714 * (X_all[:, 0] * 1.05) + 0.4286 * X_all[:, 1]

# ---------------- fitters ----------------
def fit_nnls(Xw, yw):
    w, _ = nnls(Xw, yw)
    return w

def ridge_solve(Xw, yw, alpha):
    k = Xw.shape[1]
    return np.linalg.solve(Xw.T @ Xw + alpha * np.eye(k), Xw.T @ yw)

def fit_ridge_cv(Xw, yw, alphas=RIDGE_ALPHAS, folds=5):
    """5-fold CV on the past window only (contiguous folds, deterministic)."""
    m = len(yw)
    bounds = np.linspace(0, m, folds + 1).astype(int)
    best_alpha, best_err = alphas[0], np.inf
    for a in alphas:
        errs = []
        for f in range(folds):
            lo, hi = bounds[f], bounds[f + 1]
            mask = np.ones(m, dtype=bool)
            mask[lo:hi] = False
            if mask.sum() < Xw.shape[1] or (hi - lo) == 0:
                continue
            w = ridge_solve(Xw[mask], yw[mask], a)
            errs.append(np.mean(np.abs(Xw[lo:hi] @ w - yw[lo:hi])))
        e = float(np.mean(errs))
        if e < best_err:
            best_err, best_alpha = e, a
    return ridge_solve(Xw, yw, best_alpha), best_alpha

# ---------------- rolling backtest ----------------
strategies = {
    "T2_NNLS_SOX_TSM":          {"cols": [0, 1],       "kind": "nnls"},
    "T3_NNLS_SOX_TSM_EWT":      {"cols": [0, 1, 2],    "kind": "nnls"},
    "T4_NNLS_SOX_TSM_EWT_QQQ":  {"cols": [0, 1, 2, 3], "kind": "nnls"},
    "T5_Ridge_CV_SOX_TSM_EWT_QQQ": {"cols": [0, 1, 2, 3], "kind": "ridge"},
}
factor_names = ["SOX", "TSM", "EWT", "QQQ"]

preds = {name: np.full(n, np.nan) for name in strategies}
weights_log = {name: [] for name in strategies}
alpha_log = []

for t in eval_idx:
    lo = t - FIT_WIN
    Xw_full, yw = X_all[lo:t], y[lo:t]
    for name, spec in strategies.items():
        Xw = Xw_full[:, spec["cols"]]
        if spec["kind"] == "nnls":
            w = fit_nnls(Xw, yw)
        else:
            w, a = fit_ridge_cv(Xw, yw)
            alpha_log.append(a)
        preds[name][t] = X_all[t, spec["cols"]] @ w
        weights_log[name].append(w)

# ---------------- evaluation (identical sample set for all) ----------------
ye = y[eval_idx]
dir_mask = np.abs(ye) >= DIR_EPS

def metrics(pred):
    e = pred - ye
    mae = float(np.mean(np.abs(e)))
    rmse = float(np.sqrt(np.mean(e ** 2)))
    hit = float(np.mean(np.sign(pred[dir_mask]) == np.sign(ye[dir_mask])))
    return mae, rmse, hit, np.abs(e)

b_mae, b_rmse, b_hit, b_abs = metrics(base_pred[eval_idx])

results = {
    "n_eval": int(eval_n),
    "n_direction": int(dir_mask.sum()),
    "eval_range": [df["date"].iloc[eval_idx[0]], df["date"].iloc[eval_idx[-1]]],
    "baseline": {"name": "prod_0.6SOX+0.4286TSM", "mae": b_mae, "rmse": b_rmse, "direction_hit": b_hit},
    "strategies": [],
}

for name, spec in strategies.items():
    p = preds[name][eval_idx]
    mae, rmse, hit, c_abs = metrics(p)
    diff = b_abs - c_abs  # >0 means candidate better
    tstat, pval = stats.ttest_rel(b_abs, c_abs)
    imp = (b_mae - mae) / b_mae * 100.0
    wbar = np.mean(np.vstack(weights_log[name]), axis=0)
    params = ", ".join(f"{factor_names[c]}={wbar[i]:.3f}" for i, c in enumerate(spec["cols"]))
    if spec["kind"] == "ridge":
        vals, counts = np.unique(alpha_log, return_counts=True)
        params += " | alpha_mode=" + str(vals[np.argmax(counts)]) + \
                  " alphas_used=" + json.dumps({float(v): int(c) for v, c in zip(vals, counts)})
    passes = (imp >= 5.0 and pval < 0.05 and hit >= b_hit) or \
             ((hit - b_hit) >= 0.03 and pval < 0.05)
    results["strategies"].append({
        "name": name, "n_eval": int(eval_n),
        "mae": mae, "rmse": rmse, "direction_hit": hit,
        "t_stat_vs_baseline": float(tstat), "p_value_vs_baseline": float(pval),
        "mae_improvement_pct": imp, "passes_threshold": bool(passes),
        "params": params,
        "mean_diff_abs_err": float(np.mean(diff)),
    })

print(json.dumps(results, indent=2, ensure_ascii=False))
