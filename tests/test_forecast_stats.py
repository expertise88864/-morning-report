"""批#48:巢狀比較、小樣本修正、區間鋒利度、預測效率。

這批是**正確性**修正而非新功能:DM/SPA/MCS 對巢狀模型在理論上不成立,而
「多模型合成 vs 隨機漫步(前日收盤)」正是典型的巢狀比較。用錯檢定會系統性
偏向「無法拒絕」——實測證實:同一份 48 天資料,SPA 給 p=0.121(無法宣稱贏),
Clark-West 給 p<0.0001(顯著)。
"""
import numpy as np
import pytest

import model_confidence as mc


def _rng():
    return np.random.default_rng(20260726)


def test_clark_west_detects_signal_that_dm_style_would_miss():
    """大模型真的含有訊息時,CW 應該顯著。

    刻意讓大模型只比小模型好一點點——那正是巢狀比較裡 DM 會被估計噪音蓋過的區間。
    """
    r = _rng()
    n = 200
    truth = r.normal(0, 1, n)
    small = np.zeros(n)                      # 小模型:永遠猜 0(隨機漫步的類比)
    big = 0.3 * truth + r.normal(0, 0.9, n)  # 大模型:含少量真訊號 + 估計噪音
    stat, p, mean = mc.clark_west(truth, big, small)
    assert stat is not None and p < 0.05, f"CW 沒偵測到真訊號:stat={stat}, p={p}"
    assert mean > 0


def test_clark_west_does_not_fire_on_pure_noise():
    """大模型只是噪音時不得顯著——否則這個檢定只是在製造假陽性。"""
    r = _rng()
    n = 200
    truth = r.normal(0, 1, n)
    small = np.zeros(n)
    big = r.normal(0, 1, n)                  # 與真值無關
    stat, p, _ = mc.clark_west(truth, big, small)
    assert p > 0.05, f"純噪音卻顯著:stat={stat}, p={p}"


def test_clark_west_requires_minimum_sample():
    assert mc.clark_west([1, 2], [1, 2], [1, 2]) == (None, None, None)


def test_hln_correction_shrinks_statistic():
    """HLN 修正必須讓統計量**變小**(降低假陽性),且樣本越小縮得越多。"""
    assert mc.hln_correction(3.0, 400) < 3.0
    assert mc.hln_correction(3.0, 30) < mc.hln_correction(3.0, 400)
    # 多步預測縮得更多
    assert mc.hln_correction(3.0, 100, horizon=5) < mc.hln_correction(3.0, 100, horizon=1)
    assert mc.hln_correction(3.0, 1) == 3.0      # 退化情形不得爆


def test_interval_score_punishes_width_even_when_covered():
    """**這是整批的關鍵不變式**:conformal 只保證覆蓋率,一個無窮寬的區間也能
    通過覆蓋率檢查。Interval score 必須讓「寬」本身就是懲罰。"""
    y = [100.0] * 50
    tight = mc.interval_score(y, [99.0] * 50, [101.0] * 50)
    wide = mc.interval_score(y, [50.0] * 50, [150.0] * 50)
    assert tight < wide, "無窮寬的區間沒有被懲罰"
    # 兩者覆蓋率都是 100%,單看覆蓋率完全分不出來
    assert all(99.0 <= v <= 101.0 for v in y)


def test_interval_score_punishes_misses():
    """漏失要比覆蓋貴,否則會鼓勵把區間縮到極窄。"""
    y = [100.0] * 50
    covered = mc.interval_score(y, [99.0] * 50, [101.0] * 50)
    missed = mc.interval_score(y, [101.0] * 50, [103.0] * 50)   # 全部漏在下緣
    assert missed > covered


def test_interval_score_degrades_on_bad_input():
    assert mc.interval_score([], [], []) is None
    assert mc.interval_score([1.0], [0.0], []) is None


def test_mincer_zarnowitz_detects_over_reaction():
    """b 顯著 < 1 = 預測過度反應。這是唯一能直接改善預測本身的診斷。"""
    r = _rng()
    n = 300
    truth = r.normal(100, 10, n)
    over = 100 + (truth - 100) * 2.0 + r.normal(0, 1, n)   # 反應過度兩倍
    out = mc.mincer_zarnowitz(truth, over)
    assert out and out["b"] < 1.0
    assert out["b_t_vs_1"] < -2.0
    assert "收縮" in out["shrink_hint"]


def test_mincer_zarnowitz_quiet_when_forecast_is_efficient():
    """預測本身有效率時不得亂給收縮建議。"""
    r = _rng()
    n = 300
    truth = r.normal(100, 10, n)
    good = truth + r.normal(0, 1, n)
    out = mc.mincer_zarnowitz(truth, good)
    assert out and abs(out["b"] - 1.0) < 0.1
    assert out["shrink_hint"] == ""


def test_mincer_zarnowitz_requires_sample():
    assert mc.mincer_zarnowitz([1, 2], [1, 2]) == {}


def test_pesaran_timmermann_corrects_for_base_rate():
    """**這正是 PT 存在的理由**:指數本來就偏漲時,每天猜「漲」會有很高的命中率,
    但那不是預測力。PT 必須把它判為不顯著。"""
    r = _rng()
    n = 200
    actual = np.where(r.random(n) < 0.7, 1.0, -1.0)   # 70% 天數上漲
    always_up = np.ones(n)                            # 永遠猜漲
    stat, p, hit, exp = mc.pesaran_timmermann(actual, always_up)
    assert hit > 0.6, "前提:天真策略的命中率確實很高"
    assert p is None or p > 0.05, f"天真策略被判為有預測力:hit={hit}, p={p}"


def test_pesaran_timmermann_detects_real_skill():
    r = _rng()
    n = 200
    actual = np.where(r.random(n) < 0.5, 1.0, -1.0)
    # 八成跟對方向
    pred = np.where(r.random(n) < 0.8, actual, -actual)
    stat, p, hit, exp = mc.pesaran_timmermann(actual, pred)
    assert p is not None and p < 0.01, f"真實方向能力沒被偵測到:hit={hit}, p={p}"


def test_pesaran_timmermann_requires_sample():
    assert mc.pesaran_timmermann([1, -1], [1, -1]) == (None, None, None, None)


def test_nw_se_is_positive_and_handles_short_series():
    assert mc._nw_se([1.0, 2.0, 3.0, 2.0, 1.0]) > 0
    assert mc._nw_se([1.0]) is None
    assert mc._nw_se([5.0] * 10) is None      # 零變異 → 無法給標準誤,不得回 0
    _ = pytest
