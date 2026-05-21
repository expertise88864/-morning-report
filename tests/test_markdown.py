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
    q["TW0050_PRED"] = {"last": 96.5, "pred_open": 95.4, "pred_pct": -1.14,
                        "method": "0.5 × 2330 + 0.5 × 加權指數"}
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
    # KPI 顯示 0050 取代 VIX
    assert "0050 預測" in html and "95.4" in html
    # VIX 仍在「總經指標」表，但不在 KPI 條
    kpi_section = html.split("一、美股收盤行情")[0]
    assert "VIX" not in kpi_section.split("MARKET ALERTS")[0] or "VIX 預測" not in html
    # 結論橫條
    assert "今日結論" in html and "SOX 暴跌減碼" in html
    # KPI 在 alerts 之前
    assert html.find("立場") < html.find("一、美股收盤行情")
    # 0050 卡片
    assert "六、0050 ETF 開盤預測" in html or "0050 今日合理價" in html


def test_render_html_shows_new_macro_indicators_and_breadth():
    q = _full_quotes()
    q["MACRO"] = {
        "VIX":   {"close": 17.5, "change_pct": -1.0, "pct_rank_252d": 50},
        "VIX9D": {"close": 18.0, "change_pct": 2.0,  "pct_rank_252d": 60},
        "SOX":   {"close": 5800, "change_pct": 1.2,  "pct_rank_252d": 80},
        "10Y":   {"close": 4.4,  "change_pct": -0.5},
        "DXY":   {"close": 98.0, "change_pct": 0.1},
        "13W":   {"close": 4.2,  "change_pct": 0.0},
        "N225":  {"close": 41000, "change_pct": 0.3},
        "SSE":   {"close": 3200, "change_pct": -0.4},
        "NQ":    {"close": 20100, "change_pct": 0.8,  "pct_rank_252d": 90},
        "ES":    {"close": 5800,  "change_pct": 0.5},
        "WTI":   {"close": 75.0,  "change_pct": 1.2},
        "GOLD":  {"close": 2400,  "change_pct": -0.3},
        "VIX_TERM": {"ratio": 1.029, "spread": 0.5, "state": "backwardation"},
    }
    q["BREADTH"] = {
        "total_value_raw": 3.5e11, "total_value_yi": 3500,
        "advance": 700, "decline": 200, "unchanged": 100, "total": 1000,
        "advance_ratio": 70.0, "breadth_state": "broad_rally",
    }
    html = mr.render_html(q, {"error": "x"}, {"error": "x"},
                          "x", "2026-05-21", "每日報")
    # 新總經列都在
    for label in ("VIX9D 短期恐慌", "NQ 期貨", "ES 期貨", "WTI 原油", "黃金 GC"):
        assert label in html, f"missing macro row: {label}"
    # 廣度卡片
    assert "大盤成交額與市場廣度" in html
    assert "3,500 億" in html or "3500 億" in html
    assert "70.0%" in html
    assert "普漲" in html


def test_render_html_kpi_strip_degrades_gracefully():
    """LLM 沒給立場 / Python 預測 error → KPI 條仍要渲染，欠缺欄位顯示 '—'。"""
    q = _full_quotes()
    html = mr.render_html(q, {"error": "x"}, {"error": "x"},
                          "沒有立場資訊", "2026-05-14", "每日報")
    assert "立場" in html and "—" in html
    # 不可崩
    assert html.startswith("<!DOCTYPE html>")
