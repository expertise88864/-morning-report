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


# ===== KPI strip + summary bar (頂部美觀區) =====

def test_extract_stance_with_score():
    text = "## 十二、我的明確立場\n淨分 +3\n**立場：中性偏多**（解釋…）"
    s = mr._extract_stance(text)
    assert s["score"] == 3
    assert s["label"] == "中性偏多"


def test_extract_stance_bearish():
    s = mr._extract_stance("淨分 -5\n立場：偏空 / 防守為主")
    assert s["score"] == -5 and s["label"] == "偏空"


def test_extract_stance_missing():
    s = mr._extract_stance("沒有立場相關文字")
    assert s == {"label": None, "score": None}


def test_extract_summary_basic():
    text = ("## 十四、一句話總結\n\n"
            "SOX 暴跌 + Fed 升息預期雙殺成長股，減碼 00662 等止穩。\n\n## 其他")
    assert mr._extract_summary(text).startswith("SOX 暴跌")


def test_extract_summary_missing():
    assert mr._extract_summary("沒有總結章節的文字") == ""


def test_render_html_includes_kpi_strip_with_full_data():
    q = _full_quotes()
    q["TAIEX_PRED"] = {
        "pred_open": 40487, "last_close": 41172.36,
        "signals": [], "weighted_pct": -1.66, "ci_lower": 39120,
        "ci_upper": 41855, "consensus": "偏空", "signal_std": 3.32,
        "signal_count": 3,
    }
    q["MACRO"] = {"VIX": {"close": 18.43, "change_pct": 6.78}}
    fair = {"fair_price": 116.99, "last_00662_price": 118.8,
            "qqq_pct": -1.51, "implied_change_pct": -1.52,
            "method": "簡化版", "samples": 0}
    preds = {"last_2330": 2265.0, "tsm_pct": -3.2,
             "model1_1to1": 2192.5, "model2_regression": 2187.38,
             "model3_adr_decay": 2229.2, "decay_factor": 0.494,
             "mid": 2192.5, "range": (2187.38, 2229.2)}
    analysis = "## 十二、我的明確立場\n淨分 -4\n**立場：偏空**\n\n## 十四、一句話總結\nSOX 暴跌減碼 00662。"
    html = mr.render_html(q, fair, preds, analysis, "2026-05-16", "每日報")
    # KPI 條：5 個欄位都顯示
    assert "偏空" in html and "-4" in html
    assert "2192.5" in html
    assert "116.99" in html
    assert "40,487" in html
    assert "18.43" in html
    # 結論橫條
    assert "今日結論" in html and "SOX 暴跌減碼" in html
    # KPI 在 alerts 之前
    assert html.find("立場") < html.find("一、美股收盤行情")


def test_render_html_kpi_strip_degrades_gracefully():
    """LLM 沒給立場 / Python 預測 error → KPI 條仍要渲染，欠缺欄位顯示 '—'。"""
    q = _full_quotes()
    html = mr.render_html(q, {"error": "x"}, {"error": "x"},
                          "沒有立場資訊", "2026-05-14", "每日報")
    assert "立場" in html and "—" in html
    # 不可崩
    assert html.startswith("<!DOCTYPE html>")
