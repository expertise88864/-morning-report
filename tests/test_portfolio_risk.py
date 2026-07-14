"""G1 持倉曝險引擎:純數學(合成序列精確可驗)+ 隱私(輸出/渲染不含持股明細)。

yfinance 本機 geo-block → 真數字要上 Actions;但 beta/情境/壓力的數學與隱私鐵律
這層完全離線可測到底。"""
import datetime as _dt

import morning_report as mr
import portfolio_risk as pr


# ── 合成序列工具 ─────────────────────────────────────────────────────────────
def _dates(n, start=(2026, 1, 5)):
    """n 個「工作日」日期字串(跳過週末,模擬交易日)。"""
    out = []
    d = _dt.date(*start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def _series_from_returns(dates, rets, base=100.0):
    """由日報酬序列(長度 = len(dates)-1)組 {date: close}。"""
    closes = [base]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    return {d: c for d, c in zip(dates, closes)}


# ── ols_beta ────────────────────────────────────────────────────────────────
def test_ols_beta_exact_on_synthetic():
    """資產報酬 = 1.5×驅動報酬 → OLS 斜率精確等於 1.5。"""
    driver = [0.01, -0.02, 0.015, -0.005, 0.008, -0.011, 0.02, -0.017,
              0.004, -0.009, 0.013, -0.006, 0.007, -0.014, 0.019, -0.003,
              0.011, -0.008, 0.005, -0.012, 0.016, -0.004]
    asset = [1.5 * x for x in driver]
    b = pr.ols_beta(asset, driver, min_n=20)
    assert b is not None and abs(b - 1.5) < 1e-9


def test_ols_beta_insufficient_samples_returns_none():
    assert pr.ols_beta([0.01, 0.02], [0.01, 0.02], min_n=20) is None


def test_ols_beta_zero_variance_driver_returns_none():
    asset = [0.01] * 25
    driver = [0.0] * 25          # 驅動無變異 → 無法回歸
    assert pr.ols_beta(asset, driver, min_n=20) is None


def test_ols_beta_clamped_to_bounds():
    driver = [0.01, -0.01] * 12
    asset = [10 * x for x in driver]   # 斜率 10,應被夾到 hi=4
    assert pr.ols_beta(asset, driver, min_n=20, hi=4.0) == 4.0


# ── aligned_returns ─────────────────────────────────────────────────────────
def test_aligned_returns_same_day():
    dates = _dates(6)
    a = _series_from_returns(dates, [0.01, 0.02, -0.01, 0.03, -0.02])
    d = _series_from_returns(dates, [0.005, 0.01, -0.005, 0.015, -0.01])
    xs, ys = pr.aligned_returns(a, d, lag_driver=False)
    assert len(xs) == len(ys) == 5
    assert abs(xs[0] - 0.01) < 1e-9 and abs(ys[0] - 0.005) < 1e-9


def test_aligned_returns_lag_pairs_prior_driver_day():
    """lag_driver=True:資產第 T 日報酬配「驅動在 T 之前最後一個交易日」的報酬。"""
    dates = _dates(5)
    a = _series_from_returns(dates, [0.01, 0.02, 0.03, 0.04])
    d = _series_from_returns(dates, [0.10, 0.20, 0.30, 0.40])
    xs, ys = pr.aligned_returns(a, d, lag_driver=True)
    # 資產最早有報酬的日是 dates[1];其之前的驅動報酬日不存在 → 從 dates[2] 起才配得上
    # dates[2] 的資產報酬(0.02)配 dates[1] 的驅動報酬(0.10)
    assert abs(xs[0] - 0.02) < 1e-9 and abs(ys[0] - 0.10) < 1e-9
    assert abs(xs[1] - 0.03) < 1e-9 and abs(ys[1] - 0.20) < 1e-9


def test_aligned_returns_empty_when_no_overlap():
    a = _series_from_returns(_dates(4, (2026, 1, 5)), [0.01, 0.02, 0.03])
    b = _series_from_returns(_dates(4, (2026, 6, 1)), [0.01, 0.02, 0.03])
    xs, ys = pr.aligned_returns(a, b, lag_driver=False)
    assert xs == [] and ys == []


# ── 權重 / 組合彙總 ──────────────────────────────────────────────────────────
def test_value_weights_normalizes_and_rejects_nonpositive():
    w = pr.value_weights({"a": 300, "b": 100})
    assert abs(w["a"] - 0.75) < 1e-9 and abs(w["b"] - 0.25) < 1e-9
    assert pr.value_weights({"a": 0, "b": -5}) == {}


def test_portfolio_beta_weighted_sum_and_coverage():
    weights = {"a": 0.5, "b": 0.5}
    pf, cov = pr.portfolio_beta(weights, {"a": 1.0, "b": 2.0})
    assert abs(pf - 1.5) < 1e-9 and abs(cov - 1.0) < 1e-9


def test_portfolio_beta_missing_beta_lowers_coverage():
    """某持股缺 beta(資料不足)→ 不計入,coverage < 1。"""
    weights = {"a": 0.6, "b": 0.4}
    pf, cov = pr.portfolio_beta(weights, {"a": 1.0, "b": None})
    assert abs(pf - 0.6) < 1e-9      # 只有 a 貢獻
    assert abs(cov - 0.6) < 1e-9


# ── 情境 / 壓力 ─────────────────────────────────────────────────────────────
def test_scenario_rows_math_and_skip_missing_driver():
    rows = pr.scenario_rows(
        {"qqq": 0.8, "tw": 1.2},
        [("qqq", -5.0, "那斯達克跌5%"), ("tw", -3.0, "台股跌3%"),
         ("fx", 1.0, "台幣貶1%")])   # fx 無 beta → 跳過
    assert len(rows) == 2
    assert rows[0]["delta_pct"] == -4.0    # 0.8 × -5
    assert rows[1]["delta_pct"] == -3.6    # 1.2 × -3


def test_stress_rows_negative_and_none_guard():
    assert pr.stress_rows(None, [10, 20]) == []
    rows = pr.stress_rows(0.7, [10, 20, 30])
    assert rows[0]["delta_pct"] == -7.0 and rows[2]["delta_pct"] == -21.0


def test_phrase_multiple():
    assert pr.phrase_multiple(1.23) == "約 1.2 倍"
    assert pr.phrase_multiple(None) == "—"


# ── fetch_portfolio_risk(mr 端整合;monkeypatch 掉 yfinance) ─────────────────
def _install_fake_history(monkeypatch, beta_tw=1.2):
    """假 _history_close_by_date:holding 報酬 = beta_tw × 台股大盤同日報酬。"""
    dates = _dates(90)
    import math
    tw_rets = [0.01 * math.sin(i / 3.0) for i in range(len(dates) - 1)]
    qqq_rets = [0.008 * math.cos(i / 4.0) for i in range(len(dates) - 1)]
    twii = _series_from_returns(dates, tw_rets)
    qqq = _series_from_returns(dates, qqq_rets)
    fx = _series_from_returns(dates, [0.0005 * ((-1) ** i) for i in range(len(dates) - 1)])
    holding = _series_from_returns(dates, [beta_tw * r for r in tw_rets])

    def fake(ticker, period="6mo"):
        if ticker == "^TWII":
            return dict(twii)
        if ticker == "QQQ":
            return dict(qqq)
        if ticker == "TWD=X":
            return dict(fx)
        if ticker.endswith(".TW"):
            return dict(holding)
        return {}
    monkeypatch.setattr(mr, "_history_close_by_date", fake)


def test_fetch_portfolio_risk_empty_portfolio():
    assert mr.fetch_portfolio_risk({}) == {}


def test_fetch_portfolio_risk_computes_tw_beta(monkeypatch):
    _install_fake_history(monkeypatch, beta_tw=1.2)
    out = mr.fetch_portfolio_risk({"2330": 1000, "00662": 500})
    assert out and out["tw_beta"] is not None
    assert abs(out["tw_beta"] - 1.2) < 0.05      # 合成資料應還原 ≈1.2 倍台股
    assert out["scenarios"] and out["stress"]


def test_fetch_portfolio_risk_output_has_no_holding_details(monkeypatch):
    """隱私鐵律:回傳的任何字串/鍵都不得含持股代號或股數。"""
    _install_fake_history(monkeypatch)
    out = mr.fetch_portfolio_risk({"2330": 1234, "00662": 567})
    blob = repr(out)
    for leak in ("2330", "00662", "1234", "567"):
        assert leak not in blob


def test_fetch_portfolio_risk_returns_empty_when_drivers_dead(monkeypatch):
    monkeypatch.setattr(mr, "_history_close_by_date", lambda *a, **k: {})
    assert mr.fetch_portfolio_risk({"2330": 1000}) == {}


def test_merge_share_dicts_sums_duplicate_codes():
    """兩帳戶同代號需相加(非 {**a,**b} 覆蓋),否則權重/曝險全錯(Codex review)。"""
    merged = mr._merge_share_dicts({"2330": 1000, "0050": 500},
                                   {"2330": 300, "00662": 200})
    assert merged["2330"] == 1300     # 相加,非被 300 覆蓋
    assert merged["0050"] == 500 and merged["00662"] == 200
    assert mr._merge_share_dicts({}, None, {"x": 1}) == {"x": 1}


def test_fetch_portfolio_risk_samples_from_qqq_when_twii_missing(monkeypatch):
    """台股大盤資料缺、僅那斯達克可算時,n_samples 仍應 > 0(不誤標近 0 日,Codex review)。"""
    _install_fake_history(monkeypatch)

    real = mr._history_close_by_date

    def fake(ticker, period="6mo"):
        if ticker == "^TWII":
            return {}                      # 台股大盤抓不到
        return real(ticker, period)
    monkeypatch.setattr(mr, "_history_close_by_date", fake)
    out = mr.fetch_portfolio_risk({"2330": 1000})
    assert out and out["qqq_beta"] is not None
    assert out["tw_beta"] is None          # 台股大盤資料缺 → 隱藏
    assert out["n_samples"] > 0            # 但樣本數來自那斯達克,不為 0


# ── 渲染 + 存檔去識別 ────────────────────────────────────────────────────────
def _sample_risk():
    return {
        "tw_beta": 1.4, "qqq_beta": 0.7, "fx_beta": 0.6,
        "tw_cov": 1.0, "qqq_cov": 1.0, "fx_cov": 1.0, "cov_shown": 1.0,
        "scenarios": [
            {"label": "美股科技(那斯達克)跌 5%", "move_pct": -5.0, "delta_pct": -3.5},
            {"label": "台股大盤跌 3%", "move_pct": -3.0, "delta_pct": -4.2},
        ],
        "stress": [{"drawdown_pct": 10, "delta_pct": -7.0},
                   {"drawdown_pct": 30, "delta_pct": -21.0}],
        "n_samples": 118,
    }


def test_render_portfolio_risk_plain_language_no_jargon():
    html = mr._render_portfolio_risk_html(_sample_risk())
    assert "你的持倉曝險" in html
    assert "約 1.4 倍" in html            # 白話倍數
    assert "壓力測試" in html
    # 白話鐵律:不出現艱澀術語
    for jargon in ("beta", "Beta", "β", "波動率", "追蹤誤差", "標準差", "共變異"):
        assert jargon not in html


def test_render_portfolio_risk_empty_is_blank():
    assert mr._render_portfolio_risk_html({}) == ""


def test_render_portfolio_risk_negative_beta_says_opposite_direction():
    """負係數(空頭/避險部位)→ 白話說「反向」而非「同向」;FX 驅動固定為貶值、
    方向由結果符號承載,不得雙重反轉(Codex review)。"""
    risk = {
        "tw_beta": -0.8, "qqq_beta": -0.5, "fx_beta": -0.6,
        "tw_cov": 1.0, "qqq_cov": 1.0, "fx_cov": 1.0, "cov_shown": 1.0,
        "scenarios": [], "stress": [], "n_samples": 100,
    }
    html = mr._render_portfolio_risk_html(risk)
    assert "反向" in html and "約 0.8 倍" in html   # 顯示絕對倍數 + 反向
    # FX:驅動固定「貶值」,呈現的資產變動帶負號(受損),不出現「升值」字眼翻轉
    assert "台幣每貶值 1%" in html
    assert "-0.6%" in html or "−0.6%" in html


def test_portfolio_risk_card_stripped_on_archive():
    """曝險卡以 PF_ROW 標記包裹 → 存檔去識別會整卡移除(repo public,個人財務不落地)。"""
    card = mr._render_portfolio_risk_html(_sample_risk())
    assert "<!--PF_ROW_START-->" in card and "<!--PF_ROW_END-->" in card
    page = f"<html><body>{card}<p>其他內容</p></body></html>"
    redacted = mr._redact_private_for_archive(page)
    assert "你的持倉曝險" not in redacted   # 整卡被移除
    assert "約 1.4 倍" not in redacted
    assert "其他內容" in redacted           # 卡外內容保留


def test_archive_strips_both_pf_rows():
    """KPI 持股列 + 曝險卡兩個 PF_ROW 區塊都要被去識別(全域 non-greedy)。"""
    page = ("A<!--PF_ROW_START-->持股列機密<!--PF_ROW_END-->B"
            "<!--PF_ROW_START-->曝險卡機密<!--PF_ROW_END-->C")
    out = mr._redact_private_for_archive(page)
    assert "機密" not in out
    assert "A" in out and "B" in out and "C" in out
