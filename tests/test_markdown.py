"""_md_to_html 轉譯與 render_html 結構測試。"""
import morning_report as mr


def test_md_escapes_html():
    out = mr._md_to_html("正常文字 <script>alert(1)</script> 結束")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_md_basic_formatting():
    out = mr._md_to_html("## 標題\n\n- 項目一\n- 項目二\n\n**粗體**內文")
    assert "<h2>" in out
    assert "<li>" in out
    assert "<strong>" in out


def _full_quotes():
    base = lambda t: {"ticker": t, "date": "2026-05-13", "close": 100.0,
                      "prev_close": 99.0, "change_pct": 1.01, "high": 101.0,
                      "low": 98.0, "volume": 1_000_000}
    return {
        "QQQ": base("QQQ"), "TSM": base("TSM"), "SPY": base("SPY"),
        "USDTWD": 31.0, "USDTWD_prev": 31.1, "MACRO": {},
        "SEC_FILINGS": [], "TAIFEX_OI": {}, "MARGIN": {}, "WEEKLY": {},
        "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
        "TAIEX_PRED": {}, "BACKTEST": "（無回溯資料）", "ALERTS": [],
        "DATA_QUALITY": [
            {"name": "美股行情 QQQ", "status": "ok", "detail": "收 100"},
            {"name": "夜盤台指期", "status": "error", "detail": "抓取失敗"},
        ],
    }


def test_render_html_contains_required_sections():
    html = mr.render_html(_full_quotes(), {"error": "x"}, {"error": "x"},
                          "## 測試分析", "2026-05-14 (Wed)", "每日報")
    assert html.startswith("<!DOCTYPE html>")
    for section in ("一、美股收盤行情", "三、00662", "四、2330", "資料品質"):
        assert section in html


def test_render_html_survives_full_quotes_dict():
    """回歸測試：quotes 內含 SEC_FILINGS / BACKTEST 等非行情值時不可崩潰。"""
    html = mr.render_html(_full_quotes(), {"error": "資料缺失"},
                          {"error": "資料缺失"}, "內容", "2026-05-14", "每日報")
    assert "資料缺失" in html


def test_render_html_shows_data_quality_error():
    html = mr.render_html(_full_quotes(), {"error": "x"}, {"error": "x"},
                          "x", "2026-05-14", "每日報")
    assert "夜盤台指期" in html and "失敗" in html
