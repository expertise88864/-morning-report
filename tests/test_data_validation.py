"""require_quote / safe_float 與 build_data_quality 的邊界測試。"""
import morning_report as mr


def test_safe_float():
    assert mr.safe_float("1.5") == 1.5
    assert mr.safe_float(3) == 3.0
    assert mr.safe_float(None) is None
    assert mr.safe_float("abc") is None
    assert mr.safe_float("") is None


def test_require_quote_ok():
    quotes = {"QQQ": {"ticker": "QQQ", "close": 520.0, "prev_close": 515.0}}
    q = mr.require_quote(quotes, "QQQ")
    assert q is not None and q["close"] == 520.0


def test_require_quote_error_dict():
    quotes = {"QQQ": {"ticker": "QQQ", "error": "no valid data"}}
    assert mr.require_quote(quotes, "QQQ") is None


def test_require_quote_missing_fields():
    assert mr.require_quote({"QQQ": {"close": 1.0}}, "QQQ") is None      # 缺 prev_close
    assert mr.require_quote({"QQQ": {"prev_close": 1.0}}, "QQQ") is None  # 缺 close


def test_require_quote_absent_or_non_dict():
    assert mr.require_quote({}, "QQQ") is None
    assert mr.require_quote({"QQQ": "not a dict"}, "QQQ") is None


def test_parse_recipients():
    assert mr._parse_recipients("a@x.com,b@y.com") == ["a@x.com", "b@y.com"]
    assert mr._parse_recipients("a@x.com; b@y.com") == ["a@x.com", "b@y.com"]
    assert mr._parse_recipients("  solo@x.com  ") == ["solo@x.com"]
    assert mr._parse_recipients("") == []
    assert mr._parse_recipients(None) == []


def _empty_quotes(**overrides):
    """組裝一份能讓 _build_prompt 跑起來的最小 quotes（其餘欄位 overrides 補）。"""
    base = {
        "QQQ": {"ticker": "QQQ", "close": 520, "prev_close": 515, "change_pct": 0.97},
        "TSM": {"ticker": "TSM", "close": 220, "prev_close": 218, "change_pct": 0.92},
        "SPY": {"ticker": "SPY", "close": 580, "prev_close": 578, "change_pct": 0.35},
        "USDTWD": 31.0, "USDTWD_prev": 31.1, "MACRO": {},
        "SEC_FILINGS": [], "TAIFEX_OI": {}, "MARGIN": {}, "WEEKLY": {},
        "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
        "TAIEX_PRED": {}, "BACKTEST": "", "ALERTS": [], "DATA_QUALITY": [],
    }
    base.update(overrides)
    return base


def test_build_prompt_handles_none_in_history():
    """回歸測試：歷史欄位若為 None（前一日資料抓失敗會存 None），prompt 組裝不可崩。
    曾發生 :+ 格式 spec 對 None 拋 TypeError 導致整份晨報失敗。"""
    history_with_none = [{
        "date": "2026-05-15", "weekday": "Fri",
        "qqq_pct": 0.97, "tsm_pct": 4.48, "vix": 17.26,
        "taifex_foreign_oi": None,   # 抓失敗時會存 None
        "critical_news": ["川習會落幕"],
    }]
    quotes = _empty_quotes(HISTORY=history_with_none)
    p = mr._build_prompt(quotes, {"error": "x"}, {"error": "x"}, [], [], "")
    assert isinstance(p, str)
    assert "資料缺失" in p   # taifex 欄位該以「資料缺失」呈現
    assert "川習會落幕" in p  # critical news 仍保留


def test_call_llm_analysis_survives_prompt_build_failure(monkeypatch):
    """_build_prompt 若拋例外，call_llm_analysis 必須回 fallback 字串而不是 raise，
    確保 main() 仍能寄出基本版晨報。"""
    monkeypatch.setattr(mr, "_build_prompt",
                        lambda *a, **kw: (_ for _ in ()).throw(TypeError("simulated")))
    out = mr.call_llm_analysis({"QQQ": {}}, {}, {}, news=[{"source": "X", "title": "t"}])
    assert isinstance(out, str) and len(out) > 0


def test_analysis_complete_enough_detects_truncated_watchlist():
    complete = (
        "## 十二、今日台股關注五檔\n"
        + "\n".join(f"### {2300+i} 測試\n- x" for i in range(5))
        + "\n## 十三、一句話總結\n完成"
    )
    truncated = "## 十二、今日台股關注五檔\n2451 創見\n基本面：月"
    assert mr._analysis_complete_enough(complete) is True
    assert mr._analysis_complete_enough(truncated) is False


def test_call_llm_analysis_retries_once_when_truncated(monkeypatch):
    calls = {"n": 0}

    def fake_call(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return "## 十二、今日台股關注五檔\n2451 創見\n基本面：月"
        return (
            "## 十二、今日台股關注五檔\n"
            + "\n".join(f"{2300+i} 測試\n- x" for i in range(5))
            + "\n## 十三、一句話總結\n完成"
        )

    monkeypatch.setattr(mr, "_call_llm_text", fake_call)
    out = mr.call_llm_analysis(_empty_quotes(), {"error": "x"}, {"error": "x"}, [])
    assert calls["n"] == 2
    assert "一句話總結" in out


def test_detect_us_holiday_memorial_day():
    """週二早上跑時,QQQ.date 應為週一;若為週五則代表週一 US 休市(Memorial Day 之類)。"""
    import datetime as dt
    quotes = {"QQQ": {"date": "2026-05-22"}}    # Fri
    today = dt.date(2026, 5, 26)                # Tue
    out = mr.detect_us_holiday(quotes, today)
    assert out["detected"] is True
    assert out["gap_days"] == 3
    assert out["expected_date"] == "2026-05-25"


def test_detect_us_holiday_normal_tuesday():
    import datetime as dt
    quotes = {"QQQ": {"date": "2026-05-25"}}    # Mon
    today = dt.date(2026, 5, 26)                 # Tue
    out = mr.detect_us_holiday(quotes, today)
    assert out["detected"] is False


def test_detect_us_holiday_monday_normal():
    """週一早上跑時 (TPE), 期望 US 為上週五。資料若為上週五 → 正常,非休市。"""
    import datetime as dt
    quotes = {"QQQ": {"date": "2026-05-22"}}    # Fri
    today = dt.date(2026, 5, 25)                 # Mon TPE
    out = mr.detect_us_holiday(quotes, today)
    assert out["detected"] is False              # 週末跳到 Fri 為正常


def test_detect_us_holiday_no_qqq_date():
    import datetime as dt
    out = mr.detect_us_holiday({"QQQ": {}}, dt.date(2026, 5, 26))
    assert out["detected"] is False


def test_us_holiday_triggers_red_alert():
    """US_HOLIDAY 偵測到時,detect_market_alerts 應產生 red 警告。"""
    quotes = {"US_HOLIDAY": {"detected": True, "actual_date": "2026-05-22",
                             "actual_weekday": "週五", "expected_date": "2026-05-25", "gap_days": 3},
              "MACRO": {}}
    alerts = mr.detect_market_alerts(quotes, {}, {}, {})
    assert any(a.get("title") == "美股昨日休市（國定假日）" and a.get("level") == "red"
               for a in alerts)


def test_data_quality_flags_us_holiday():
    quotes = {
        "QQQ": {"ticker": "QQQ", "date": "2026-05-22", "close": 720.0, "prev_close": 718.0},
        "TSM": {"ticker": "TSM", "date": "2026-05-22", "close": 405.0, "prev_close": 408.0},
        "SPY": {"ticker": "SPY", "date": "2026-05-22", "close": 745.0, "prev_close": 742.0},
        "USDTWD": 31.5, "MACRO": {}, "TAIEX_PRED": {}, "NIGHT_TXF": {},
        "TAIFEX_OI": {}, "MARGIN": {}, "SEC_FILINGS": [],
        "TW_UNIVERSE_FALLBACK": False,
        "US_HOLIDAY": {"detected": True, "actual_date": "2026-05-22",
                       "actual_weekday": "週五", "expected_date": "2026-05-25", "gap_days": 3},
    }
    dq = mr.build_data_quality(quotes, {"error": "x"}, {"error": "x"},
                                news=[{"title": "x"}] * 12, tw0050=[])
    # 應有「美股交易日」項目標 fallback
    holiday_entry = next((d for d in dq if d["name"] == "美股交易日"), None)
    assert holiday_entry is not None
    assert holiday_entry["status"] == "fallback"
    # 美股行情各檔也應降為 fallback,且 detail 含「休市」字眼
    qqq_entry = next(d for d in dq if d["name"] == "美股行情 QQQ")
    assert qqq_entry["status"] == "fallback"
    assert "休市" in qqq_entry["detail"]


def test_build_data_quality_detects_zero_filled_institutional():
    """回歸：fetch_twse_institutional 失敗時 snapshot 仍會回 100 檔（全填 0）。
    dq 不能只看數量就說「正常」，必須抓出『法人欄全 0』的情況。"""
    # 100 檔，全部法人值是 0（模擬 三大法人端點失敗的結果）
    tw0050 = [{"code": str(2300 + i), "name": f"x{i}", "desc": "x",
               "close": 100.0, "day_pct": 0.0, "vol_ratio": 1.0, "month_pct": 0.0,
               "foreign_lot": 0.0, "invest_lot": 0.0, "dealer_lot": 0.0, "total_lot": 0.0,
               "foreign_30d_lot": 0.0, "invest_30d_lot": 0.0, "dealer_30d_lot": 0.0,
               "inst_30d_days": 0, "market_cap": 1e10}
              for i in range(100)]
    quotes = {"QQQ": {"ticker": "QQQ", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "TSM": {"ticker": "TSM", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "SPY": {"ticker": "SPY", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "USDTWD": 31.0, "MACRO": {}, "TAIEX_PRED": {}, "NIGHT_TXF": {},
              "TAIFEX_OI": {}, "MARGIN": {}, "SEC_FILINGS": [],
              "TW_UNIVERSE_FALLBACK": False}
    dq = mr.build_data_quality(quotes, {"error": "x"}, {"error": "x"},
                                news=[{"title": "x"}] * 12, tw0050=tw0050)
    inst_entry = next(d for d in dq if "universe 籌碼" in d["name"])
    assert inst_entry["status"] == "error"
    assert "三大法人" in inst_entry["detail"]


def test_build_data_quality_universe_ok_when_institutional_present():
    """正常情況：100 檔多數有非零法人 → dq 仍應為 ok。"""
    tw0050 = [{"code": str(2300 + i), "name": f"x{i}", "desc": "x",
               "close": 100.0, "day_pct": 0.0, "vol_ratio": 1.0, "month_pct": 0.0,
               "foreign_lot": 1000.0 if i < 90 else 0.0,    # 90/100 有法人資料
               "invest_lot": 0.0, "dealer_lot": 0.0, "total_lot": 1000.0,
               "foreign_30d_lot": 0.0, "invest_30d_lot": 0.0, "dealer_30d_lot": 0.0,
               "inst_30d_days": 0, "market_cap": 1e10}
              for i in range(100)]
    quotes = {"QQQ": {"ticker": "QQQ", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "TSM": {"ticker": "TSM", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "SPY": {"ticker": "SPY", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "USDTWD": 31.0, "MACRO": {}, "TAIEX_PRED": {}, "NIGHT_TXF": {},
              "TAIFEX_OI": {}, "MARGIN": {}, "SEC_FILINGS": [],
              "TW_UNIVERSE_FALLBACK": False}
    dq = mr.build_data_quality(quotes, {"error": "x"}, {"error": "x"},
                                news=[{"title": "x"}] * 12, tw0050=tw0050)
    inst_entry = next(d for d in dq if "universe 籌碼" in d["name"])
    assert inst_entry["status"] == "ok"


def test_build_data_quality_marks_error_and_ok():
    quotes = {
        "QQQ": {"ticker": "QQQ", "date": "2026-05-13", "close": 520, "prev_close": 515},
        "TSM": {"ticker": "TSM", "error": "no valid data"},
        "SPY": {"ticker": "SPY", "date": "2026-05-13", "close": 580, "prev_close": 578},
        "USDTWD": 31.0,
        "MACRO": {},
        "TAIEX_PRED": {}, "NIGHT_TXF": {}, "TAIFEX_OI": {}, "MARGIN": {},
        "SEC_FILINGS": [],
    }
    fair = {"error": "QQQ 行情抓取失敗"}
    preds = {"error": "TSM ADR 行情抓取失敗"}
    dq = mr.build_data_quality(quotes, fair, preds, news=[], tw0050=[])
    by_name = {d["name"]: d for d in dq}
    assert by_name["美股行情 QQQ"]["status"] == "ok"
    assert by_name["美股行情 TSM ADR"]["status"] == "error"
    assert by_name["00662 估值"]["status"] == "error"
    assert by_name["2330 三模型預測"]["status"] == "error"
    # 每筆都要有三個欄位
    for d in dq:
        assert {"name", "status", "detail"} <= set(d)
        assert d["status"] in ("ok", "fallback", "error")
