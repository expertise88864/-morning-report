"""overfit_check:Deflated Sharpe + PBO(CSCV)單元測試。"""
import numpy as np

import overfit_check as oc


def test_norm_cdf_known_values():
    assert abs(oc._norm_cdf(0.0) - 0.5) < 1e-9
    assert abs(oc._norm_cdf(1.959964) - 0.975) < 1e-4
    assert abs(oc._norm_cdf(-1.959964) - 0.025) < 1e-4


def test_norm_ppf_known_values():
    assert abs(oc._norm_ppf(0.5)) < 1e-6
    assert abs(oc._norm_ppf(0.975) - 1.959964) < 1e-4
    assert abs(oc._norm_ppf(0.025) + 1.959964) < 1e-4
    # ppf 與 cdf 互為反函數
    for p in (0.1, 0.3, 0.8, 0.99):
        assert abs(oc._norm_cdf(oc._norm_ppf(p)) - p) < 1e-6


def test_sharpe_ratio_basic():
    assert oc.sharpe_ratio([1.0, 1.0, 1.0]) == 0.0          # 零波動
    assert oc.sharpe_ratio([0.01] * 1) == 0.0               # 樣本不足
    r = [0.02, -0.01, 0.03, 0.00, 0.015]
    sd = np.std(r, ddof=1)
    assert abs(oc.sharpe_ratio(r) - np.mean(r) / sd) < 1e-9


def test_deflated_sharpe_more_trials_lowers_dsr():
    """試越多組(n_trials↑)→ 期望最大 Sharpe(null)↑ → DSR↓(同一條報酬)。"""
    rng = np.random.RandomState(0)
    rets = rng.normal(0.05, 0.1, 120)              # 明顯正 Sharpe
    sharpes = list(rng.normal(0, 0.3, 50))         # 模擬 50 組 trial 的 Sharpe 分布
    few = oc.deflated_sharpe_ratio(rets, n_trials=2, all_trial_sharpes=sharpes)
    many = oc.deflated_sharpe_ratio(rets, n_trials=50, all_trial_sharpes=sharpes)
    assert few and many
    assert few["sr"] == many["sr"]                 # 同一條報酬 SR 不變
    assert many["sr0"] > few["sr0"]                # 試越多,null 期望最大 SR 越高
    assert many["dsr"] <= few["dsr"]               # → DSR 被 haircut 得更低


def test_deflated_sharpe_strong_single_trial_significant():
    rng = np.random.RandomState(1)
    rets = rng.normal(0.08, 0.05, 150)             # 很強的 Sharpe
    out = oc.deflated_sharpe_ratio(rets, n_trials=1)   # 單一檢定、無 haircut
    assert out["sr0"] == 0.0 and out["significant"] is True


def test_pbo_low_when_one_strategy_persistently_best():
    """有一個策略每期都明顯最好 → IS 最佳在 OS 也最佳 → PBO 接近 0。"""
    rng = np.random.RandomState(2)
    T, N = 160, 6
    M = rng.normal(0.0, 0.02, (T, N))
    M[:, 0] += 0.03                                # 策略 0 持續高報酬
    out = oc.pbo_cscv(M, n_splits=8)
    assert out and out["pbo"] < 0.15
    assert out["n_strategies"] == N


def test_pbo_is_valid_probability_on_noise():
    """純雜訊:PBO 必為合法機率 ∈[0,1](單一 seed 不強求 0.5,避免 flaky)。"""
    rng = np.random.RandomState(3)
    M = rng.normal(0.0, 0.02, (160, 8))
    out = oc.pbo_cscv(M, n_splits=8)
    assert out and 0.0 <= out["pbo"] <= 1.0
    assert out["n_combos"] == 70                  # C(8,4)


def test_pbo_higher_for_insample_only_edge_than_persistent_edge():
    """關鍵不變量:樣本內才有的假 edge(暴賺集中在各自時段)PBO 應遠高於『真持續最佳』。"""
    T, N = 160, 8
    block = T // N
    rng = np.random.RandomState(4)
    # 假 edge:策略 n 只在自己的時段暴賺 → IS 抓到、OS 落空
    overfit = rng.normal(0, 0.01, (T, N))
    for n in range(N):
        overfit[n * block:(n + 1) * block, n] += 0.25
    # 真 edge:策略 0 全程持續較佳
    persistent = rng.normal(0, 0.02, (T, N))
    persistent[:, 0] += 0.03
    pbo_overfit = oc.pbo_cscv(overfit, n_splits=8)["pbo"]
    pbo_persistent = oc.pbo_cscv(persistent, n_splits=8)["pbo"]
    assert pbo_overfit > pbo_persistent
    assert pbo_overfit > 0.4


def test_pbo_rejects_bad_shape():
    assert oc.pbo_cscv(np.zeros((100, 1))) == {}            # 只有 1 策略
    assert oc.pbo_cscv(np.zeros((4, 4)), n_splits=16) == {}  # T 太短
