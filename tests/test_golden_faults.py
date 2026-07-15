"""P2-6|Golden 區塊順序測試 + 故障注入整合測試。

Golden 哲學:驗「區塊存在性 + 相對順序」,**不逐字比對**(文案微調不應紅);
故障注入:429 / 空資料 / 慢源(單 host 死掉) / SMTP 失敗,各自驗「晨報不可斷」
與 at-least-once 語意(寄成功才落狀態)。全部離線(無網路)。
"""
import pytest

import morning_report as mr


# ═══════════════ Golden:區塊順序 ═══════════════
def _golden_quotes():
    """填夠資料讓主要區段全部渲染(含 G1 曝險/G3 門檻警示)的固定 fixture。"""
    def base(t):
        return {"ticker": t, "date": "2026-07-13", "close": 100.0,
                "prev_close": 99.0, "change_pct": 1.01}
    return {
        "QQQ": base("QQQ"), "TSM": base("TSM"), "SPY": base("SPY"),
        "USDTWD": 31.0, "USDTWD_prev": 31.1,
        "MACRO": {
            "VIX": {"close": 16.4, "change_pct": -2.0, "pct_rank_252d": 30.0},
            "SOX": {"close": 5200.0, "change_pct": 1.5, "pct_rank_252d": 80.0},
            # G3:MOVE 異常 → 門檻警示卡要出現(驗它排在總經表之後)
            "MOVE": {"close": 140.0, "change_pct": 12.0, "pct_rank_252d": 95.0},
        },
        "MARKET_REGIME": "neutral",
        # TAIEX_PRED 帶 pred_open 就要求完整欄位(ci_lower/consensus…)——golden 聚焦
        # 區塊順序,這裡給空 dict 走「無預測」路徑即可
        "TAIEX_PRED": {},
        "TW0050_PRED": {"pred_open": 109.3, "last": 107.8},
        "MA200_STATUS": {"2330": {"name": "2330 台積電", "close": 2410,
                                  "ma200": 2200, "above": True, "dist_pct": 9.5}},
        # G1:曝險卡(驗它掛在第六段之後、200 日均線之前)
        "PORTFOLIO_RISK": {
            "tw_beta": 1.3, "qqq_beta": 0.8, "fx_beta": 0.5,
            "tw_cov": 1.0, "qqq_cov": 1.0, "fx_cov": 1.0, "cov_shown": 1.0,
            "scenarios": [{"label": "台股大盤跌 3%", "move_pct": -3.0, "delta_pct": -3.9}],
            "stress": [{"drawdown_pct": 10, "delta_pct": -8.0}],
            "n_samples": 110,
        },
        "SEC_FILINGS": [], "TAIFEX_OI": {}, "MARGIN": {}, "WEEKLY": {},
        "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
        "BACKTEST": "", "ALERTS": [], "DATA_QUALITY": [],
    }


def test_golden_section_order_stable():
    """關鍵區塊的「相對順序」是 golden 合約:重排視為破壞性變更,必須紅。
    (只驗順序與存在,不逐字比對——文案/樣式微調不應讓本測試紅。)"""
    fair = {"fair_price": 124.43, "last_00662_price": 122.5, "implied_change_pct": 1.5,
            "qqq_pct": 1.7, "fx_pct": 0.06, "avg_deviation_pct": 1.0, "premium_pct": 0.5}
    preds = {"mid": 2464, "last_2330": 2410, "low": 2440, "high": 2489}
    # 「我的明確立場」段會被抽到頂部結論卡(標題不留在分析本文)→ 本文順序 marker
    # 改用會留在原位的段標「科技板塊脈動」。
    analysis = ("## 科技板塊脈動\n台積電:法說前上修。\n\n"
                "## 我的明確立場\n淨分 +5\n立場:偏多\n\n## 一句話總結\n偏多,續抱。")
    html = mr.render_html(_golden_quotes(), fair, preds, analysis,
                          "2026-07-14 (Tue)", "每日報")
    markers = [
        "一、美股收盤行情",       # 行情表
        "二、總經指標",           # 總經表
        "市場結構訊號",           # G3 門檻警示(緊接總經)
        "個股開盤預測",           # 六、預測與公允價
        # G1 曝險卡已依使用者要求刪除(2026-07-15),不再是 golden 合約的一部分
        "長線趨勢參考",           # 200 日均線
        "科技板塊脈動",           # LLM 分析本文(立場段被抽頂,此段留原位)
    ]
    pos = [html.find(m) for m in markers]
    missing = [m for m, p in zip(markers, pos) if p < 0]
    assert not missing, f"區塊消失: {missing}"
    assert pos == sorted(pos), (
        "區塊順序改變: " + " → ".join(f"{m}@{p}" for m, p in zip(markers, pos)))


def test_golden_render_survives_everything_empty():
    """空資料故障注入:所有選配區塊皆空/缺 → 仍產出完整信件骨架,不崩不留破圖。"""
    quotes = {
        "QQQ": {"ticker": "QQQ", "error": "dead"},
        "TSM": {"ticker": "TSM", "error": "dead"},
        "SPY": {"ticker": "SPY", "error": "dead"},
        "USDTWD": None, "MACRO": {}, "SEC_FILINGS": [], "TAIFEX_OI": {},
        "MARGIN": {}, "WEEKLY": {}, "EARNINGS_PROXIMITY": {}, "HISTORY": [],
        "NIGHT_TXF": {}, "TAIEX_PRED": {}, "BACKTEST": "", "ALERTS": [],
        "DATA_QUALITY": [],
    }
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"},
                          "", "2026-07-14 (Tue)", "每日報")
    assert isinstance(html, str) and "晨報" in html            # 骨架仍在
    assert "你的持倉曝險" not in html                            # 無資料的卡不留空殼
    assert "市場結構訊號" not in html


# ═══════════════ 故障注入:429 ═══════════════
def test_http_get_retries_on_429_then_succeeds(monkeypatch):
    """429(限流)屬可重試狀態:退避後重試成功,不把限流當永久失敗。"""
    calls = {"n": 0}

    class R:
        def __init__(self, sc):
            self.status_code = sc

    def fake_get(url, **kw):
        calls["n"] += 1
        return R(429 if calls["n"] < 3 else 200)
    monkeypatch.setattr(mr.requests, "get", fake_get)
    monkeypatch.setattr(mr.time, "sleep", lambda s: None)      # 免等退避
    r = mr._http_get("https://example.com/x", retries=2)
    assert r.status_code == 200 and calls["n"] == 3


def test_http_get_429_exhausted_returns_last_response(monkeypatch):
    """重試耗盡仍 429 → 回最後一個 Response(呼叫端自行判斷),不無限重試。"""
    calls = {"n": 0}

    class R:
        status_code = 429

    monkeypatch.setattr(mr.requests, "get",
                        lambda url, **kw: calls.__setitem__("n", calls["n"] + 1) or R())
    monkeypatch.setattr(mr.time, "sleep", lambda s: None)
    r = mr._http_get("https://example.com/x", retries=2)
    assert r.status_code == 429 and calls["n"] == 3            # 1 + 2 次重試


# ═══════════════ 故障注入:慢源/死源(單 host) ═══════════════
def test_fetch_news_one_dead_host_does_not_kill_others(monkeypatch):
    """一個 host 整批逾時 → 其他 host 的新聞照常回傳(晨報不可斷)。"""
    mr._FEED_STATS.clear()
    mr._RSS_CONTENT_CACHE.clear()

    class _Feed:
        def __init__(self):
            self.entries = [{"title": "健康來源頭條", "link": "https://ok.com/1",
                             "summary": "內容", "published": ""}]

    def fake_parse(url, timeout=12):
        if "dead.com" in url:
            raise TimeoutError("simulated slow source")        # 慢源:恆逾時
        return _Feed()
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout", fake_parse)
    monkeypatch.setattr(mr, "_entry_published_dt", lambda e: None)
    monkeypatch.setattr(mr, "_parse_news_time_required", lambda p: None)
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no json")))
    monkeypatch.setattr(mr, "RSS_FEEDS", {
        "死源A": "https://dead.com/rss1", "死源B": "https://dead.com/rss2",
        "健康源": "https://ok.com/rss",
    })
    monkeypatch.setattr(mr, "NEWS_FETCH_WORKERS", 8)
    out = mr.fetch_news()
    assert any(n.get("title") == "健康來源頭條" for n in out)   # 健康 host 不受拖累


# ═══════════════ 故障注入:SMTP ═══════════════
def test_deliver_report_smtp_failure_does_not_commit_state(monkeypatch):
    """at-least-once:SMTP 失敗 → 例外上拋、絕不存檔/標記/寫歷史
    (否則寄失敗卻標 podcast shown → 永久漏寄;歷史也會多一筆沒寄出的報告)。"""
    events = []
    monkeypatch.setattr(mr, "send_email",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("SMTP 535")))
    monkeypatch.setattr(mr, "archive_report_html",
                        lambda *a, **k: events.append("archive"))
    monkeypatch.setattr(mr, "persist_delivered_report_state",
                        lambda *a, **k: events.append("persist"))
    with pytest.raises(RuntimeError):
        mr.deliver_report("<html>x</html>", "subj",
                          {"date": "2026-07-14"}, [{"guid": "ep1"}])
    assert events == []                                        # 半點狀態都不許動


def test_deliver_report_success_order_send_archive_persist(monkeypatch):
    """成功路徑順序合約:寄信 → 存檔 → 落狀態(順序即語意,重排=破壞 at-least-once)。"""
    events = []
    monkeypatch.setattr(mr, "send_email", lambda *a: events.append("send"))
    monkeypatch.setattr(mr, "archive_report_html",
                        lambda *a, **k: events.append("archive"))
    monkeypatch.setattr(mr, "persist_delivered_report_state",
                        lambda *a, **k: events.append("persist"))
    mr.deliver_report("<html>x</html>", "subj", {"date": "2026-07-14"}, [])
    assert events == ["send", "archive", "persist"]


def test_send_email_failure_message_has_no_secrets(monkeypatch):
    """SMTP 例外訊息可能進 log:確保帳密不外漏(Gmail app password)。"""
    import smtplib
    monkeypatch.setattr(mr, "GMAIL_USER", "me@gmail.com")
    monkeypatch.setattr(mr, "GMAIL_APP_PASSWORD", "supersecretpw")
    monkeypatch.setattr(mr, "RECIPIENTS", ["me@gmail.com"])

    class BoomSMTP:
        def __init__(self, *a, **k):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
    monkeypatch.setattr(smtplib, "SMTP_SSL", BoomSMTP)
    with pytest.raises(Exception) as ei:
        mr.send_email("<html>x</html>", "subj")
    assert "supersecretpw" not in str(ei.value)


# ═══════════════ 故障注入:時間預算(慢源的下游保命) ═══════════════
def test_budget_gate_skips_and_records_under_pressure(monkeypatch):
    """時間預算耗盡 → 非核心步驟跳過並記錄降級;資料品質區看得到(P0-2 與 P2-6 的接縫)。"""
    monkeypatch.setattr(mr, "_RUN_DEADLINE", mr.time.monotonic() + 10)   # 只剩 10 秒
    mr._DEGRADED_STEPS.clear()
    try:
        assert mr._run_budget_ok(5, "小步驟") is True                     # 夠 → 放行
        assert mr._run_budget_ok(300, "大步驟") is False                  # 不夠 → 跳過
        assert "大步驟" in mr._DEGRADED_STEPS
        dq = mr.build_data_quality(
            {"QQQ": {}, "TSM": {}, "SPY": {}, "MACRO": {},
             "TAIEX_PRED": {}, "NIGHT_TXF": {}, "TAIFEX_OI": {}, "MARGIN": {},
             "SEC_FILINGS": []},
            {"error": "x"}, {"error": "x"}, news=[], tw0050=[])
        budget_row = next(d for d in dq if d["name"] == "時間預算")
        assert "大步驟" in budget_row["detail"]
    finally:
        mr._DEGRADED_STEPS.clear()
