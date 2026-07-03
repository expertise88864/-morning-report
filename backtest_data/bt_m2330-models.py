# -*- coding: utf-8 -*-
"""
Backtest family: 2330 production-model variants vs protocol baseline.

Protocol (unified across agents):
- data: backtest_data/panel.csv (stale gap>4 already dropped at build; verified again here)
- target: y_2330 = 2330 open(D)(+div)/close(D-1) - 1 in %
- eval window: LAST 500 aligned samples (report actual N)
- rolling refit: model at eval day t fits ONLY on rows [t-250, t)  (strictly < t)
- metrics: MAE(%), RMSE(%), direction hit (sign match, drop |y|<0.05%)
- paired t-test on (|err_baseline| - |err_candidate|), two-sided
- pass: (MAE improve >=5% AND p<0.05 AND direction not worse) OR (direction +3pp AND p<0.05)

Strategies:
  M0 baseline = median(model1, model3); model1 = tsm_pct (1:1);
       model3 = tsm_pct * decay, decay = OLS slope through origin of
       y_2330 ~ tsm_pct on [t-60, t); if <30 samples decay = 0.75.
  M1 = pure model1
  M2 = pure model3
  M3 = median(model1, model2, model3, model4)
       model2 = rolling 250-day OLS WITH intercept: y = a + b*tsm_pct
       model4 = 5-day close momentum(%) * 0.25
  M4 = same 4 models, weighted by inverse MAE over [t-20, t) per model
       (simulates production MAE-inverse weighting; falls back to median
        if any model lacks window samples -- never triggers in eval range)
"""
import json
import math
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, "panel.csv")

EVAL_N = 500
REFIT_WIN = 250
DECAY_WIN = 60
DECAY_MIN = 30
DECAY_DEFAULT = 0.75
MAEW_WIN = 20
MOM_LAG = 5
MOM_DAMP = 0.25
FLAT_EPS = 0.05  # % ; drop from direction-hit only


def paired_t(d):
    """Two-sided paired t-test on array d (H0: mean=0). Returns (t, p)."""
    d = np.asarray(d, dtype=float)
    n = len(d)
    sd = d.std(ddof=1)
    if sd == 0 or n < 2:
        return 0.0, 1.0
    t = d.mean() / (sd / math.sqrt(n))
    try:
        from scipy import stats
        p = 2.0 * stats.t.sf(abs(t), n - 1)
    except Exception:
        # normal approximation (n=500 -> t-dist ~ normal)
        p = math.erfc(abs(t) / math.sqrt(2.0))
    return float(t), float(p)


def main():
    df = pd.read_csv(PANEL)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # protocol re-checks
    assert (df["us_gap_days"] <= 4).all(), "stale rows present"
    assert df["y_2330"].notna().all(), "missing y_2330"
    assert df["tsm_pct"].notna().all(), "missing tsm_pct"

    y = df["y_2330"].to_numpy(dtype=float)      # %
    x = df["tsm_pct"].to_numpy(dtype=float)     # %
    prevc = df["tw2330_prev_close"].to_numpy(dtype=float)
    n = len(df)

    # ---- per-row component model predictions (in % return space) ----
    p1 = np.full(n, np.nan)  # model1 1:1
    p2 = np.full(n, np.nan)  # model2 rolling-250 OLS w/ intercept
    p3 = np.full(n, np.nan)  # model3 ADR decay (OLS slope through origin, 60d)
    p4 = np.full(n, np.nan)  # model4 5d momentum * 0.25

    for t in range(n):
        p1[t] = x[t]

        # model3: decay from [t-60, t), slope through origin; <30 -> 0.75
        lo = max(0, t - DECAY_WIN)
        xs, ys = x[lo:t], y[lo:t]
        if len(xs) >= DECAY_MIN:
            sxx = float(np.dot(xs, xs))
            decay = float(np.dot(xs, ys)) / sxx if sxx > 0 else DECAY_DEFAULT
        else:
            decay = DECAY_DEFAULT
        p3[t] = x[t] * decay

        # model2: [t-250, t) OLS with intercept (needs full window)
        lo2 = t - REFIT_WIN
        if lo2 >= 0:
            xs2, ys2 = x[lo2:t], y[lo2:t]
            xm, ym = xs2.mean(), ys2.mean()
            sxx2 = float(np.dot(xs2 - xm, xs2 - xm))
            if sxx2 > 0:
                b = float(np.dot(xs2 - xm, ys2 - ym)) / sxx2
                a = ym - b * xm
                p2[t] = a + b * x[t]

        # model4: 5-day close momentum of 2330 (last close vs close 5 TW
        # trading days earlier), dampened 0.25  (production logic)
        if t - MOM_LAG >= 0 and prevc[t - MOM_LAG] > 0:
            mom = (prevc[t] / prevc[t - MOM_LAG] - 1.0) * 100.0
            p4[t] = mom * MOM_DAMP

    # ---- strategy predictions ----
    m0 = np.nanmedian(np.vstack([p1, p3]), axis=0)        # baseline (=mean of 2)
    m1 = p1.copy()
    m2 = p3.copy()
    four = np.vstack([p1, p2, p3, p4])                    # 4 x n
    m3 = np.where(np.isnan(four).any(axis=0), np.nan, np.nanmedian(four, axis=0))

    # M4: inverse-MAE weighting over [t-20, t) per model; fallback to median
    m4 = np.full(n, np.nan)
    abs_err4 = np.abs(four - y[None, :])                  # 4 x n
    for t in range(n):
        if np.isnan(four[:, t]).any():
            continue
        lo = t - MAEW_WIN
        if lo < 0 or np.isnan(abs_err4[:, lo:t]).any():
            m4[t] = m3[t]  # fallback: equal-weight median (production rule)
            continue
        maes = abs_err4[:, lo:t].mean(axis=1)
        if (maes <= 0).any():
            m4[t] = m3[t]
            continue
        w = 1.0 / maes
        m4[t] = float(np.dot(w, four[:, t]) / w.sum())

    strategies = {
        "M0_baseline_median(m1,m3)": m0,
        "M1_pure_1to1": m1,
        "M2_pure_decay60": m2,
        "M3_median_of_4": m3,
        "M4_invMAE20_weighted_4": m4,
    }

    # ---- identical eval sample set: last EVAL_N rows where ALL strategies valid ----
    valid = np.ones(n, dtype=bool)
    for arr in strategies.values():
        valid &= ~np.isnan(arr)
    idx_all = np.where(valid)[0]
    idx = idx_all[-EVAL_N:] if len(idx_all) >= EVAL_N else idx_all
    n_eval = len(idx)
    ye = y[idx]

    base_abs = np.abs(strategies["M0_baseline_median(m1,m3)"][idx] - ye)

    def metrics(pred):
        err = pred[idx] - ye
        mae = float(np.abs(err).mean())
        rmse = float(np.sqrt((err ** 2).mean()))
        keep = np.abs(ye) >= FLAT_EPS
        hit = float((np.sign(pred[idx][keep]) == np.sign(ye[keep])).mean()) * 100.0
        return mae, rmse, hit, np.abs(err), int(keep.sum())

    base_mae, base_rmse, base_hit, _, n_dir = metrics(
        strategies["M0_baseline_median(m1,m3)"])

    results = []
    for name, pred in strategies.items():
        mae, rmse, hit, abs_e, _ = metrics(pred)
        if name.startswith("M0"):
            tval, pval, imp = 0.0, 1.0, 0.0
        else:
            tval, pval = paired_t(base_abs - abs_e)
            imp = (base_mae - mae) / base_mae * 100.0
        passes = bool(
            (imp >= 5.0 and pval < 0.05 and hit >= base_hit - 1e-9)
            or (hit - base_hit >= 3.0 and pval < 0.05)
        )
        results.append({
            "name": name,
            "n_eval": n_eval,
            "n_direction": n_dir,
            "mae": round(mae, 4),
            "rmse": round(rmse, 4),
            "direction_hit": round(hit, 2),
            "t_value_vs_baseline": round(tval, 3),
            "p_value_vs_baseline": float(f"{pval:.3e}"),
            "mae_improvement_pct": round(imp, 3),
            "passes_threshold": passes,
        })

    out = {
        "family": "2330 production-model variants (M0..M4)",
        "eval_range": [str(df['date'].iloc[idx[0]].date()),
                       str(df['date'].iloc[idx[-1]].date())],
        "n_eval": n_eval,
        "n_direction_samples": n_dir,
        "results": results,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    with open(os.path.join(HERE, "bt_m2330-models_results.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
