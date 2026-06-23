"""alpha_factors:Alpha101 風格算子 + IC 驗效 單元測試。"""
import numpy as np
import pandas as pd

import alpha_factors as af


def _df(rows):
    return pd.DataFrame(rows, columns=["A", "B", "C", "D"])


def test_cs_rank_is_row_percentile():
    df = _df([[10, 20, 30, 40]])
    r = af.cs_rank(df)
    assert list(r.iloc[0]) == [0.25, 0.5, 0.75, 1.0]      # 橫斷面百分位


def test_ts_delta_and_delay():
    df = pd.DataFrame({"A": [1.0, 3.0, 6.0, 10.0]})
    assert list(af.ts_delta(df, 1)["A"].dropna()) == [2.0, 3.0, 4.0]
    assert af.ts_delay(df, 1)["A"].tolist()[1:] == [1.0, 3.0, 6.0]


def test_scale_normalizes_abs_sum():
    df = _df([[1.0, -3.0, 2.0, -4.0]])
    s = af.scale(df, a=1.0)
    assert abs(s.iloc[0].abs().sum() - 1.0) < 1e-9       # Σ|scaled| = 1


def test_decay_linear_weights_recent_more():
    df = pd.DataFrame({"A": [0.0, 0.0, 3.0]})            # 只有最近一天有值
    out = af.decay_linear(df, 3)
    # 視窗 [oldest..newest] 對權重 [1,2,3]/6 → 最近一天權重最大 3/6=0.5 → 3*0.5=1.5
    assert abs(out["A"].iloc[-1] - 1.5) < 1e-9


def test_alphas_run_on_small_panel():
    np.random.seed(0)
    days, n = 40, 6
    idx = pd.date_range("2026-01-01", periods=days, freq="B")
    cols = [f"{1000+i}" for i in range(n)]
    close = pd.DataFrame(100 * np.cumprod(1 + np.random.normal(0, 0.02, (days, n)), axis=0),
                         index=idx, columns=cols)
    P = {"close": close, "open": close.shift(1).fillna(close),
         "volume": pd.DataFrame(np.random.randint(1e3, 1e5, (days, n)).astype(float),
                                index=idx, columns=cols)}
    for name, fn in af.ALPHAS.items():
        sig = fn(P)
        assert sig.shape == close.shape, name


def test_rank_ic_detects_perfect_signal():
    """signal 與未來報酬完全同向 → 平均 IC 接近 +1。"""
    idx = pd.date_range("2026-01-01", periods=10, freq="B")
    cols = [str(c) for c in range(20)]
    rng = np.random.RandomState(1)
    fwd = pd.DataFrame(rng.normal(0, 1, (10, 20)), index=idx, columns=cols)
    sig = fwd.copy()                                     # 訊號 = 未來報酬本身
    r = af._rank_ic(sig, fwd)
    assert r and r["mean_ic"] > 0.99 and r["pos_pct"] == 100.0


def test_rank_ic_insufficient_returns_empty():
    idx = pd.date_range("2026-01-01", periods=3, freq="B")
    small = pd.DataFrame(np.ones((3, 5)), index=idx)     # 檔數 < MIN_NAMES
    assert af._rank_ic(small, small) == {}
