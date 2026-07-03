# -*- coding: utf-8 -*-
"""
Backtest family: 2330 ADR premium mean-reversion (m2330-premium).

Unified protocol (must match all other agents):
- Data: backtest_data/panel.csv (predictors = most recent US close strictly before TW day D).
- Drop stale rows (us_gap_days > 4) and rows without computable y_2330.
- Eval window: LAST 500 aligned samples (report actual N).
- Rolling refit at eval day t uses ONLY samples in [t-250, t)  (strictly < t).
- Metrics: MAE(%), RMSE(%), direction hit rate (samples with |y| < 0.05% excluded).
- Paired t-test on (|err_baseline| - |err_candidate|), two-sided.
- Pass threshold: (MAE improvement >= 5% AND p < 0.05 AND direction hit not worse)
                  OR (direction hit + >= 3pp AND p < 0.05).
- Baseline (2330): median(model1, model3); model1 = tsm_pct (1:1),
  model3 = tsm_pct * decay, decay = no-intercept OLS slope of y_2330 ~ tsm_pct
  over [t-60, t); if < 30 samples -> decay = 0.75.

Premium proxy (no FX data available -> proxy, see caveats):
  p_t = cum_log_return(TSM ADR, each unique US session counted once, up to the
        US close available at TPE morning of day t)
      - cum_log_return(2330 close-to-close, dividend-adjusted, up to close(t-1))
  z_t = rolling 60-day z-score of p_t (window ends at t; only past info).

Strategies:
  P1  = baseline + k*z,  k from rolling-250 no-intercept OLS of (y - baseline) ~ z
  P1i = baseline + a + k*z (with intercept; exploratory variant)
  P2  = model1   + k*z,  k from rolling-250 no-intercept OLS of (y - model1) ~ z
  P2i = model1   + a + k*z (with intercept; exploratory variant)
"""
import json
import numpy as np
import pandas as pd
from scipy import stats

PANEL = r"C:\Users\User\Desktop\程式\-morning-report-main\backtest_data\panel.csv"

EVAL_N = 500
REFIT_WIN = 250
DECAY_WIN = 60
DECAY_MIN = 30
DECAY_FALLBACK = 0.75
Z_WIN = 60
FLAT_EPS = 0.05  # |y| < 0.05% excluded from direction hit

# ---------------------------------------------------------------- load
df = pd.read_csv(PANEL)
df = df[df["us_gap_days"] <= 4].copy()
df = df.dropna(subset=["y_2330", "tsm_pct"]).reset_index(drop=True)
n_all = len(df)

y = df["y_2330"].to_numpy(float)
tsm = df["tsm_pct"].to_numpy(float)
prev_close = df["tw2330_prev_close"].to_numpy(float)
div = df["tw2330_div"].to_numpy(float)

# ------------------------------------------------- unique US session flag
# Same US close repeated across consecutive TW days (US holiday / weekend
# pattern) -> count its return only once in the cumulative ADR index.
us_cols = df[["sox_pct", "tsm_pct", "ewt_pct", "qqq_pct"]].to_numpy(float)
new_us = np.ones(n_all, dtype=bool)
for t in range(1, n_all):
    new_us[t] = not np.allclose(us_cols[t], us_cols[t - 1], rtol=0, atol=1e-12)

# cumulative log return of TSM ADR known at TPE morning of day t (in %-log units)
cum_tsm = np.zeros(n_all)
acc = 0.0
for t in range(n_all):
    if new_us[t]:
        acc += np.log1p(tsm[t] / 100.0) * 100.0
    cum_tsm[t] = acc

# -------------------------------------- 2330 close-to-close return (div-adj)
# rcc[t] = return of TW day t: (close(t) + div(t)) / close(t-1) - 1
# close(t) = tw2330_prev_close[t+1]; div(t) ex-div at open of day t.
rcc = np.full(n_all, np.nan)
for t in range(n_all - 1):
    rcc[t] = ((prev_close[t + 1] + div[t]) / prev_close[t] - 1.0) * 100.0

# cumulative 2330 log return known at morning of day t = through close(t-1)
cum_2330_known = np.zeros(n_all)
acc = 0.0
for t in range(n_all):
    cum_2330_known[t] = acc            # sum over s <= t-1
    if not np.isnan(rcc[t]):
        acc += np.log1p(rcc[t] / 100.0) * 100.0

# ------------------------------------------------------- premium and z-score
p = cum_tsm - cum_2330_known           # ADR-implied premium proxy (log-% units)
z = np.full(n_all, np.nan)
for t in range(Z_WIN - 1, n_all):
    w = p[t - Z_WIN + 1: t + 1]
    sd = w.std(ddof=1)
    z[t] = (p[t] - w.mean()) / sd if sd > 1e-8 else 0.0

# ------------------------------------------------------------------ baseline
def decay_at(t):
    lo = max(0, t - DECAY_WIN)
    xs, ys = tsm[lo:t], y[lo:t]
    m = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[m], ys[m]
    if len(xs) < DECAY_MIN or np.sum(xs * xs) <= 0:
        return DECAY_FALLBACK
    return float(np.sum(xs * ys) / np.sum(xs * xs))

model1 = tsm.copy()
model3 = np.array([tsm[t] * decay_at(t) for t in range(n_all)])
baseline = np.median(np.vstack([model1, model3]), axis=0)

# ------------------------------------------------------- rolling k for P1/P2
def fit_k(t, resid, with_intercept):
    """OLS of resid ~ z over [t-REFIT_WIN, t); returns (a, k)."""
    lo = max(0, t - REFIT_WIN)
    zz, rr = z[lo:t], resid[lo:t]
    m = np.isfinite(zz) & np.isfinite(rr)
    zz, rr = zz[m], rr[m]
    if len(zz) < 60:
        return 0.0, 0.0
    if with_intercept:
        X = np.column_stack([np.ones(len(zz)), zz])
        coef, *_ = np.linalg.lstsq(X, rr, rcond=None)
        return float(coef[0]), float(coef[1])
    den = np.sum(zz * zz)
    return 0.0, float(np.sum(zz * rr) / den) if den > 0 else 0.0

resid_base = y - baseline
resid_m1 = y - model1

eval_start = n_all - EVAL_N if n_all >= EVAL_N else 0
idx_eval_candidates = list(range(eval_start, n_all))

pred = {"P1": np.full(n_all, np.nan), "P1i": np.full(n_all, np.nan),
        "P2": np.full(n_all, np.nan), "P2i": np.full(n_all, np.nan)}
k_log = {s: [] for s in pred}

for t in idx_eval_candidates:
    a, k = fit_k(t, resid_base, False)
    pred["P1"][t] = baseline[t] + k * z[t]
    k_log["P1"].append(k)
    a, k = fit_k(t, resid_base, True)
    pred["P1i"][t] = baseline[t] + a + k * z[t]
    k_log["P1i"].append(k)
    a, k = fit_k(t, resid_m1, False)
    pred["P2"][t] = model1[t] + k * z[t]
    k_log["P2"].append(k)
    a, k = fit_k(t, resid_m1, True)
    pred["P2i"][t] = model1[t] + a + k * z[t]
    k_log["P2i"].append(k)

# -------------------------------------------- identical eval set for everyone
valid = np.isfinite(y) & np.isfinite(baseline) & np.isfinite(z)
for s in pred:
    valid &= np.isfinite(pred[s])
eval_idx = np.array([t for t in idx_eval_candidates if valid[t]])
ye = y[eval_idx]

def metrics(pr):
    err = pr - ye
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    m = np.abs(ye) >= FLAT_EPS
    hit = float(np.mean(np.sign(pr[m]) == np.sign(ye[m]))) * 100.0
    return mae, rmse, hit, int(m.sum())

b_mae, b_rmse, b_hit, n_dir = metrics(baseline[eval_idx])

out = {
    "family": "2330 ADR premium mean reversion (m2330-premium)",
    "n_eval": int(len(eval_idx)),
    "n_direction_samples": n_dir,
    "eval_range": [df["date"].iloc[eval_idx[0]], df["date"].iloc[eval_idx[-1]]],
    "baseline": {"mae": round(b_mae, 4), "rmse": round(b_rmse, 4),
                 "direction_hit": round(b_hit, 2)},
    "strategies": [],
}

for s in ["P1", "P1i", "P2", "P2i"]:
    pr = pred[s][eval_idx]
    mae, rmse, hit, _ = metrics(pr)
    d = np.abs(baseline[eval_idx] - ye) - np.abs(pr - ye)
    tstat, pval = stats.ttest_1samp(d, 0.0)
    imp = (b_mae - mae) / b_mae * 100.0
    passed = bool((imp >= 5.0 and pval < 0.05 and hit >= b_hit) or
                  (hit - b_hit >= 3.0 and pval < 0.05))
    ks = np.array(k_log[s])
    out["strategies"].append({
        "name": s,
        "mae": round(mae, 4), "rmse": round(rmse, 4),
        "direction_hit": round(hit, 2),
        "mae_improvement_pct": round(imp, 2),
        "t_stat": round(float(tstat), 3), "p_value_vs_baseline": round(float(pval), 4),
        "passes_threshold": passed,
        "k_mean": round(float(ks.mean()), 4), "k_min": round(float(ks.min()), 4),
        "k_max": round(float(ks.max()), 4),
    })

# diagnostics: in-eval correlation between z and baseline residual
ze = z[eval_idx]
out["diag"] = {
    "corr_z_vs_baseline_resid": round(float(np.corrcoef(ze, resid_base[eval_idx])[0, 1]), 4),
    "corr_z_vs_y": round(float(np.corrcoef(ze, ye)[0, 1]), 4),
    "z_eval_mean": round(float(ze.mean()), 3), "z_eval_std": round(float(ze.std()), 3),
    "n_rows_after_filters": n_all,
    "n_repeated_us_sessions": int((~new_us).sum()),
}

print(json.dumps(out, indent=2, ensure_ascii=False))
