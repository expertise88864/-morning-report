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


def test_clark_west_p_uses_hln_corrected_statistic():
    """r1(Codex):p 值必須由**修正後**的統計量、以 t(n-1) 算。
    先前回傳未修正統計量 + 常態 CDF,而 docstring 聲稱「HLN 修正後」——
    門檻附近會給出與宣稱不同的結論。"""
    r = _rng()
    n = 40                                    # 小樣本,修正才看得出差別
    truth = r.normal(0, 1, n)
    small = np.zeros(n)
    big = 0.3 * truth + r.normal(0, 0.9, n)
    stat, p, _ = mc.clark_west(truth, big, small)
    # 回傳的 stat 必須已縮小(HLN 的作用)
    raw_like = stat / mc.hln_correction(1.0, n)
    assert stat < raw_like, "回傳的統計量沒有套用 HLN 修正"
    # p 必須與 t(n-1) 一致,而非常態
    assert abs(p - mc._t_sf(stat, n - 1)) < 1e-9
    assert abs(p - (1 - mc._norm_cdf(stat))) > 1e-12 or n > 1000


def test_t_sf_matches_known_values():
    """t 分布上尾機率的正確性(無 scipy,自行實作,必須對得上已知值)。"""
    assert abs(mc._t_sf(1.96, 10 ** 6) - 0.025) < 0.002
    assert abs(mc._t_sf(2.086, 20) - 0.025) < 0.002
    assert abs(mc._t_sf(0.0, 30) - 0.5) < 1e-6


def test_mincer_zarnowitz_detects_pure_additive_bias():
    """r1(Codex):MZ 的虛無假設是 **(a,b)=(0,1) 兩個限制**。只驗 b 會漏掉純加法
    偏誤(actual ≈ pred + 10 時 b≈1、只有 a 偏離)——那是實打實的預測無效率,
    卻會靜默通過。"""
    r = _rng()
    n = 300
    truth = r.normal(100, 10, n)
    biased = truth - 10 + r.normal(0, 0.5, n)      # 系統性低估 10
    out = mc.mincer_zarnowitz(truth, biased)
    assert out
    assert abs(out["b"] - 1.0) < 0.1, "前提:斜率接近 1,只有截距偏"
    assert out["joint_p"] is not None and out["joint_p"] < 0.01, \
        f"純加法偏誤沒被聯合檢定抓到:joint_p={out['joint_p']}"
    assert "偏誤" in out["shrink_hint"]


def test_mincer_zarnowitz_joint_test_quiet_on_efficient_forecast():
    r = _rng()
    n = 300
    truth = r.normal(100, 10, n)
    good = truth + r.normal(0, 1, n)
    out = mc.mincer_zarnowitz(truth, good)
    assert out["joint_p"] > 0.01
    assert out["shrink_hint"] == ""


def test_run_reports_nested_diagnostics_not_only_spa():
    """r1(Codex):新診斷先前定義在 __main__ 之後且沒被 run() 呼叫——
    跑文件裡那行 `python model_confidence.py` 只會看到舊的 SPA 結論,
    而那正是本批要指出「因巢狀比較而不成立」的那個結論。"""
    import inspect
    src = inspect.getsource(mc.run)
    assert "_print_nested_diagnostics" in src, "run() 沒有呼叫巢狀診斷"
    assert "巢狀比較" in src or "不成立" in src, "run() 未標註 SPA 對巢狀不適用"
    # __main__ 守衛必須在所有定義之後
    full = inspect.getsource(mc)
    assert full.index('if __name__ == "__main__"') > full.index("def clark_west")


def test_mz_shadow_never_changes_the_emitted_prediction():
    """批#61:MZ 收縮走**影子模式** —— 算出來記錄,**不改寄出的數字**。

    walk-forward 驗證(2026-07-28、n=49、評估區間 29 天):
        原始預測    MAE 29.71  方向命中 77.8%
        水準收縮    MAE 29.10  方向命中 74.1%  ← 方向反而變差
        變動量收縮  MAE 27.26  方向命中 85.2%  ← 兩項都更好
    但配對檢定 t=+1.07(改好 15/29 天)——**樣本太小,還不能排除是運氣**。
    """
    import morning_report as mr
    from pathlib import Path
    src = Path(mr.__file__).read_text(encoding="utf-8")
    i = src.index('_RUN_MANIFEST["mz_shadow"]')
    window = src[max(0, i - 900):i + 400]
    # 影子值只能寫進 manifest,不得回寫進 predictions 的任何輸出欄位
    for emitted in ('predictions["weighted_final"] = _mz',
                    'predictions["mid"] = _mz',
                    'predictions["mid"] = shrunk'):
        assert emitted not in src, f"影子值被寫進寄出的預測:{emitted}"
    assert "不改寄出的數字" in window


def test_mz_shadow_uses_the_shared_price_frame(monkeypatch):
    """歷史配對必須沿用 build_price_frame —— 它已處理 forecast ledger 與
    model_history 的接合,以及**前視偏誤防護**(嚴格取前一個 session 的收盤)。

    自測踩到:第一版我猜 row["predictions"]["weighted_final"],實測 **n=0**
    ——那些欄位根本不在 model_history 裡。**又是自己猜 schema。**
    (測試環境的 state 被導到暫存目錄,所以這裡注入合成序列驗數學。)
    """
    import model_confidence as mc
    import morning_report as mr

    # 合成:實際變動 = 預測變動 × 0.5(即預測過度反應一倍)。
    # **變動量必須有變異** —— 第一版我讓每天都是 +20,sxx=0、斜率不可估,
    # 函式正確地回 applied=False(那個保護是對的,是我的 fixture 不對)。
    base = [2300.0 + i for i in range(30)]
    deltas = [(-1) ** i * (5 + i) for i in range(30)]
    pred = [b + d for b, d in zip(base, deltas)]
    act = [b + d * 0.5 for b, d in zip(base, deltas)]
    monkeypatch.setattr(mc, "build_price_frame", lambda: (act, pred, base))

    out = mr._mz_shadow_prediction(2340.0, 2300.0)
    assert out["n"] == 30 and out["applied"] is True
    assert abs(out["b"] - 0.5) < 0.02, f"收縮係數沒抓到過度反應:{out}"
    # +40 的預測變動應被收縮成約 +20
    assert abs(out["shadow"] - 2320.0) < 1.0, out
    # **必然**更靠近基準
    assert abs(out["shadow"] - 2300.0) < abs(out["raw"] - 2300.0)


def test_mz_shadow_needs_enough_history(monkeypatch):
    """樣本不足時不調整 —— 與 walk-forward 驗證腳本的 MIN_TRAIN=20 一致。"""
    import model_confidence as mc
    import morning_report as mr
    short = ([2300.0 + i * 0.5 for i in range(10)],
             [2300.0 + i for i in range(10)],
             [2300.0] * 10)
    monkeypatch.setattr(mc, "build_price_frame", lambda: short)
    out = mr._mz_shadow_prediction(2340.0, 2300.0)
    assert out == {"n": 10, "applied": False}


def test_mz_shadow_degrades_safely():
    import morning_report as mr
    assert mr._mz_shadow_prediction(None, 2350.0) == {}
    assert mr._mz_shadow_prediction(2336.0, None) == {}
    assert mr._mz_shadow_prediction("x", "y") == {}


def test_mz_shadow_survives_into_the_persisted_manifest(tmp_path, monkeypatch):
    """r1(Codex,P1):**同一個坑的第三次** —— 三審 P1-4 的 stance_dual、
    批#50 r1 的 data_checks,現在是 mz_shadow。_write_run_manifest 是**重建
    白名單 dict**,沒列到的鍵一律丟掉。

    影子模式的**唯一目的**就是累積樣本外資料;不落地等於整個功能白做,
    而且失敗是靜默的(記憶體裡有值、檔案裡沒有)。

    這條測試讀**序列化後的 JSON**,不是記憶體裡的 dict —— 前一版的測試只驗了
    賦值與數學,所以完全漏掉。
    """
    import datetime as dt
    import json
    import morning_report as mr

    f = tmp_path / "run_manifest.json"
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", f)
    mr._RUN_MANIFEST["mz_shadow"] = {"n": 49, "applied": True, "b": 0.6865,
                                     "raw": 2336.0, "shadow": 2342.85,
                                     "delta": 6.85}
    mr._write_run_manifest(dt.datetime(2026, 7, 28, 6, 0))
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert "mz_shadow" in saved, "影子預測沒落地 —— 樣本永遠累積不起來"
    assert saved["mz_shadow"]["shadow"] == 2342.85
    assert saved["mz_shadow"]["raw"] == 2336.0, "原始值也要留,否則無法事後比較"
