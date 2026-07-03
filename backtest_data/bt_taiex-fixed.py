# -*- coding: utf-8 -*-
"""
Backtest: TAIEX fixed-weight reference family (T0/T1a/T1b/T1c/T1d).

Unified protocol:
- Data: backtest_data/panel.csv (already aligned: predictors = most recent US close
  strictly before TW day D; rows with us_gap_days > 4 were dropped at build time,
  but we re-enforce the filter defensively here).
- Target: y_taiex = TAIEX open(D)/close(D-1) - 1 (%).
- Eval window: LAST 500 aligned samples (report actual N).
- Rolling refit: not applicable -- all strategies in this family use fixed weights
  with zero fitted parameters, so there is no look-ahead by construction.
- Metrics: MAE(%), RMSE(%), direction hit rate (sign match, drop |y| < 0.05%).
- Test: paired t-test on (|err_baseline| - |err_candidate|), same samples.
- Pass threshold: (MAE improvement >= 5% AND p < 0.05 AND direction hit not worse)
  OR (direction hit +3pp or more AND p < 0.05).

Strategies:
  T0  = baseline = 0.5714*(SOX%*1.05) + 0.4286*TSM%
  T1a = SOX% * 1.05
  T1b = TSM%
  T1c = EWT%
  T1d = QQQ%
"""
import json
import os

import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, "panel.csv")

EVAL_N = 500
FLAT_EPS = 0.05  # |y| < 0.05% excluded from direction hit


def main():
    df = pd.read_csv(PANEL, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Defensive re-application of protocol filters (panel should already satisfy them).
    n0 = len(df)
    df = df[df["us_gap_days"] <= 4].copy()
    n_stale_dropped = n0 - len(df)

    # Valid sample = y_taiex and all predictors present.
    need = ["y_taiex", "sox_pct", "tsm_pct", "ewt_pct", "qqq_pct"]
    df = df.dropna(subset=need).reset_index(drop=True)

    # Eval window: last 500 aligned samples (identical for every strategy).
    ev = df.iloc[-EVAL_N:].copy() if len(df) >= EVAL_N else df.copy()
    n_eval = len(ev)

    y = ev["y_taiex"].to_numpy()
    sox = ev["sox_pct"].to_numpy()
    tsm = ev["tsm_pct"].to_numpy()
    ewt = ev["ewt_pct"].to_numpy()
    qqq = ev["qqq_pct"].to_numpy()

    preds = {
        "T0_baseline": 0.5714 * (sox * 1.05) + 0.4286 * tsm,
        "T1a_pure_SOX_x1.05": sox * 1.05,
        "T1b_pure_TSM": tsm,
        "T1c_pure_EWT": ewt,
        "T1d_pure_QQQ": qqq,
    }

    base_err = np.abs(preds["T0_baseline"] - y)
    base_mask_dir = np.abs(y) >= FLAT_EPS  # same flat-day mask for everyone

    def direction_hit(p):
        m = base_mask_dir
        return float(np.mean(np.sign(p[m]) == np.sign(y[m])))

    base_hit = direction_hit(preds["T0_baseline"])
    base_mae = float(np.mean(base_err))

    results = []
    for name, p in preds.items():
        err = np.abs(p - y)
        mae = float(np.mean(err))
        rmse = float(np.sqrt(np.mean((p - y) ** 2)))
        hit = direction_hit(p)
        if name == "T0_baseline":
            t_stat, p_val = 0.0, 1.0
            impr = 0.0
        else:
            # paired t-test on (|err_baseline| - |err_candidate|); positive mean => candidate better
            t_stat, p_val = stats.ttest_rel(base_err, err)
            t_stat, p_val = float(t_stat), float(p_val)
            impr = (base_mae - mae) / base_mae * 100.0
        hit_pp = (hit - base_hit) * 100.0
        passes = (
            (impr >= 5.0 and p_val < 0.05 and hit >= base_hit)
            or (hit_pp >= 3.0 and p_val < 0.05)
        )
        if name == "T0_baseline":
            passes = False  # baseline cannot beat itself
        results.append(
            {
                "name": name,
                "n_eval": n_eval,
                "n_dir": int(base_mask_dir.sum()),
                "mae": round(mae, 4),
                "rmse": round(rmse, 4),
                "direction_hit": round(hit, 4),
                "t_vs_baseline": round(t_stat, 3),
                "p_value_vs_baseline": round(p_val, 5),
                "mae_improvement_pct": round(impr, 2),
                "dir_hit_delta_pp": round(hit_pp, 2),
                "passes_threshold": bool(passes),
            }
        )

    out = {
        "family": "TAIEX fixed-weight reference (T0/T1a-T1d)",
        "panel_rows_used": len(df),
        "stale_redropped": n_stale_dropped,
        "eval_window": [str(ev["date"].iloc[0].date()), str(ev["date"].iloc[-1].date())],
        "n_eval": n_eval,
        "flat_excluded_from_dir": int(n_eval - base_mask_dir.sum()),
        "results": results,
    }
    print(json.dumps(out, indent=2))
    with open(os.path.join(HERE, "bt_taiex-fixed_results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
