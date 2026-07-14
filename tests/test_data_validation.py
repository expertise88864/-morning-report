"""require_quote / safe_float 與 build_data_quality 的邊界測試。"""
import time

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


def test_build_prompt_does_not_ask_llm_to_write_watchlist_section():
    p = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, [], [], "")
    assert "## 十二、今日台股關注五檔" not in p
    assert "不要撰寫「今日台股關注五檔」段落" in p


def test_build_prompt_injects_python_2330_price_levels():
    """2330 關鍵價位必須由 Python 注入(新台幣中樞值),不可再叫 LLM 自己用 XXX 算
    → 根除把台積電 ADR 美元價(約 426 美元)誤當 2330 台股價(約 2300 元)的幻覺。"""
    preds = {"mid": 2313.24, "last_2330": 2295.0,
             "model1_1to1": 2310, "model2_regression": 2320}
    fair = {"fair_price": 120.16, "last_00662_price": 118.15}
    p = mr._build_prompt(_empty_quotes(), fair, preds, [], [], "")
    assert "2313" in p                 # Python 注入的新台幣中樞值
    assert "守穩 XXX" not in p          # 舊的「LLM 自己填」占位符已移除
    assert "新台幣計價" in p            # 明確標示幣別
    assert "R14" in p                  # 幣別/量級鐵律存在
    # 美股報價不再把含 history 的整個 dict 倒進 prompt
    assert "history" not in p.lower()


def test_build_prompt_2330_price_unavailable_is_explicit():
    """預測缺失時,prompt 要明寫「資料未提供」並禁止編造,而非留白讓 LLM 亂掰。"""
    p = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, [], [], "")
    assert "資料未提供" in p
    assert "守穩 XXX" not in p


def test_build_prompt_company_news_roundrobin_keeps_late_companies():
    """重點公司新聞展平採輪替:即使前面公司各塞 3 則,後面公司(含關注三檔)
    的頭條仍須在全域上限內露出,不被整家吃掉。"""
    news = []
    for i in range(15):                       # 15 家 × 3 則 = 45,超過 lines[:36] 上限
        label = f"C{i:02d}"
        for j in range(3):
            news.append({
                "company_label": label, "source": f"Google:{label}", "source_name": "鉅亨",
                "title": f"{label} 取得大訂單第{j}案", "summary": "客戶擴產投片",
            })
    p = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, news, [], "")
    # 第 13~15 家在舊的「每家連塞 3 則後 [:36] 截斷」會整家消失;輪替後其頭條仍在。
    assert "[C12]" in p and "[C13]" in p and "[C14]" in p


def test_call_llm_analysis_survives_prompt_build_failure(monkeypatch):
    """_build_prompt 若拋例外，call_llm_analysis 必須回 fallback 字串而不是 raise，
    確保 main() 仍能寄出基本版晨報。"""
    monkeypatch.setattr(mr, "_build_prompt",
                        lambda *a, **kw: (_ for _ in ()).throw(TypeError("simulated")))
    out = mr.call_llm_analysis({"QQQ": {}}, {}, {}, news=[{"source": "X", "title": "t"}])
    assert isinstance(out, str) and len(out) > 0


def test_analysis_complete_enough_detects_missing_report_ending():
    complete = (
        "## 十一、我的明確立場\n"
        "淨分 +1\n立場：中性\n"
        "\n## 十二、一句話總結\n完成"
    )
    truncated = "## 十一、我的明確立場\n淨分 +1\n立場：中性\n"
    assert mr._analysis_complete_enough(complete) is True
    assert mr._analysis_complete_enough(truncated) is False


def test_strip_llm_watchlist_section_keeps_summary():
    text = (
        "## 十一、我的明確立場\n淨分 +1\n立場：中性\n"
        "\n## 十二、今日台股關注五檔\n### 2330 台積電\n- 不應渲染\n"
        "\n## 十三、一句話總結\n完成"
    )
    stripped = mr._strip_llm_watchlist_section(text)
    assert "今日台股關注五檔" not in stripped
    assert "2330 台積電" not in stripped
    assert "一句話總結" in stripped


def test_call_llm_analysis_retries_once_when_truncated(monkeypatch):
    calls = {"n": 0}

    def fake_call(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return "## 十一、我的明確立場\n淨分 +1\n立場：中性\n"
        return (
            "## 十一、我的明確立場\n"
            "淨分 +1\n立場：中性\n"
            "\n## 十二、一句話總結\n完成"
        )

    monkeypatch.setattr(mr, "_call_llm_text", fake_call)
    out = mr.call_llm_analysis(_empty_quotes(), {"error": "x"}, {"error": "x"}, [])
    assert calls["n"] == 2
    assert "一句話總結" in out


def test_call_llm_analysis_falls_back_when_retry_still_truncated(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(mr, "LLM_PROVIDER", "gemini")

    def fake_call(prompt):
        calls["n"] += 1
        return "## 十一、我的明確立場\n淨分 +1\n立場：中性\n"

    monkeypatch.setattr(mr, "_call_llm_text", fake_call)
    out = mr.call_llm_analysis(_empty_quotes(), {"error": "x"}, {"error": "x"}, [])
    assert calls["n"] == 2
    assert "LLM 服務暫時不可用" in out


def test_redact_secret_text_removes_configured_secrets_and_query_keys(monkeypatch):
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "deepseek-secret")
    text = (
        "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent"
        "?key=gemini-secret Authorization: Bearer deepseek-secret"
    )
    redacted = mr._redact_secret_text(text)
    assert "gemini-secret" not in redacted
    assert "deepseek-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_gemini_call_sends_key_header_not_query(monkeypatch):
    captured = {}
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "gemini-secret")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    def fake_post(url, json, timeout, headers=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        return FakeResponse()

    monkeypatch.setattr(mr.requests, "post", fake_post)
    assert mr._call_gemini_once("gemini-test", "prompt") == "ok"
    assert "gemini-secret" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "gemini-secret"


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


def test_last_known_usdtwd_stale_fallback(tmp_path, monkeypatch):
    """即時匯率失敗時,從 history 讀最近非空 usdtwd 昨值;超齡或無檔回 None。"""
    import datetime as dt
    import json
    p = tmp_path / "history.json"
    p.write_text(json.dumps([
        {"date": "2026-07-01", "usdtwd": 31.5},
        {"date": "2026-07-03", "usdtwd": 31.8},   # 最新非空
        {"date": "2026-07-02", "usdtwd": None},
        {"date": "bad-date", "usdtwd": 99.9},      # 壞日期不可入選
    ]), encoding="utf-8")
    monkeypatch.setattr(mr, "STATE_FILE", p)
    now = dt.datetime(2026, 7, 6, tzinfo=mr.TPE)
    r = mr._last_known_usdtwd(now_tpe=now)
    assert r == {"value": 31.8, "date": "2026-07-03", "age_days": 3}
    assert mr._last_known_usdtwd(max_age_days=2, now_tpe=now) is None   # 超齡
    monkeypatch.setattr(mr, "STATE_FILE", tmp_path / "missing.json")
    assert mr._last_known_usdtwd(now_tpe=now) is None                   # 無檔


def test_last_known_usdtwd_skips_stale_rows(tmp_path, monkeypatch):
    """Codex 回歸:usdtwd_stale=True 的筆不可被當真觀測,否則昨值自我延續、護欄失效。"""
    import datetime as dt
    import json
    p = tmp_path / "history.json"
    p.write_text(json.dumps([
        {"date": "2026-06-25", "usdtwd": 31.0},                        # 真觀測,但 11 天前
        {"date": "2026-07-05", "usdtwd": 31.9, "usdtwd_stale": True},  # 昨值降級,須跳過
    ]), encoding="utf-8")
    monkeypatch.setattr(mr, "STATE_FILE", p)
    now = dt.datetime(2026, 7, 6, tzinfo=mr.TPE)
    # 唯一真觀測已 >7 天、stale 筆被跳過 → None(護欄有效,不會延用 31.9)
    assert mr._last_known_usdtwd(now_tpe=now) is None


def test_build_data_quality_usdtwd_stale_is_fallback():
    """USDTWD_STALE 標記存在時,匯率項顯示 fallback + 昨值天數,不再標 error。"""
    quotes = {"QQQ": {"ticker": "QQQ", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "TSM": {"ticker": "TSM", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "SPY": {"ticker": "SPY", "close": 1.0, "prev_close": 1.0, "date": "d"},
              "USDTWD": 31.8, "USDTWD_STALE": {"value": 31.8, "date": "2026-07-03", "age_days": 3},
              "MACRO": {}, "TAIEX_PRED": {}, "NIGHT_TXF": {},
              "TAIFEX_OI": {}, "MARGIN": {}, "SEC_FILINGS": []}
    dq = mr.build_data_quality(quotes, {"error": "x"}, {"error": "x"}, news=[], tw0050=[])
    fx = next(d for d in dq if d["name"] == "USD/TWD 匯率")
    assert fx["status"] == "fallback" and "昨值" in fx["detail"] and "3" in fx["detail"]


def test_build_data_quality_flags_tsm_adr_sox_divergence():
    """SOX 大幅變動但 TSM ADR 幾乎不動 → 標記疑報價未更新(2026-07-10 事故);一般日不誤報。"""
    base = {"QQQ": {"ticker": "QQQ", "close": 1.0, "prev_close": 1.0, "date": "d"},
            "TSM": {"ticker": "TSM", "close": 436.0, "prev_close": 436.0, "date": "d",
                    "change_pct": 0.0},
            "SPY": {"ticker": "SPY", "close": 1.0, "prev_close": 1.0, "date": "d"},
            "USDTWD": 31.0, "TAIEX_PRED": {}, "NIGHT_TXF": {}, "TAIFEX_OI": {},
            "MARGIN": {}, "SEC_FILINGS": []}
    q = {**base, "MACRO": {"SOX": {"close": 12960, "change_pct": 3.06}}}
    dq = mr.build_data_quality(q, {"error": "x"}, {"error": "x"}, news=[], tw0050=[])
    tsm = next((d for d in dq if d["name"] == "TSM ADR 新鮮度"), None)
    assert tsm is not None and "背離" in tsm["detail"]
    # 一般日(SOX 小動)不誤報
    q2 = {**base, "MACRO": {"SOX": {"close": 12960, "change_pct": 0.5}}}
    dq2 = mr.build_data_quality(q2, {"error": "x"}, {"error": "x"}, news=[], tw0050=[])
    assert not any(d["name"] == "TSM ADR 新鮮度" for d in dq2)


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


# === DeepSeek 400 → 精簡 payload 自動重試 ===

class _FakePostResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            err = mr.requests.exceptions.HTTPError(f"{self.status_code} Bad Request")
            err.response = self
            raise err

    def json(self):
        return self._payload


def test_deepseek_400_retries_with_slim_payload(monkeypatch):
    """thinking/reasoning_effort 造成 400 時,應去掉這些參數以精簡 payload 重試並成功。"""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json or {})
        if "thinking" in (json or {}):
            return _FakePostResp(400, text='{"error":{"message":"unsupported param"}}')
        return _FakePostResp(200, {"choices": [{"message": {"content": "分析內容"}}],
                                   "usage": {}})

    monkeypatch.setattr(mr.requests, "post", fake_post)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(mr, "DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(mr, "DEEPSEEK_REASONING_EFFORT", "high")
    out = mr._call_deepseek("prompt")
    assert out == "分析內容"
    assert any("thinking" in c for c in calls)          # 第一次帶 thinking → 400
    assert any("thinking" not in c for c in calls)       # slim 重試不帶 → 成功


def test_deepseek_400_body_in_error(monkeypatch):
    """所有嘗試 400 時,RuntimeError 應帶回 DeepSeek 的錯誤內文(供信件診斷)。"""
    def always_400(url, json=None, headers=None, timeout=None):
        return _FakePostResp(400, text='{"error":{"message":"context length exceeded"}}')

    monkeypatch.setattr(mr.requests, "post", always_400)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(mr, "DEEPSEEK_MODEL", "deepseek-v4-flash")  # 無 thinking
    import pytest
    with pytest.raises(RuntimeError) as ei:
        mr._call_deepseek("prompt")
    assert "context length exceeded" in str(ei.value)


def test_deepseek_request_respects_shared_wall_clock_budget(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["timeout"] = timeout
        return _FakePostResp(
            200, {"choices": [{"message": {"content": "ok"}}], "usage": {}})

    monkeypatch.setattr(mr.requests, "post", fake_post)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(mr, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    previous = mr._LLM_DEADLINE
    mr._LLM_DEADLINE = time.monotonic() + 2
    try:
        assert mr._call_deepseek("prompt") == "ok"
    finally:
        mr._LLM_DEADLINE = previous
    assert 0 < captured["timeout"] <= 2


# === 外資台指期淨空警告:看「方向(日變化)+ 現貨對照」而非只看水位 ===

def _short_oi_alert(oi, chg, spot):
    alerts = mr.detect_market_alerts(
        {"MACRO": {}}, {}, {},
        {"foreign_oi_net": oi, "foreign_oi_chg": chg, "foreign_spot_net_lot": spot})
    return next((a for a in alerts if "台指期淨空" in a["title"]), None)


def test_short_oi_hedge_downgrades_to_yellow():
    """大淨空但外資現貨大買 → 多為避險,降為 yellow、不喊開低(對應『昨天同樣淨空卻漲』)。"""
    a = _short_oi_alert(-66772, -2000, 86505)
    assert a and a["level"] == "yellow"
    assert "避險" in a["title"] or "避險" in a["detail"]


def test_short_oi_increasing_is_red():
    """空單較前日明顯新增 + 現貨未買超 → 真實空壓,red。"""
    a = _short_oi_alert(-66772, -12000, -5000)
    assert a and a["level"] == "red"
    assert "再增" in a["title"] or "新增" in a["detail"]


def test_short_oi_stable_is_orange():
    """水位大但日變化持平、無明顯現貨買超 → 既有部位,orange、方向訊號弱。"""
    a = _short_oi_alert(-66772, -800, -500)
    assert a and a["level"] == "orange"
    assert "既有" in a["title"] or "方向訊號偏弱" in a["detail"]


def test_short_oi_no_change_data_still_warns():
    """無日變化/現貨資料時仍給保守警告(orange),不崩。"""
    a = mr.detect_market_alerts({"MACRO": {}}, {}, {}, {"foreign_oi_net": -66772})
    hit = next((x for x in a if "台指期淨空" in x["title"]), None)
    assert hit is not None


def test_data_quality_flags_flat_2330_prediction_with_adr_move():
    """2330 預測與昨收持平、但 TSM ADR 波動明顯 → 資料品質表提示(透明度)。"""
    quotes = {"TSM": {"change_pct": -0.65}}
    predictions = {"weighted_final": 2415.0, "last_2330": 2415.0}
    dq = mr.build_data_quality(quotes, {}, predictions, [], [])
    assert any(d["name"] == "2330 預測" and d["status"] == "fallback" for d in dq)
    # ADR 幾乎沒動 → 持平預測屬正常,不提示
    quotes2 = {"TSM": {"change_pct": 0.05}}
    dq2 = mr.build_data_quality(quotes2, {}, predictions, [], [])
    assert not any(d["name"] == "2330 預測" for d in dq2)
    # 預測有明確方向 → 不提示
    predictions3 = {"weighted_final": 2390.0, "last_2330": 2415.0}
    dq3 = mr.build_data_quality(quotes, {}, predictions3, [], [])
    assert not any(d["name"] == "2330 預測" for d in dq3)


def test_world_news_feeds_and_prompt_block():
    """世界大事 feeds 存在(不掛 company_label→不進計分);prompt 組出獨立取材段與新節指引。"""
    for k in ("世界-國際大事", "世界-災難極端", "世界-科學太空", "世界-AI大事", "中央社國際"):
        assert k in mr.RSS_FEEDS
    news = [
        {"source": "世界-國際大事", "title": "某國停火協議破裂", "published": "2026-07-15"},
        {"source": "中央社國際", "title": "葉門局勢升溫", "published": "2026-07-15"},
        {"source": "世界-科學太空", "title": "舊聞無日期", "date_missing": True},   # 無日期不入段
        {"source": "鉅亨台股", "title": "台股大漲", "published": "2026-07-15"},      # 非世界來源不入段
    ]
    p = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, news, [], "")
    assert "【昨日世界大事新聞" in p
    assert "[國際大事] 某國停火協議破裂" in p            # 前綴剝除為類別標示
    assert "[中央社國際] 葉門局勢升溫" in p
    assert "舊聞無日期" not in p.split("【昨日世界大事新聞")[1].split("】")[1][:600]
    # 新節指引存在,且防誇大鐵則在內
    assert "七之二、世界大事速覽" in p
    assert "只寫「已發生」的事" in p


def test_world_news_block_caps_per_source():
    """每來源最多 4 則進 prompt(保跨類多樣性、控長度)。"""
    news = [{"source": "世界-國際大事", "title": f"事件{i}", "published": "2026-07-15"}
            for i in range(7)]
    p = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, news, [], "")
    blk = p.split("【昨日世界大事新聞")[1]
    assert "事件3" in blk and "事件4" not in blk


def test_world_items_excluded_from_market_buckets():
    """世界項目(即使被判 critical)不進市場配額桶,只出現在世界取材段(Codex review)。"""
    news = [
        {"source": "世界-國際大事", "world_cat": "國際大事", "importance": "critical",
         "title": "某區域戰爭爆發", "published": "2026-07-15"},
        {"source": "CNBC Top News", "importance": "critical",
         "title": "Fed 意外升息", "published": "2026-07-15"},
    ]
    p = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, news, [], "")
    # 世界標題只出現一次(取材段),不佔 ★★★ 市場桶
    assert p.count("某區域戰爭爆發") == 1
    assert "[國際大事] 某區域戰爭爆發" in p
    # 市場 critical 正常進桶
    assert "Fed 意外升息" in p


def test_dedup_news_preserves_world_cat():
    """同一事件同時出現在一般來源與世界來源:不論留哪版,world_cat 都要活下來(Codex review)。"""
    news = [
        {"source": "Google-地緣", "title": "美伊衝突再升級,油價跳漲",
         "summary": "很長的摘要" * 30},                      # keep_score 較高,會被保留
        {"source": "世界-國際大事", "world_cat": "國際大事",
         "title": "美伊衝突再升級,油價跳漲", "summary": "短"},
    ]
    out = mr.dedup_news(news)
    assert len(out) == 1
    assert out[0].get("world_cat") == "國際大事"            # 標記併到留下的那筆


def test_mixed_source_event_reaches_both_blocks():
    """跨源大事件(市場來源+世界來源皆報導):dedup 後市場配額桶與世界取材段都要有
    (Codex review 第二輪:不可因帶 world_cat 就從市場桶消失)。"""
    news = mr.dedup_news([
        {"source": "Google-地緣", "title": "美伊衝突升級油價飆漲",
         "summary": "很長的摘要" * 30},
        {"source": "世界-國際大事", "world_cat": "國際大事",
         "title": "美伊衝突升級油價飆漲", "summary": "短"},
    ])
    assert len(news) == 1 and news[0].get("world_and_market") is True
    news[0]["importance"] = "critical"
    news[0]["published"] = "2026-07-15"
    p = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, news, [], "")
    assert p.count("美伊衝突升級油價飆漲") == 2       # 市場桶一次 + 世界取材段一次


def test_yield_curve_read_plain_language():
    """美債殖利率曲線白話化:倒掛/偏小/正常三態,且不吐術語(隱藏「倒掛/殖利率曲線」)。"""
    warn = mr._yield_curve_read({"13W": {"close": 5.2}, "10Y": {"close": 4.5}})
    assert warn["flag"] == "warn" and "領先景氣轉弱" in warn["detail"]
    caution = mr._yield_curve_read({"13W": {"close": 4.4}, "10Y": {"close": 4.55}})
    assert caution["flag"] == "caution"
    normal = mr._yield_curve_read({"13W": {"close": 4.0}, "10Y": {"close": 4.6},
                                   "30Y": {"close": 4.8}})
    assert normal["flag"] == "normal" and "30 年 4.80%" in normal["detail"]
    # 白話:結論裡不得出現艱澀術語
    for r in (warn, caution, normal):
        assert "倒掛" not in r["detail"] and "殖利率曲線" not in r["detail"] and "2s10s" not in r["detail"]
    assert mr._yield_curve_read({"10Y": {"close": 4.5}}) == {}     # 缺短率 → 空


def test_optional_5y30y_failure_does_not_degrade_macro_quality():
    """選配 5Y/30Y 抓取失敗不得把總經來源判成 fallback(Codex review P2)。"""
    macro = {n: {"close": 1.0, "change_pct": 0.0} for n in
             ["VIX", "VIX9D", "SOX", "10Y", "DXY", "13W", "N225", "SSE",
              "NQ", "ES", "WTI", "GOLD", "BTC", "COPPER"]}
    macro["5Y"] = {"error": "資料不足"}      # 選配失敗
    macro["30Y"] = {"error": "資料不足"}
    dq = mr.build_data_quality({"MACRO": macro}, {}, {}, [], [])
    macro_row = next((d for d in dq if d["name"].startswith("總經/國際")), None)
    assert macro_row is not None and macro_row["status"] == "ok"   # 不因選配失敗降級


def test_basis_line_html_factual_no_sentiment():
    """台指期價差:純事實(不下看多/看空),隱藏「基差/逆價差」術語,除息季加季節註記。"""
    h = mr._basis_line_html({"fut_settle": 45565, "spot": 45381, "diff": 184, "div_season": True})
    assert "近月期貨 45,565" in h and "大盤現貨 45,381" in h and "期貨高 <b>184</b>" in h
    assert "除息旺季" in h and "非看空訊號" in h
    # 不下情緒結論、不吐術語
    for bad in ("法人看多", "法人看空", "法人避險", "基差", "逆價差", "正價差"):
        assert bad not in h
    low = mr._basis_line_html({"fut_settle": 45300, "spot": 45400, "diff": -100, "div_season": False})
    assert "期貨低 <b>100</b>" in h.replace("184", "184") or "期貨低 <b>100</b>" in low
    assert mr._basis_line_html({}) == ""                    # 無資料 → 空


# ===================== A4 估值溫度 + A5 選擇權磁吸價(2026-07-14)=====================

def test_third_wednesday():
    import datetime as dt
    assert mr._third_wednesday("202607") == dt.date(2026, 7, 15)
    assert mr._third_wednesday("202608") == dt.date(2026, 8, 19)
    assert mr._third_wednesday("bad") is None


def test_fetch_txo_magnet_math_and_wall_range(monkeypatch):
    """磁吸價=賣方總賠付最小履約價;牆只在 ±6% 內找(深價外樂透倉不當壓力/支撐);
    盤後 '-' 列與週別合約排除。"""
    def row(month, k, cp, oi):
        return {"Contract": "TXO", "ContractMonth(Week)": month, "StrikePrice": str(k),
                "CallPut": cp, "OpenInterest": oi}
    rows = []
    # 近月:put OI 集中低檔、call OI 集中高檔 → 磁吸落中間
    for k in (19000, 19500, 20000, 20500, 21000):
        rows.append(row("202607", k, "買權", str(max(0, (k - 20000) // 100 * 50 + 100))))
        rows.append(row("202607", k, "賣權", str(max(0, (20000 - k) // 100 * 50 + 100))))
    rows.append(row("202607", 20500, "買權", "-"))          # 盤後列 → 略過
    rows.append(row("202607W4", 20000, "買權", "99999"))    # 週別 → 略過
    rows.append(row("202608", 20000, "買權", "99999"))      # 次月 → 略過
    rows.append(row("202607", 26000, "買權", "50000"))      # 深價外(+30%)大 OI:不得當壓力牆
    rows.append(row("202607", 15000, "賣權", "50000"))      # 深價外(-25%)大 OI:不得當支撐牆

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R())
    out = mr.fetch_txo_magnet()
    assert out["month"] == "202607" and out["settle"] == "07/15"
    assert 19000 <= out["magnet"] <= 21000                 # 磁吸在近價區
    for wall in (out["call_wall"], out["put_wall"]):
        if wall is not None:
            assert abs(wall - out["magnet"]) / out["magnet"] <= 0.06   # 牆在 ±6% 內


def test_fetch_market_valuation_bands(monkeypatch):
    """全市場 PE/殖利率中位數與溫度標籤;樣本不足回 {};2330 個股估值抽取。"""
    def rows(pe):
        out = [{"Code": f"{1000+i}", "PEratio": str(pe), "DividendYield": "3.5",
                "PBratio": "1.5"} for i in range(150)]
        out.append({"Code": "2330", "PEratio": "32.8", "DividendYield": "0.9",
                    "PBratio": "10.7"})
        return out

    class R:
        def __init__(self, d):
            self._d = d

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R(rows("12")))
    v = mr.fetch_market_valuation()
    assert v["label"] == "偏便宜" and v["tsmc"]["pe"] == 32.8
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R(rows("20")))
    assert mr.fetch_market_valuation()["label"] == "偏貴"
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R(rows("15")[:50]))   # 樣本不足
    assert mr.fetch_market_valuation() == {}


def test_txo_wall_requires_positive_side_oi(monkeypatch):
    """牆的該側 OI 必須 >0:履約價只掛賣權時不得被當「上方壓力」(Codex review)。"""
    def row(k, cp, oi):
        return {"Contract": "TXO", "ContractMonth(Week)": "202607",
                "StrikePrice": str(k), "CallPut": cp, "OpenInterest": str(oi)}
    rows = []
    for k in (19600, 19800, 20000, 20200, 20400):
        rows.append(row(k, "賣權", 500))          # 全部只有賣權
    rows.append(row(19800, "買權", 300))          # 僅一檔有買權(磁吸下方)
    class R:
        def raise_for_status(self):
            pass
        def json(self):
            return rows
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R())
    out = mr.fetch_txo_magnet()
    assert out["call_wall"] is None               # 磁吸上方無買權 OI → 無壓力牆,不得亂指


def test_valuation_and_magnet_reach_prompt():
    """兩項白話資料要進 LLM prompt 當背景(顯示+prompt,皆不進計分;Codex review)。"""
    q = _empty_quotes()
    q["VALUATION"] = {"median_pe": 20.5, "median_yield": 3.37, "label": "偏貴",
                      "tsmc": {}, "n": 800}
    q["TXO_MAGNET"] = {"magnet": 45500.0, "call_wall": 47000.0,
                       "put_wall": 45000.0, "settle": "07/15", "month": "202607"}
    p = mr._build_prompt(q, {"error": "x"}, {"error": "x"}, [], [], "")
    assert "台股估值溫度" in p and "偏貴" in p
    assert "結算磁吸參考價約 45,500" in p and "07/15 結算" in p
    # 無資料時不產生空段
    p2 = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, [], [], "")
    assert "結算磁吸參考價" not in p2


# ===================== P0-2 寄信保命時間預算 =====================

def test_run_budget_gate(monkeypatch):
    """時間閘:剩餘充足→執行;不足→跳過並記錄降級;未設 deadline→不限制。"""
    import time
    monkeypatch.setattr(mr, "_RUN_DEADLINE", None)
    mr._DEGRADED_STEPS.clear()
    assert mr._run_budget_ok(999, "x") is True and mr._DEGRADED_STEPS == []
    monkeypatch.setattr(mr, "_RUN_DEADLINE", time.monotonic() + 600)
    assert mr._run_budget_ok(360, "全文") is True and mr._DEGRADED_STEPS == []
    monkeypatch.setattr(mr, "_RUN_DEADLINE", time.monotonic() + 100)
    assert mr._run_budget_ok(360, "全文擷取") is False
    assert "全文擷取" in mr._DEGRADED_STEPS
    mr._DEGRADED_STEPS.clear()


def test_degraded_steps_surface_in_data_quality(monkeypatch):
    """跳過的步驟出現在資料品質(供 LLM 知悉、透明);去重不重覆。"""
    monkeypatch.setattr(mr, "_DEGRADED_STEPS",
                        ["重大事件全文擷取", "重大事件全文擷取", "LLM 新聞事件抽取"])
    dq = mr.build_data_quality({}, {}, {}, [], [])
    row = next((d for d in dq if d["name"] == "時間預算"), None)
    assert row is not None and row["status"] == "fallback"
    assert "全文擷取" in row["detail"] and "事件抽取" in row["detail"]
    assert row["detail"].count("全文擷取") == 1        # 去重


def test_no_degraded_row_when_budget_healthy():
    """時間充足(無降級)→ 不產生時間預算列。"""
    mr._DEGRADED_STEPS.clear()
    dq = mr.build_data_quality({}, {}, {}, [], [])
    assert not any(d["name"] == "時間預算" for d in dq)


# ===================== P1-4 觀測性 run manifest =====================

def test_run_manifest_and_step_summary(tmp_path, monkeypatch):
    """階段耗時 manifest 寫入 JSON,並在 GITHUB_STEP_SUMMARY 存在時附 markdown 表。"""
    import time
    import json as _json
    import datetime as dt
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", tmp_path / "run_manifest.json")
    ss = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(ss))
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", ["重大事件全文擷取"])
    monkeypatch.setattr(mr, "_FEED_STATS",
                        {"news.google.com": {"ok": 40, "fail": 6, "streak": 0},
                         "feeds.bloomberg.com": {"ok": 0, "fail": 1, "streak": 1}})
    t = time.monotonic()
    monkeypatch.setitem(mr._RUN_MANIFEST, "marks",
                        [("行情", t), ("新聞", t + 30), ("預測", t + 80),
                         ("LLM", t + 260), ("完成", t + 300)])
    mr._write_run_manifest(dt.datetime(2026, 7, 15, 6, 50))
    m = _json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["total_seconds"] == 300.0
    labels = {p["label"]: p["seconds"] for p in m["phases"]}
    # marks 相鄰差:行情30、新聞50、預測180、LLM40(完成為終點,不產生階段)
    assert labels["行情"] == 30.0 and labels["新聞"] == 50.0
    assert labels["預測"] == 180.0 and labels["LLM"] == 40.0 and "完成" not in labels
    assert m["degraded_steps"] == ["重大事件全文擷取"]
    assert m["feeds"]["news.google.com"]["fail"] == 6
    # Step Summary markdown 表存在且列出最慢階段、降級、失敗來源
    summ = ss.read_text(encoding="utf-8")
    assert "晨報執行摘要" in summ and "| 階段 | 耗時(s) |" in summ
    assert "時間預算降級" in summ and "bloomberg" in summ


def test_run_manifest_no_step_summary_when_env_absent(tmp_path, monkeypatch):
    """非 Actions 環境(無 GITHUB_STEP_SUMMARY)→ 只寫 JSON,不炸。"""
    import time
    import datetime as dt
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", tmp_path / "rm.json")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    monkeypatch.setattr(mr, "_FEED_STATS", {})
    t = time.monotonic()
    monkeypatch.setitem(mr._RUN_MANIFEST, "marks", [("a", t), ("完成", t + 10)])
    mr._write_run_manifest(dt.datetime(2026, 7, 15, 6, 50))
    assert (tmp_path / "rm.json").exists()


def test_mark_phase_records():
    mr._RUN_MANIFEST["marks"].clear()
    mr._mark_phase("x")
    mr._mark_phase("y")
    labels = [m[0] for m in mr._RUN_MANIFEST["marks"]]
    assert labels == ["x", "y"]
    mr._RUN_MANIFEST["marks"].clear()


def test_git_push_skips_missing_paths_not_whole_push(tmp_path, monkeypatch):
    """某 state 檔不存在(如 manifest 寫入失敗)不得讓整個 state push 被跳過(Codex review):
    `git add` 只帶入存在的路徑,history/podcast 等仍照常 push。"""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("DRY_RUN", raising=False)
    real = tmp_path / "history.json"
    real.write_text("[]", encoding="utf-8")
    missing = tmp_path / "run_manifest.json"          # 不建立 → 不存在
    added = {}

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "add"]:
            added["paths"] = cmd[2:]
        class R:
            returncode = 1                            # diff --cached --quiet → 有變動
        return R()
    monkeypatch.setattr(mr.subprocess, "run", fake_run)
    mr._git_commit_and_push_state([str(real), str(missing)], "chore: test")
    # 只 add 存在者;不存在的 manifest 被濾掉,git add 不會因它整批失敗
    assert str(real) in added["paths"] and str(missing) not in added["paths"]


def test_git_push_all_missing_returns_early(tmp_path, monkeypatch):
    """全部路徑都不存在 → 早退,不呼叫 git。"""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("DRY_RUN", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(mr.subprocess, "run",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    mr._git_commit_and_push_state([str(tmp_path / "nope.json")], "chore: test")
    assert called["n"] == 0


# ── G2 事件情境決策表(prompt 層) ────────────────────────────────────────────
def test_format_event_scenarios_filters_window_and_keeps_notes():
    import datetime as dt
    now = dt.datetime.now(mr.TPE)
    today = now.date()
    cal = [
        {"date": today, "time": "20:30", "title": "[USD] CPI y/y",
         "note": "預期 3.1%、前值 3.2%", "impact": "high"},
        {"date": today + dt.timedelta(days=1), "time": "盤後(美東)",
         "title": "NVDA 財報", "note": "", "impact": "high"},
        {"date": today + dt.timedelta(days=10), "time": "10:00",
         "title": "[USD] 太遠的事件", "note": "預期 X", "impact": "high"},
    ]
    out = mr._format_event_scenarios(cal, now_tpe=now)
    assert "CPI" in out and "預期 3.1%" in out       # 視窗內、保留預期/前值
    assert "NVDA 財報" in out
    assert "太遠的事件" not in out                    # 視窗外(>48h)剔除


def test_format_event_scenarios_empty_returns_placeholder():
    assert "無重大排程事件" in mr._format_event_scenarios([])
    assert "無重大排程事件" in mr._format_event_scenarios(None)


def test_format_event_scenarios_accepts_datetime_date_without_typeerror():
    """財報 adapter 可能存入 datetime(date 子類);date<=datetime 比較會 TypeError,
    須先正規化成 date,否則一顆壞事件讓整份 prompt 降級(Codex review)。"""
    import datetime as dt
    now = dt.datetime.now(mr.TPE)
    cal = [{"date": dt.datetime(now.year, now.month, now.day, 13, 30),
            "time": "盤後(美東)", "title": "NVDA 財報", "note": "", "impact": "high"}]
    out = mr._format_event_scenarios(cal, now_tpe=now)   # 不可拋例外
    assert "NVDA 財報" in out


def test_build_prompt_has_event_scenario_section_with_injected_events():
    import datetime as dt
    today = dt.datetime.now(mr.TPE).date()
    cal = [{"date": today, "time": "20:30", "title": "[USD] CPI y/y",
            "note": "預期 3.1%、前值 3.2%", "impact": "high"}]
    p = mr._build_prompt(_empty_quotes(EVENT_CALENDAR=cal),
                         {"error": "x"}, {"error": "x"}, [], [], "")
    assert "七之三" in p                     # 新段標題存在
    assert "CPI" in p and "預期 3.1%" in p    # 事件與預期值注入 prompt
    assert "失效條件" in p                    # 指引出現
    assert "嚴禁自己編一個數字" in p          # 防幻覺鐵律出現


def test_build_prompt_event_scenario_section_present_when_no_events():
    """無事件時段落仍在(帶佔位提示),指引 LLM 寫「無重大排程事件」一行。"""
    p = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, [], [], "")
    assert "七之三" in p
    assert "未來 48 小時無重大排程事件" in p


# ── G3 世界證據門檻警示 ──────────────────────────────────────────────────────
def _macro_ok(**over):
    base = {k: {"close": 100.0, "prev_close": 100.0, "change_pct": 0.0,
                "pct_rank_252d": 50.0} for k in
            ("VIX", "VIX9D", "SOX", "10Y", "DXY", "13W", "N225", "SSE",
             "NQ", "ES", "WTI", "GOLD", "BTC", "COPPER")}
    base.update(over)
    return base


def test_world_evidence_quiet_by_default():
    """平常(所有指標正常)→ 不出現任何警示。"""
    assert mr._world_evidence_signals(_macro_ok(), {"change_pct": 0.3}) == []


def test_world_evidence_move_spike_or_percentile_or_level():
    for mv in ({"change_pct": 15.0, "pct_rank_252d": 50, "close": 100},   # 單日急升
               {"change_pct": 1.0, "pct_rank_252d": 95, "close": 100},    # 逼近一年高
               {"change_pct": 1.0, "pct_rank_252d": 50, "close": 140}):   # 絕對水位高
        sig = mr._world_evidence_signals(_macro_ok(MOVE=mv), {"change_pct": 0.3})
        assert any("債市波動" in s for s in sig)


def test_world_evidence_breadth_divergence():
    """指數(SPY)漲但等權(RSP)明顯落後 → 廣度警示。"""
    sig = mr._world_evidence_signals(
        _macro_ok(RSP={"change_pct": -0.6}), {"change_pct": 1.2})
    assert any("廣度" in s for s in sig)
    # 兩者同漲(廣度健康)→ 不觸發
    assert mr._world_evidence_signals(
        _macro_ok(RSP={"change_pct": 1.1}), {"change_pct": 1.2}) == []


def test_world_evidence_copper_gold_ratio_drop():
    sig = mr._world_evidence_signals(
        _macro_ok(COPPER={"change_pct": -4.0}, GOLD={"change_pct": 1.0}),
        {"change_pct": 0.3})
    assert any("銅金比" in s for s in sig)


def test_world_evidence_missing_data_is_safe():
    assert mr._world_evidence_signals({}, {}) == []
    assert mr._world_evidence_signals({"MOVE": {"error": "x"}, "RSP": None}, None) == []


def test_render_world_evidence_empty_and_nonempty():
    assert mr._render_world_evidence_html([]) == ""
    html = mr._render_world_evidence_html(["債市波動明顯升溫,僅供留意。"])
    assert "市場結構訊號" in html and "債市波動" in html
    assert "不影響本報立場計分" in html    # 明示不進計分


def test_move_rsp_optional_do_not_degrade_macro_quality():
    """MOVE/RSP 抓取失敗不得把整個總經來源判成 fallback(列入 _MACRO_OPTIONAL)。"""
    macro = _macro_ok(MOVE={"error": "no data"}, RSP={"error": "no data"})
    quotes = {
        "QQQ": {"ticker": "QQQ", "date": "2026-05-13", "close": 520, "prev_close": 515},
        "TSM": {"ticker": "TSM", "date": "2026-05-13", "close": 220, "prev_close": 218},
        "SPY": {"ticker": "SPY", "date": "2026-05-13", "close": 580, "prev_close": 578},
        "USDTWD": 31.0, "MACRO": macro,
        "TAIEX_PRED": {}, "NIGHT_TXF": {}, "TAIFEX_OI": {}, "MARGIN": {},
        "SEC_FILINGS": [],
    }
    dq = mr.build_data_quality(quotes, {"error": "x"}, {"error": "x"}, news=[], tw0050=[])
    macro_row = next(d for d in dq if d["name"].startswith("總經/國際/期貨/商品"))
    assert macro_row["status"] == "ok"    # MOVE/RSP error 被排除,其餘全 ok


# ── G4 敘事變化(Narrative Delta) ────────────────────────────────────────────
def test_format_narrative_delta_uses_last_entry_verbatim():
    hist = [
        {"date": "2026-07-10", "stance_label": "偏空", "critical_news": ["舊事件"]},
        {"date": "2026-07-13", "stance_label": "偏多",
         "critical_news": ["Fed 官員放鴿", "台積電法說優於預期"]},
    ]
    out = mr._format_narrative_delta(hist)
    assert "2026-07-13" in out and "昨日立場:偏多" in out   # 取最新一份(末尾)
    assert "Fed 官員放鴿" in out and "台積電法說優於預期" in out
    assert "舊事件" not in out                              # 不取更早的那份


def test_format_narrative_delta_empty_and_no_material():
    assert "無昨日紀錄可對照" in mr._format_narrative_delta([])
    assert "無昨日紀錄可對照" in mr._format_narrative_delta(None)
    # 有 entry 但無立場也無事件 → 佔位
    assert "無昨日紀錄可對照" in mr._format_narrative_delta(
        [{"date": "2026-07-13", "critical_news": []}])


def test_build_prompt_has_narrative_delta_section():
    hist = [{"date": "2026-07-13", "stance_label": "偏多",
             "critical_news": ["Fed 官員放鴿"]}]
    p = mr._build_prompt(_empty_quotes(HISTORY=hist),
                         {"error": "x"}, {"error": "x"}, [], [], "")
    assert "七之四" in p and "敘事變化" in p
    assert "昨日立場:偏多" in p and "Fed 官員放鴿" in p     # 昨日紀錄逐字注入
    assert "不可" in p and "替昨日補記" in p                # 防幻覺鐵律


def test_build_prompt_narrative_delta_placeholder_without_history():
    p = mr._build_prompt(_empty_quotes(HISTORY=[]),
                         {"error": "x"}, {"error": "x"}, [], [], "")
    assert "七之四" in p and "無昨日紀錄可對照" in p


def test_format_narrative_delta_excludes_same_day_rerun_entry():
    """同日重跑會把「今天早上的報告」存進 history;傳 today 須排除,不可拿今天當昨日
    (Codex review)。"""
    hist = [
        {"date": "2026-07-12", "stance_label": "偏空", "critical_news": ["前日事件"]},
        {"date": "2026-07-13", "stance_label": "偏多", "critical_news": ["今日事件"]},
    ]
    # today=2026-07-13:末筆(今天)被排除,退回取前一日
    out = mr._format_narrative_delta(hist, today="2026-07-13")
    assert "昨日立場:偏空" in out and "前日事件" in out
    assert "今日事件" not in out
    # 只有今天一筆 → 排除後無可對照
    assert "無昨日紀錄可對照" in mr._format_narrative_delta(
        [{"date": "2026-07-13", "stance_label": "偏多", "critical_news": ["今日事件"]}],
        today="2026-07-13")


# ── G5 週報錯誤檢討 ──────────────────────────────────────────────────────────
def test_compute_weekly_review_stats_math():
    hist = [
        {"date": "2026-07-06", "target_session_date": "2026-07-06",
         "pred_taiex": 100, "actual_open_taiex": 102},
        {"date": "2026-07-07", "target_session_date": "2026-07-07",
         "pred_taiex": 100, "actual_open_taiex": 99},
        {"date": "2026-07-08", "target_session_date": "2026-07-08",
         "pred_taiex": 100, "actual_open_taiex": 100.5,
         "critical_news": ["台積電法說優於預期"]},
    ]
    s = mr._compute_weekly_review_stats(hist, today="2099-01-01")
    t = s["taiex"]
    assert t["n"] == 3
    assert abs(t["mae_pct"] - 1.17) < 0.01           # (2+1+0.5)/3
    assert abs(t["bias_pct"] - 0.5) < 0.01           # (2-1+0.5)/3
    assert t["hit_rate_pct"] == 100 and t["n_dir"] == 2   # 兩次方向皆命中
    assert "台積電法說優於預期" in s["critical_events"]


def test_compute_weekly_review_stats_empty_and_excludes_today():
    assert mr._compute_weekly_review_stats([]) == {}
    assert mr._compute_weekly_review_stats(None) == {}
    # 只有今天的 entry → today 過濾後無資料
    assert mr._compute_weekly_review_stats(
        [{"date": "2026-07-13", "pred_taiex": 100, "actual_open_taiex": 101}],
        today="2026-07-13") == {}


def test_format_weekly_review_has_numbers():
    stats = {"taiex": {"n": 3, "mae_pct": 1.17, "bias_pct": 0.5,
                       "hit_rate_pct": 100, "n_dir": 2},
             "tw2330": None, "critical_events": ["Fed 放鴿"], "n_days": 3}
    out = mr._format_weekly_review(stats)
    assert "平均絕對誤差 1.17%" in out and "持續偏誤 +0.50%" in out
    assert "方向命中 100%" in out and "Fed 放鴿" in out
    assert mr._format_weekly_review({}) == ""


def test_build_prompt_weekly_review_section_present_and_absent():
    stats = {"taiex": {"n": 3, "mae_pct": 1.17, "bias_pct": 0.5,
                       "hit_rate_pct": 100, "n_dir": 2},
             "tw2330": None, "critical_events": ["台積電法說"], "n_days": 3}
    p = mr._build_prompt(_empty_quotes(WEEKLY_REVIEW=stats),
                         {"error": "x"}, {"error": "x"}, [], [], "")
    assert "七之五" in p and "平均絕對誤差 1.17%" in p and "台積電法說" in p
    assert "本週要重點驗證" in p                        # 指引出現
    # 平日(無 WEEKLY_REVIEW)→ 整段不出現
    p2 = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"}, [], [], "")
    assert "七之五" not in p2


def test_weekly_review_excludes_immature_records():
    """未成熟(無實際開盤)的週六紀錄不得佔位,其 critical_news 不得被當上週(Codex review)。"""
    # 只有未成熟紀錄 → {}(不讓純事件撐起七之五)
    assert mr._compute_weekly_review_stats(
        [{"date": "2026-07-11", "critical_news": ["週六事件"]}], today="2099-01-01") == {}
    # 一筆成熟 + 一筆未成熟:只算成熟者,未成熟事件被排除
    hist = [
        {"date": "2026-07-08", "pred_taiex": 100, "actual_open_taiex": 101,
         "critical_news": ["成熟事件"]},
        {"date": "2026-07-11", "critical_news": ["未成熟事件"]},   # 無 actual → 未成熟
    ]
    s = mr._compute_weekly_review_stats(hist, today="2099-01-01")
    assert "成熟事件" in s["critical_events"] and "未成熟事件" not in s["critical_events"]
    assert s["n_days"] == 1


def test_weekly_review_flat_prediction_counts_as_directional_miss():
    """預測『不變』但實際變動 → 未命中,須計入 n_dir(不可略過膨脹命中率,Codex review)。"""
    hist = [
        {"date": "2026-07-06", "pred_taiex": 100, "actual_open_taiex": 100},
        {"date": "2026-07-07", "pred_taiex": 100, "actual_open_taiex": 103},  # pred 平、實際漲
    ]
    t = mr._compute_weekly_review_stats(hist, today="2099-01-01")["taiex"]
    assert t["n_dir"] == 1 and t["hit_rate_pct"] == 0
