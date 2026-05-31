"""個人持股預測測試:設定解析 / beta 漲幅彙總 / 隱私(個股不外洩) / 降級。"""
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


# ---------- calc_portfolio_forecast ----------

def test_forecast_empty_portfolio():
    assert mr.calc_portfolio_forecast({}, 1.0, {}, {}) == {}


def test_forecast_no_prices_returns_empty():
    # 有持股但 close_map 沒報價 → 無法估,回 {}
    assert mr.calc_portfolio_forecast({"2330": 1}, 1.0, {}, {}) == {}


def test_forecast_special_pred_and_beta_mix():
    portfolio = {"2330": 2, "2454": 1}
    special = {"2330": 2.0}            # 2330 專屬模型 +2%
    taiex_pct = 1.0                    # 加權 +1%
    close_map = {"2330": 1000.0, "2454": 1300.0}
    beta_map = {"2454": 1.5}           # 2454 beta 1.5 → +1.5%
    out = mr.calc_portfolio_forecast(portfolio, taiex_pct, special, close_map, beta_map)
    # 2330: 2張=2000股 ×1000 = 2,000,000 ×2% = 40,000
    # 2454: 1張=1000股 ×1300 = 1,300,000 ×1.5% = 19,500
    assert out["pred_amount"] == 59500
    assert out["last_value"] == 3300000
    assert out["pred_pct"] == 1.8       # 59500/3300000 = 1.803 → 1.8
    assert out["n_holdings"] == 2
    assert out["n_priced"] == 2


def test_forecast_default_beta_one_when_missing():
    # 沒 beta → 視為 1.0 → 漲幅 = 加權 %
    out = mr.calc_portfolio_forecast({"6505": 1}, 2.0, {}, {"6505": 500.0}, {})
    # 1張=1000股 ×500 = 500,000 ×2% = 10,000
    assert out["pred_amount"] == 10000
    assert out["pred_pct"] == 2.0


def test_forecast_skips_unpriced_holding():
    # 一檔有報價、一檔沒 → 只算有報價的
    out = mr.calc_portfolio_forecast(
        {"2330": 1, "9999": 1}, 1.0, {"2330": 1.0}, {"2330": 1000.0}, {})
    assert out["n_holdings"] == 2
    assert out["n_priced"] == 1
    assert out["pred_amount"] == 10000   # 只有 2330: 1000股×1000×1%


def test_forecast_output_has_no_stock_codes():
    """隱私關鍵:回傳彙總 dict 不可含任何個股代號 / 張數。"""
    out = mr.calc_portfolio_forecast(
        {"2330": 5, "2454": 3}, 1.0, {"2330": 1.0}, {"2330": 1000.0, "2454": 1300.0}, {})
    assert set(out.keys()) <= {"pred_pct", "pred_amount", "last_value",
                                "n_holdings", "n_priced"}
    # 代號字串不應出現在任何值裡
    assert "2454" not in str(out)


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
        "p1": {"pred_pct": 1.23, "pred_amount": 35200, "last_value": 2860000,
               "n_holdings": 3, "n_priced": 3},
        "p2": {"pred_pct": -0.45, "pred_amount": -8800, "last_value": 1955000,
               "n_holdings": 2, "n_priced": 2},
        "p1_name": "主帳戶", "p2_name": "定存股",
    }
    quotes = _min_quotes(PORTFOLIO_FORECAST=pf)
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, "分析",
                          "2026-05-28 (Thu)", "每日報")
    # 持倉列應出現:名稱 + 漲幅 + 金額(萬)
    assert "主帳戶" in html
    assert "定存股" in html
    assert "+1.23%" in html
    assert "3.5萬" in html      # 35200 → +NT$3.5萬
    # 隱私:forecast 不含個股代號,html 自然不會有(這裡只能確認彙總值有出現)
    assert "今日預估" in html


def test_render_no_portfolio_row_when_absent():
    quotes = _min_quotes()    # 無 PORTFOLIO_FORECAST
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, "分析",
                          "2026-05-28 (Thu)", "每日報")
    assert "今日預估" not in html    # 沒設定 → 不顯示持倉列
