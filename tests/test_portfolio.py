"""個人持股「昨日已實現損益」測試:設定解析 / 實際漲跌彙總 / 隱私 / 除息偵測 / 渲染。"""
import morning_report as mr


# ---------- _parse_portfolio ----------

def test_parse_portfolio_json():
    out = mr._parse_portfolio('{"2330": 5, "2454": 2}')
    assert out == {"2330": 5.0, "2454": 2.0}


def test_parse_portfolio_simple_comma():
    assert mr._parse_portfolio("2330:5,2454:2") == {"2330": 5.0, "2454": 2.0}


def test_parse_portfolio_simple_semicolon():
    assert mr._parse_portfolio("2330:5;2454:2") == {"2330": 5.0, "2454": 2.0}


def test_parse_portfolio_fractional_lots():
    # 零股以張為單位:0.5 張 = 500 股
    assert mr._parse_portfolio("2330:0.5") == {"2330": 0.5}


def test_parse_portfolio_empty_and_invalid():
    assert mr._parse_portfolio("") == {}
    assert mr._parse_portfolio(None) == {}
    assert mr._parse_portfolio("garbage no colon") == {}
    assert mr._parse_portfolio("{bad json") == {}


def test_parse_portfolio_filters_nonpositive():
    assert mr._parse_portfolio("2330:0,2317:-3,2454:1") == {"2454": 1.0}


# ---------- calc_portfolio_actual（昨日已實現漲跌 = 前天收盤 vs 昨天收盤）----------

def test_actual_empty_portfolio():
    assert mr.calc_portfolio_actual({}, {}) == {}


def test_actual_no_closes_returns_empty():
    # 有持股但 closes_map 沒資料 → 回 {}
    assert mr.calc_portfolio_actual({"2330": 1}, {}) == {}


def test_actual_realized_gain():
    portfolio = {"00662": 7.059, "00631L": 19}
    # closes_map: (前天收盤, 昨天收盤)
    closes = {"00662": (120.0, 121.2), "00631L": (210.0, 215.0)}
    out = mr.calc_portfolio_actual(portfolio, closes)
    # 00662: 7059股 ×(121.2−120.0)=7059×1.2 = 8,470.8
    # 00631L: 19000股 ×(215−210)=19000×5 = 95,000
    # 前天市值 = 7059×120 + 19000×210 = 847,080 + 3,990,000 = 4,837,080
    assert out["gain_amount"] == round(8470.8 + 95000.0, 0)   # 103471
    assert out["prev_value"] == 4837080
    # gain% = 103470.8 / 4837080 × 100 ≈ 2.14%
    assert out["gain_pct"] == round((103470.8 / 4837080) * 100, 2)
    assert out["n_holdings"] == 2 and out["n_priced"] == 2


def test_actual_negative_day():
    out = mr.calc_portfolio_actual({"0050": 10}, {"0050": (103.0, 102.0)})
    # 10000股 ×(102−103) = −10,000
    assert out["gain_amount"] == -10000
    assert out["gain_pct"] == round((-10000 / 1030000) * 100, 2)


def test_actual_skips_unpriced_holding():
    out = mr.calc_portfolio_actual(
        {"2330": 1, "9999": 1}, {"2330": (1000.0, 1010.0)})
    assert out["n_holdings"] == 2
    assert out["n_priced"] == 1
    assert out["gain_amount"] == 10000   # 只有 2330: 1000股×(1010−1000)


def test_actual_output_has_no_stock_codes():
    """隱私關鍵:回傳彙總 dict 不可含任何個股代號 / 張數。"""
    out = mr.calc_portfolio_actual(
        {"2330": 5, "2454": 3}, {"2330": (1000.0, 1010.0), "2454": (1300.0, 1290.0)})
    assert set(out.keys()) <= {"gain_pct", "gain_amount", "prev_value",
                                "last_value", "n_holdings", "n_priced"}
    assert "2454" not in str(out)


# ---------- detect_ex_dividend_today（公開卡 2330/0050/00662 用）----------

def test_detect_ex_dividend_today_hits(fake_yf):
    import datetime as dt
    import pandas as pd
    FT = fake_yf({})    # patch yf.Ticker → FakeTicker
    today = dt.date(2026, 6, 12)
    FT.div_map = {
        "2330.TW": pd.Series([4.0], index=[pd.Timestamp("2026-06-12")]),  # 今日除息
        "0050.TW": pd.Series([1.5], index=[pd.Timestamp("2026-03-20")]),  # 非今日
    }
    out = mr.detect_ex_dividend_today(["2330", "0050", "00662"], today)
    assert out == {"2330": 4.0}


def test_detect_ex_dividend_today_none(fake_yf):
    import datetime as dt
    FT = fake_yf({})
    FT.div_map = {}    # 無配息資料
    out = mr.detect_ex_dividend_today(["2330", "0050"], dt.date(2026, 6, 12))
    assert out == {}


# ---------- render 隱私 + KPI 持倉列 ----------

def _min_quotes(**over):
    base = {
        "QQQ": {"ticker": "QQQ", "close": 720, "prev_close": 718, "change_pct": 0.3,
                "high": 721, "low": 717, "volume": 1, "date": "2026-05-28"},
        "TSM": {"ticker": "TSM", "close": 420, "prev_close": 410, "change_pct": 2.4,
                "high": 422, "low": 415, "volume": 1, "date": "2026-05-28"},
        "SPY": {"ticker": "SPY", "close": 750, "prev_close": 749, "change_pct": 0.1,
                "high": 751, "low": 748, "volume": 1, "date": "2026-05-28"},
        "MACRO": {}, "USDTWD": 31.4, "USDTWD_prev": 31.4,
        "SEC_FILINGS": [], "TW_MOPS": [], "TAIFEX_OI": {}, "MARGIN": {},
        "WEEKLY": {}, "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
        "TAIEX_PRED": {}, "TW0050_PRED": {}, "BREADTH": {}, "MIDTERM": {},
        "BACKTEST": "", "ALERTS": [], "DATA_QUALITY": [], "TW_UNIVERSE_SNAPSHOT": [],
        "US_HOLIDAY": {},
    }
    base.update(over)
    return base


def test_render_shows_portfolio_row_and_hides_holdings():
    pf = {
        "p1": {"gain_pct": 1.23, "gain_amount": 35200, "prev_value": 2824800,
               "last_value": 2860000, "n_holdings": 3, "n_priced": 3},
        "p2": {"gain_pct": -0.45, "gain_amount": -8800, "prev_value": 1963800,
               "last_value": 1955000, "n_holdings": 2, "n_priced": 2},
        "p1_name": "主帳戶", "p2_name": "定存股",
    }
    quotes = _min_quotes(PORTFOLIO_ACTUAL=pf)
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, "分析",
                          "2026-05-28 (Thu)", "每日報")
    # 持倉列只顯示名稱 + 昨日損益%/金額，不揭露總市值。
    assert "主帳戶" in html
    assert "定存股" in html
    assert "總市值" not in html
    assert "286.0萬" not in html
    assert "195.5萬" not in html
    assert "+1.23%" in html
    assert "3.5萬" in html               # +NT$3.5萬


def test_render_no_portfolio_row_when_absent():
    quotes = _min_quotes()    # 無 PORTFOLIO_ACTUAL
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, "分析",
                          "2026-05-28 (Thu)", "每日報")
    assert "總市值" not in html    # 沒設定 → 不顯示持倉列
