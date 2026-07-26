"""model_confidence.py 的前視偏誤防護。

這支工具在開發時踩過一個**會讓整份結論反轉**的坑,故單獨立測試釘死:
history.json 的 `date` 與 `target_session_date` 有 50/51 是同一天(晨報在當日
開盤前發出,預測的就是當天開盤)。若隨機漫步基準取「`date` 那天的收盤」,等於用
今天的收盤預測今天的開盤 —— 前視偏誤,會讓基準假性地強到打敗全部模型
(實測基準平均誤差從 1.482% 假性降到 1.039%,SPA p 值從 0.121 假性升到 0.773,
結論由「模型贏基準」翻成「模型全輸」)。

這類 bug 不會讓任何測試變紅,只會讓你對系統下錯結論——正是最該被釘住的那種。
"""
import model_confidence as mc


def _mh(session_closes: dict[str, float], opens: dict[str, float] | None = None):
    """建 {session: record} 對照表,label_prices 帶 2330 的收盤/開盤。"""
    out = {}
    for s, c in session_closes.items():
        lp = {"close": c}
        if opens and s in opens:
            lp["open"] = opens[s]
        out[s] = {"session_date": s, "label_prices": {"2330": lp}}
    return out


def test_random_walk_baseline_uses_prior_session_not_target_day():
    """基準必須取目標日**之前**那個交易日的收盤,不能取目標日自己的收盤。"""
    closes = {"2026-07-21": 100.0, "2026-07-22": 200.0, "2026-07-23": 300.0}
    mh = _mh(closes)
    sessions = sorted(mh)

    assert mc._prev_close(mh, sessions, "2026-07-23") == 200.0, \
        "應取前一交易日(07-22)的收盤,取到 300 就是用了目標日自己的收盤(前視)"
    assert mc._prev_close(mh, sessions, "2026-07-22") == 100.0


def test_first_session_has_no_baseline():
    """序列中第一個交易日沒有前一日 → 必須回 None 而非悄悄取到自己。"""
    mh = _mh({"2026-07-21": 100.0, "2026-07-22": 200.0})
    sessions = sorted(mh)
    assert mc._prev_close(mh, sessions, "2026-07-21") is None


def test_unknown_session_returns_none():
    """目標日不在交易日序列中(例如尚未結算)→ 回 None,不得猜。"""
    mh = _mh({"2026-07-21": 100.0})
    assert mc._prev_close(mh, sorted(mh), "2026-07-25") is None


def test_actual_open_prefers_label_prices():
    """實際開盤以 label_prices 為權威來源(那是標籤價的正式出處)。"""
    mh = {"2026-07-22": {"session_date": "2026-07-22",
                         "label_prices": {"2330": {"open": 111.0, "close": 1.0}},
                         "stocks": {"2330": {"open": 999.0}}}}
    assert mc._actual_open(mh, "2026-07-22") == 111.0


def test_actual_open_falls_back_to_stocks():
    """label_prices 缺開盤時才退回 stocks。"""
    mh = {"2026-07-22": {"session_date": "2026-07-22",
                         "label_prices": {"2330": {"close": 1.0}},
                         "stocks": {"2330": {"open": 222.0}}}}
    assert mc._actual_open(mh, "2026-07-22") == 222.0


def test_t_sf_is_accurate_for_small_t_not_just_the_tails():
    """r2(七維度審查,P1):Lentz 連分數只在 x < (a+1)/(a+b+2) 收斂,而
    x = dof/(dof+t²) 在 **t→0 時 x→1**,恆在收斂域外,需要對稱式切換。
    先前 dof=49、t=0.001 回 p=0.0227(真值 0.4996)——把「毫無證據」報成
    p<0.05 顯著。失敗方向是**假陽性**,恰是這支工具最不該犯的方向。

    原測試只取 t=1.96/2.086/0.0 三點:前兩點在收斂域內,t=0 走 x>=1 短路分支
    碰巧正確——三個取樣點全部避開了失效帶。"""
    for dof, t, expect in [(49, 0.001, 0.4996), (49, 0.05, 0.4802),
                           (200, 0.05, 0.4801), (99, 0.01, 0.4960)]:
        got = mc._t_sf(t, dof)
        assert abs(got - expect) < 0.002, f"dof={dof} t={t}: {got} vs {expect}"


def test_t_sf_is_monotonic_and_never_reports_noise_as_significant():
    """單調性 + 不變式:t 很小時 p 必須接近 0.5,絕不能落進顯著區。"""
    prev = 1.0
    for t in [0.0, 0.001, 0.01, 0.1, 0.5, 1.0, 2.0, 3.0, 5.0]:
        p = mc._t_sf(t, 47)
        assert p <= prev + 1e-9, f"t={t} 破壞單調性"
        prev = p
        if t < 0.5:
            assert p > 0.30, f"t={t} 的 p={p} 落進顯著區——假陽性"


def test_mz_does_not_advise_subtracting_the_intercept_on_price_levels():
    """r2(七維度審查):原建議「應先扣掉截距」在真實資料上**是有害的**。

    本檢定跑在價格水準上,而水準迴歸中 a ≈ ȳ − b·x̄,截距與斜率幾乎完全負相關
    ——「a≠0」是「b≠1」的機械推論,不是獨立的加法偏誤。真實資料實測:
    真平均偏誤僅 +2.02 元,水準版卻報 a=422.12(t=3.67);照原建議扣截距
    MAE 25.66 → 424.14(**惡化 16 倍**),而 a+b*pred 校準 → 21.89。
    """
    import random
    rnd = random.Random(7)
    # 近隨機漫步的價格序列(本系統常態):預測 = 前日 + 過度反應的變動
    lvl, actual, pred = 2300.0, [], []
    for _ in range(60):
        step = rnd.gauss(0, 20)
        actual.append(lvl + step)
        pred.append(lvl + step * 1.4)     # 過度反應,但**無**加法偏誤
        lvl = actual[-1]
    r = mc.mincer_zarnowitz(actual, pred)
    assert r["b_t_vs_1"] < -2.0, "應偵測到過度反應"
    assert "扣掉截距" not in r["shrink_hint"], \
        f"仍在給有害建議:{r['shrink_hint']}"
    assert "a + b*pred" in r["shrink_hint"]


def test_mz_still_flags_genuine_additive_bias():
    """但斜率正常、只有加法偏誤時仍要報出來——修正不得把真訊號一起關掉。"""
    import random
    rnd = random.Random(11)
    actual = [100.0 + rnd.gauss(0, 5) for _ in range(200)]
    pred = [a - 8.0 + rnd.gauss(0, 0.5) for a in actual]   # b≈1、固定低估 8
    r = mc.mincer_zarnowitz(actual, pred)
    assert abs(r["b_t_vs_1"]) <= 2.5, f"斜率不該顯著偏離 1:{r['b_t_vs_1']}"
    assert "系統性偏誤" in r["shrink_hint"], f"漏報真實加法偏誤:{r}"
