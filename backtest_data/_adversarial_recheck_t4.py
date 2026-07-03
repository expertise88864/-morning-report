# -*- coding: utf-8 -*-
"""Independent adversarial re-implementation of T4_NNLS_SOX_TSM_EWT_QQQ vs baseline.

Written from scratch (not copied from bt_taiex-learned.py). Same unified protocol:
- predictors already aligned in panel.csv (most recent US close strictly before D)
- stale rule us_gap_days <= 4
- y_taiex = open(D)/close(D-1)-1 (%)
- rolling refit on [t-250, t), strictly before t
- eval window: last 500 (and last 250 for robustness)
- direction hit excludes |y| < 0.05
- paired t-test on |err_base| - |err_cand|
"""
import numpy as np
import pandas as pd
from scipy.optimize import nnls
from scipy import stats

PANEL = r"C:\Users\User\Desktop\程式\-morning-report-main\backtest_data\panel.csv"
FIT = 250

d = pd.read_csv(PANEL)
d = d[d["us_gap_days"] <= 4].copy()
d = d.dropna(subset=["y_taiex", "sox_pct", "tsm_pct", "ewt_pct", "qqq_pct"])
d = d.sort_values("date").reset_index(drop=True)
N = len(d)
print("rows after filters:", N, "| date range:", d["date"].iloc[0], "->", d["date"].iloc[-1])

# sanity: dates strictly increasing, no duplicates
assert d["date"].is_unique and (pd.to_datetime(d["date"]).diff().dropna() > pd.Timedelta(0)).all()

F = d[["sox_pct", "tsm_pct", "ewt_pct", "qqq_pct"]].values
yv = d["y_taiex"].values
baseline_all = 0.5714 * 1.05 * F[:, 0] + 0.4286 * F[:, 1]

def run_eval(eval_n):
    start = N - eval_n
    assert start >= FIT
    cand = np.empty(eval_n)
    scaled = np.empty(eval_n)   # diagnostic: k * baseline, k fit on same window (OLS through origin)
    wsum = []
    wlog = []
    for i, t in enumerate(range(start, N)):
        Xtr = F[t - FIT:t]
        ytr = yv[t - FIT:t]
        w, _res = nnls(Xtr, ytr)
        cand[i] = F[t] @ w
        wlog.append(w)
        wsum.append(w.sum())
        btr = baseline_all[t - FIT:t]
        k = float(btr @ ytr) / float(btr @ btr)
        scaled[i] = k * baseline_all[t]
    ye = yv[start:N]
    be = baseline_all[start:N]
    dmask = np.abs(ye) >= 0.05

    def mm(p):
        err = p - ye
        return (float(np.mean(np.abs(err))), float(np.sqrt(np.mean(err**2))),
                float(np.mean(np.sign(p[dmask]) == np.sign(ye[dmask]))), np.abs(err))

    bm, brm, bh, babs = mm(be)
    cm, crm, ch, cabs = mm(cand)
    sm, srm, sh, sabs = mm(scaled)
    tt, pp = stats.ttest_rel(babs, cabs)
    tt2, pp2 = stats.ttest_rel(sabs, cabs)  # candidate vs scaled-baseline diagnostic
    wbar = np.mean(np.vstack(wlog), axis=0)
    print(f"\n=== eval last {eval_n} ({d['date'].iloc[start]} -> {d['date'].iloc[N-1]}), n_dir={int(dmask.sum())} ===")
    print(f"baseline : MAE={bm:.6f} RMSE={brm:.6f} hit={bh:.4f}")
    print(f"T4 NNLS  : MAE={cm:.6f} RMSE={crm:.6f} hit={ch:.4f}  t={tt:.3f} p={pp:.3e}  imp={(bm-cm)/bm*100:.2f}%")
    print(f"  mean weights SOX={wbar[0]:.3f} TSM={wbar[1]:.3f} EWT={wbar[2]:.3f} QQQ={wbar[3]:.3f}  mean wsum={np.mean(wsum):.3f}")
    print(f"  pass(MAE branch): imp>=5%={((bm-cm)/bm*100)>=5} p<0.05={pp<0.05} hit_no_worse={ch>=bh}")
    print(f"diag k*baseline (rolling-rescaled prod): MAE={sm:.6f} hit={sh:.4f} imp_vs_base={(bm-sm)/bm*100:.2f}% | T4_vs_scaled t={tt2:.3f} p={pp2:.3e}")
    # direction-hit margin in raw counts
    bhits = int(np.sum(np.sign(be[dmask]) == np.sign(ye[dmask])))
    chits = int(np.sum(np.sign(cand[dmask]) == np.sign(ye[dmask])))
    print(f"  hit counts: baseline {bhits}/{int(dmask.sum())}, T4 {chits}/{int(dmask.sum())} (margin {chits-bhits})")

run_eval(min(500, N - FIT))
run_eval(250)

# full-sample no-intercept OLS for context (not part of protocol)
wols, *_ = np.linalg.lstsq(F, yv, rcond=None)
print("\nfull-sample OLS (no intercept) betas:", np.round(wols, 3), "sum=", round(float(wols.sum()), 3))
