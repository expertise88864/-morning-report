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
