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


def test_dim_source_citations_keeps_confidence():
    """批#27:來源引用括號淡化為小灰字,信心標「[X 級・信心:…]」保留原樣。"""
    from render_utils import _dim_source_citations
    html = ("獲 Anthropic 訂單 [CNBC／鉅亨網]。傳導到 2330 CoWoS。"
            "[A 級・信心:高] 另一條 [財經皓角] [B 級・信心:中-低,資訊有限]")
    out = _dim_source_citations(html)
    # 來源 → 小灰字括號,原方括號消失
    assert "[CNBC／鉅亨網]" not in out and "（CNBC／鉅亨網）" in out
    assert "[財經皓角]" not in out and "（財經皓角）" in out
    assert "color:#94a3b8" in out and "font-size:12px" in out
    # 信心標原樣保留(含方括號)
    assert "[A 級・信心:高]" in out
    assert "[B 級・信心:中-低,資訊有限]" in out


def test_dim_source_citations_noop_without_brackets():
    from render_utils import _dim_source_citations
    assert _dim_source_citations("純文字無括號") == "純文字無括號"


def test_dim_source_citations_preserves_semantic_tags():
    """批#27 r4(Codex):語義/風險標籤 [stale]、[geo_critical] 不得被誤當來源
    淡化(R13 休市要求輸出醒目 [stale]);媒體來源仍淡化。"""
    from render_utils import _dim_source_citations
    out = _dim_source_citations("QQQ [stale] 延續值 [geo_critical] 事件 [中央社]")
    assert "[stale]" in out and "[geo_critical]" in out   # 語義標籤原樣保留
    assert "[中央社]" not in out and "（中央社）" in out    # 媒體來源淡化
    # Codex r5:小寫 ASCII 媒體名仍是來源,必須淡化(不可被語義標籤白名單誤豁免)
    low = _dim_source_citations("報導 [cnbc] 與 [reuters]")
    assert "[cnbc]" not in low and "（cnbc）" in low
    assert "[reuters]" not in low and "（reuters）" in low
    # Codex r6:含「級」的評級機構來源仍是來源,必須淡化(不可被誤當信心標)
    rate = _dim_source_citations("引用 [惠譽評級] 與 [標普全球評級]")
    assert "[惠譽評級]" not in rate and "（惠譽評級）" in rate
    assert "[標普全球評級]" not in rate and "（標普全球評級）" in rate


def _full_quotes():
    def base(t):
        return {"ticker": t, "date": "2026-05-13", "close": 100.0,
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


def test_render_html_size_guard_truncates_low_priority(monkeypatch):
    """trim 模式:超標時依優先序移除;體育在政策/醫界/文獻/五檔之後才砍。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "trim")
    # 內容敏感估算器:只要還含體育區塊就判超標,移除後即降回門檻內 → 驗證順序與停手
    monkeypatch.setattr(
        mr, "_estimated_email_kb",
        lambda h: 120.0 if "中華職棒 最新賽果" in h else 80.0)
    quotes = {**_full_quotes(), "SPORTS": {"news": {}, "cpbl_scores": [
        {"away": "統一", "home": "味全", "away_score": 5, "home_score": 3,
         "winner": "away", "date": "06/14"}]}}
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"},
                          "## 測試分析", "2026-05-14 (Wed)", "每日報")
    assert "為避免 Gmail 截斷" in html and "體育" in html
    assert "醫學文獻" not in html and "Podcast" not in html   # 砍到體育就停,沒續砍
    assert "中華職棒 最新賽果" not in html                    # 體育確實被移除(closure 重組生效)
    # 核心永不被剪
    assert "一、美股收盤行情" in html and "個股開盤預測" in html and "2330" in html


def test_render_html_has_preheader_with_key_numbers():
    """收件匣預覽文字(preheader):含當日關鍵數字、在正文之前、隱藏、無持股洩漏、冪等。"""
    import re
    q = {**_full_quotes(),
         "TAIEX_PRED": {"pred_open": 45210, "last_close": 45000, "weighted_pct": 0.47,
                        "ci_lower": 44500, "ci_upper": 45900, "consensus": "偏多",
                        "signals": [], "signal_std": 2.0, "signal_count": 3},
         "TW0050_PRED": {"last": 96.5, "pred_open": 95.4, "pred_pct": -1.14,
                         "method": "0.5 × 2330 + 0.5 × 加權指數"}}
    fair = {"fair_price": 116.99, "last_00662_price": 118.8, "qqq_pct": -1.51,
            "implied_change_pct": -1.52, "method": "簡化版", "samples": 0}
    preds = {"last_2330": 2265.0, "mid": 2192.5, "model2_regression": 2187.38,
             "model3_adr_decay": 2229.2, "range": (2187.38, 2229.2)}
    analysis = "## 十二、我的明確立場\n淨分 +3\n**立場：偏多**\n"
    html = mr.render_html(q, fair, preds, analysis, "2026-06-16", "每日報")
    m = re.search(r'mso-hide:all[^>]*>([^<]*)</div>', html)
    assert m, "preheader 隱藏 div 應存在"
    preheader = m.group(1)
    # preheader 在正文(hero)之前
    assert html.find("mso-hide:all") < html.find("MORNING MARKET BRIEF")
    # 含當日關鍵數字與立場
    assert "45,210" in preheader and "2,192" in preheader
    assert "95.40" in preheader and "116.99" in preheader and "偏多" in preheader
    # 隱藏、不佔版面
    assert "display:none" in html[:html.find("MORNING MARKET BRIEF")]
    # 冪等:同輸入兩次 render,preheader 一致
    html2 = mr.render_html({**q}, dict(fair), dict(preds), analysis, "2026-06-16", "每日報")
    assert re.search(r'mso-hide:all[^>]*>([^<]*)</div>', html2).group(1) == preheader


def test_render_html_preheader_falls_back_without_data():
    """無任何預測數字時 preheader 退回標題,不留空(空預覽會被 Gmail 抓信首雜訊)。"""
    import re
    html = mr.render_html(_full_quotes(), {"error": "x"}, {"error": "x"},
                          "沒有立場", "2026-06-16", "每日報")
    ph = re.search(r'mso-hide:all[^>]*>([^<]*)</div>', html).group(1)
    assert ph.strip() and "美股晨報" in ph


def test_archive_report_html_redacts_and_prunes(tmp_path, monkeypatch):
    """信件存檔(§B):寫 gzip、去識別移除持股列、修剪過舊檔;失敗不拋。"""
    import gzip
    monkeypatch.setattr(mr, "EMAIL_ARCHIVE_DIR", tmp_path)
    (tmp_path / "2020-01-01.html.gz").write_bytes(b"old")   # 很舊,應被修剪
    html = ("<body><!--PF_ROW_START--><tr><td>持倉1 昨日帳上 +NT$1.2萬</td></tr>"
            "<!--PF_ROW_END--><div>一、美股收盤行情</div></body>")
    out = mr.archive_report_html(html, "2026-07-06", keep_days=30)
    assert out and out.exists()
    saved = gzip.open(out, "rt", encoding="utf-8").read()
    assert "NT$1.2萬" not in saved and "昨日帳上" not in saved   # 敏感財務去識別
    assert "一、美股收盤行情" in saved                           # 其餘內容保留
    assert not (tmp_path / "2020-01-01.html.gz").exists()        # 舊檔已修剪
    assert mr.archive_report_html(html, "bad-date") is None       # 日期格式異常 → 不寫檔


def test_redact_scrubs_custom_portfolio_name_defense_in_depth(monkeypatch):
    """Codex 防禦縱深:即使持倉名稱漏到 PF 標記之外,存檔去識別也要遮蔽。"""
    monkeypatch.setattr(mr, "PORTFOLIO_1_NAME", "老婆帳戶")
    html = ("<div>老婆帳戶 對帳單</div>"
            "<!--PF_ROW_START--><tr><td>老婆帳戶 +NT$1.2萬</td></tr><!--PF_ROW_END-->")
    out = mr._redact_private_for_archive(html)
    assert "老婆帳戶" not in out and "NT$1.2萬" not in out and "PF_ROW_START" not in out


def test_render_html_portfolio_redacted_in_archive_but_present_in_email():
    """持股帳上損益:寄給本人的信中保留,存檔版(去識別)移除整列。"""
    q = {**_full_quotes(), "PORTFOLIO_ACTUAL": {
        "p1": {"gain_pct": 1.5, "gain_amount": 12000},
        "p1_name": "持倉1", "p2": {}, "p2_name": "持倉2"}}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    # 批#15:持倉列連信件本體都隱藏(使用者要求);去識別存檔自然也無
    assert "昨日帳上" not in html and "PF_ROW_START" not in html
    redacted = mr._redact_private_for_archive(html)
    assert "昨日帳上" not in redacted and "PF_ROW_START" not in redacted  # 存檔去識別
    assert "一、美股收盤行情" in redacted                          # 其餘保留


def test_render_html_size_guard_quiet_when_small(monkeypatch):
    monkeypatch.setattr(mr, "_estimated_email_kb", lambda h: 50.0)
    html = mr.render_html(_full_quotes(), {"error": "x"}, {"error": "x"},
                          "x", "2026-05-14", "每日報")
    assert "為避免 Gmail 截斷" not in html and "Gmail 可能於信末" not in html


def test_truncate_order_env_override(monkeypatch):
    monkeypatch.delenv("EMAIL_TRUNCATE_ORDER", raising=False)
    assert mr._truncate_order() == list(mr._TRUNCATE_SECTIONS)          # 預設順序
    monkeypatch.setenv("EMAIL_TRUNCATE_ORDER", "event_timeline, journals")
    order = mr._truncate_order()
    assert order[:2] == ["event_timeline", "journals"]                 # env 指定者優先
    assert set(order) == set(mr._TRUNCATE_SECTIONS)                     # 未列入者仍涵蓋全部
    monkeypatch.setenv("EMAIL_TRUNCATE_ORDER", "bogus,podcast")          # 未知 key 忽略
    assert mr._truncate_order()[0] == "podcast" and "bogus" not in mr._truncate_order()


def _podcast_episodes(n, points_per_ep=15):
    pts = ["這是一段很長的播客重點摘要內容用來灌版面測試" * 4 for _ in range(points_per_ep)]
    return [{"show": f"節目{i}", "title": f"EPMARK{i}",
             "digest": {"summary_points": list(pts), "tickers": []}} for i in range(n)]


def test_render_html_size_guard_reduces_podcast_before_nuking(monkeypatch):
    """trim 模式:Podcast 超標時先局部縮減集數,縮到 ≤3 集即降回門檻內 → 不整塊砍掉。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "trim")
    # 內容敏感估算器:只要 Podcast 卡片數(EPMARK)> 3 就判超標,縮到 3 集即降回門檻內。
    monkeypatch.setattr(mr, "_estimated_email_kb",
                        lambda h: 120.0 if h.count("EPMARK") > 3 else 80.0)
    q = {**_full_quotes(), "PODCAST_DIGEST": _podcast_episodes(10)}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-15", "每日報")
    assert html.count("EPMARK") == 3                  # 縮到 3 集
    assert "Podcast 已縮減集數" in html                # 提示局部縮減
    assert "已暫略:Podcast" not in html               # 未被整塊移除
    assert "一、美股收盤行情" in html                  # 核心永不被剪


def test_render_html_size_guard_compacts_points_for_few_large_episodes(monkeypatch):
    """trim 模式:只有 1–3 集但很長時,先壓每集條數(compact_points),而非整塊砍掉。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "trim")
    # 估算器對「渲染出的重點條數(PTMARK)」敏感:>12 條判超標,壓到 ≤12 條降回門檻內。
    monkeypatch.setattr(mr, "_estimated_email_kb",
                        lambda h: 120.0 if h.count("PTMARK") > 12 else 80.0)
    # 每條重點只含 1 個 PTMARK(長度靠其餘文字),才能用計數精準反映「條數」
    pts = [f"PTMARK_{j} " + "很長的播客重點內容" * 5 for j in range(15)]
    episodes = [{"show": f"節目{i}", "title": f"集{i}",
                 "digest": {"summary_points": list(pts), "tickers": []}} for i in range(2)]
    q = {**_full_quotes(), "PODCAST_DIGEST": episodes}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-15", "每日報")
    assert 0 < html.count("PTMARK") <= 12              # 條數被壓低,但未清空
    assert "已暫略:Podcast" not in html               # 沒被整塊砍
    assert "一、美股收盤行情" in html


def test_render_html_size_guard_drops_policy_before_podcast_and_sports(monkeypatch):
    """trim 模式:超標時依使用者優先序先砍政策,Podcast 與體育保留。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "trim")
    monkeypatch.setattr(mr, "_estimated_email_kb",
                        lambda h: 120.0 if "POLICYMARK" in h else 80.0)
    q = {**_full_quotes(),
         "TW_DAILY_INTELLIGENCE": {
             "policy": [{"title": "POLICYMARK 重大政策", "published": "2026-06-15", "link": "#"}],
             "medical": [{"title": "醫界訊息", "published": "2026-06-15", "link": "#"}]},
         "SPORTS": {"news": {}, "cpbl_scores": [
             {"away": "統一", "home": "味全", "away_score": 5, "home_score": 3,
              "winner": "away", "date": "06/14"}]},
         "PODCAST_DIGEST": [{"show": "股癌", "title": "EP670",
                             "digest": {"summary_points": ["重點一", "重點二"], "tickers": []}}]}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert "POLICYMARK" not in html                 # 政策被砍
    assert "已暫略" in html and "政府政策" in html    # 橫幅標示砍了政策
    assert "中華職棒" in html                         # 體育保留
    assert "Podcast 重點" in html and "股癌" in html  # Podcast 保留
    # 批#9 回歸:政策被 trim 整塊移除 → 回報未顯示,寄信端不得把沒看到的條目標成 shown
    assert q["TW_INTEL_POLICY_SHOWN"] is False


def test_render_html_reports_policy_shown_when_not_trimmed(monkeypatch):
    """政策區正常出現在信中 → TW_INTEL_POLICY_SHOWN=True(寄信端據此記錄已顯示)。"""
    monkeypatch.delenv("EMAIL_OVERFLOW_MODE", raising=False)   # 預設 full,不移除任何區塊
    q = {**_full_quotes(),
         "TW_DAILY_INTELLIGENCE": {
             "policy": [{"title": "重大政策", "published": "2026-06-15", "link": "#"}],
             "medical": []}}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert "重大政策" in html
    assert q["TW_INTEL_POLICY_SHOWN"] is True


def test_render_html_reports_only_shown_podcast_episodes(monkeypatch):
    """只有真正出現在信中的 Podcast 集才回報為 shown;被砍/縮掉的不算(否則永遠不再出現)。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "trim")   # 縮減/移除行為僅在 trim 模式
    eps = _podcast_episodes(10)
    # 不超標 → 全部顯示
    monkeypatch.setattr(mr, "_estimated_email_kb", lambda h: 50.0)
    q = {**_full_quotes(), "PODCAST_DIGEST": list(eps)}
    mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert len(q["PODCAST_SHOWN_EPISODES"]) == 10

    # 局部縮減到 3 集 → 只回報 3 集
    monkeypatch.setattr(mr, "_estimated_email_kb",
                        lambda h: 120.0 if h.count("EPMARK") > 3 else 80.0)
    q2 = {**_full_quotes(), "PODCAST_DIGEST": list(eps)}
    mr.render_html(q2, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert len(q2["PODCAST_SHOWN_EPISODES"]) == 3
    assert [e["title"] for e in q2["PODCAST_SHOWN_EPISODES"]] == [f"EPMARK{i}" for i in range(3)]

    # 整塊砍掉(永遠超標)→ 回報 0 集(不會把未顯示的集標成已顯示)
    monkeypatch.setattr(mr, "_estimated_email_kb", lambda h: 200.0)
    q3 = {**_full_quotes(), "PODCAST_DIGEST": list(eps)}
    mr.render_html(q3, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert q3["PODCAST_SHOWN_EPISODES"] == []


def test_render_html_keep_mode_compacts_points_keeps_all_episodes(monkeypatch):
    """keep 模式超標時只壓「每集重點條數」,所有集數都保留且都正確標記已顯示。
    絕不砍集數:load_podcast_digest 每節目只取 2 集未顯示、>96h 即丟棄,且顯示順序固定
    (台灣節目優先),砍集數會讓排序靠後的節目永遠輪不到而過期消失(Codex review)。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "keep")   # keep 為選項(預設已改 full)
    pts = [f"PTMARK{j} " + "很長的重點內容" * 5 for j in range(15)]
    eps = [{"show": f"節目{i}", "title": f"EPMARK{i}",
            "digest": {"summary_points": list(pts), "tickers": []}} for i in range(10)]
    # 重點條數 >60 判超標 → 迫使壓到每集 6 條(10 集 × 6 = 60)
    monkeypatch.setattr(mr, "_estimated_email_kb",
                        lambda h: 120.0 if h.count("PTMARK") > 60 else 80.0)
    q = {**_full_quotes(), "PODCAST_DIGEST": eps}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert html.count("EPMARK") == 10                  # 10 集全在,一集都沒砍
    assert len(q["PODCAST_SHOWN_EPISODES"]) == 10      # 全部正確標記已顯示
    assert html.count("PTMARK") <= 60                  # 條數被壓低
    assert "已暫略" not in html                         # keep 模式不省略任何區塊


def test_render_html_keep_mode_last_resort_reduces_episodes_and_shown_count(monkeypatch):
    """keep 模式壓到最小條數仍超標 → 最後手段才減集數,且 shown 數與實際渲染數一致
    (未渲染的集不標記已顯示,隔天會再出現;絕不「沒看到卻永久消失」)。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "keep")   # keep 為選項(預設已改 full)
    eps = _podcast_episodes(10)
    # 只要集數(EPMARK)>4 就判超標(壓條數救不了)→ 迫使最後手段減到 4 集
    monkeypatch.setattr(mr, "_estimated_email_kb",
                        lambda h: 120.0 if h.count("EPMARK") > 4 else 80.0)
    q = {**_full_quotes(), "PODCAST_DIGEST": list(eps)}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    n = html.count("EPMARK")
    assert 0 < n <= 4                                  # 集數被減到過關
    assert len(q["PODCAST_SHOWN_EPISODES"]) == n       # shown 與實際渲染一致,不多標
    assert "已暫略" not in html                         # 仍不省略任何區塊


def test_render_html_keep_mode_reduces_episodes_one_at_a_time(monkeypatch):
    """最後手段減集數一次只減 1 集:3 集超標但 2 集塞得下 → 應保留 2 集(不可跳到 1),
    否則多丟一集反而加重餓死風險(Codex review)。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "keep")   # keep 為選項(預設已改 full)
    eps = _podcast_episodes(3)
    monkeypatch.setattr(mr, "_estimated_email_kb",
                        lambda h: 120.0 if h.count("EPMARK") > 2 else 80.0)
    q = {**_full_quotes(), "PODCAST_DIGEST": list(eps)}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert html.count("EPMARK") == 2                   # 剛好減到 2 集
    assert len(q["PODCAST_SHOWN_EPISODES"]) == 2       # shown 同步


def test_render_html_medical_journals_at_email_end():
    """醫學文獻速報放信件最後(政策/醫界之後)——使用者 2026-07-14 明確拍板。

    歷史:2026-07-10 曾因剪信事故把文獻移到 podcast 之前;使用者今反向決定「文獻放最後」
    (與「低優先排信末、被 Gmail 剪先剪它們」的既定政策一致,接受此取捨)。
    """
    q = {**_full_quotes(),
         "MEDICAL_JOURNALS": [{"journal": "JAAD", "pmid": "123", "zh": "測試皮膚醫學文獻",
                               "title": "Test Derm Article"}],
         "TW_DAILY_INTELLIGENCE": {"policy": [{"title": "測試政策", "published": "2026-06-16",
                                               "category": "其他政策", "importance": 5}]},
         "PODCAST_DIGEST": [{"show": "股癌", "title": "EPPOD",
                             "digest": {"summary_points": ["重點一"], "tickers": []}}]}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert "醫學文獻速報" in html and "Podcast 重點" in html
    # 新順序:podcast → … → 政策/醫界 → 醫學文獻(信件最末內容區)
    assert html.index("Podcast 重點") < html.index("醫學文獻速報")
    if "台灣政策" in html:
        assert html.index("台灣政策") < html.index("醫學文獻速報")


def test_render_html_stays_within_gmail_ceiling_with_huge_podcast(monkeypatch):
    """trim 模式端到端:用超大 Podcast 灌爆版面,真實估算器下守衛仍把信壓進 Gmail 102KB 內,核心保留。"""
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "trim")
    q = {**_full_quotes(), "PODCAST_DIGEST": _podcast_episodes(30)}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-15", "每日報")
    assert mr._estimated_email_kb(html) <= 102
    assert "一、美股收盤行情" in html and "2330" in html


def test_render_html_keep_mode_does_not_omit_sections(monkeypatch):
    """預設 keep 模式:即使估算超標也不省略任何區塊,只加可點開提示;Podcast/體育/政策都在。"""
    monkeypatch.delenv("EMAIL_OVERFLOW_MODE", raising=False)   # 預設 = keep
    monkeypatch.setattr(mr, "_estimated_email_kb", lambda h: 130.0)   # 一律「超標」
    q = {**_full_quotes(),
         "TW_DAILY_INTELLIGENCE": {
             "policy": [{"title": "POLICYKEEP 政策", "published": "2026-06-15", "link": "#"}]},
         "SPORTS": {"news": {}, "cpbl_scores": [
             {"away": "統一", "home": "味全", "away_score": 5, "home_score": 3,
              "winner": "away", "date": "06/14"}]},
         "PODCAST_DIGEST": [{"show": "股癌", "title": "EPKEEP",
                             "digest": {"summary_points": ["重點一", "重點二"], "tickers": []}}]}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert "已暫略" not in html                       # 不省略
    assert "顯示完整內容" not in html                   # 使用者要求移除「本期內容較長」提示橫幅
    assert "POLICYKEEP" in html and "中華職棒" in html and "EPKEEP" in html  # 全都在
    assert q["PODCAST_SHOWN_EPISODES"] == q["PODCAST_DIGEST"]   # 全集視為已顯示


def test_render_html_user_requested_trims_2026_06():
    """2026-06 使用者批次精簡:市場警告/外資台指期未平倉/中期展望/區間方法/今日立場/
    已自我校正/個股冗長註腳 全部移除;政策·醫界各只留 3 篇;核心(訊號共識/行情/個股預測)保留。"""
    q = {**_full_quotes(),
         "ALERTS": [{"level": "red", "title": "費半急跌", "detail": "SOX 單日跌 -5.71%"}],
         "TAIFEX_OI": {"foreign_oi_net": -69847, "invest_oi_net": 56894,
                       "dealer_oi_net": 2219, "date": "2026/06/16"},
         "TAIEX_PRED": {"last_close": 45809, "pred_open": 45521, "weighted_pct": -0.63,
                        "ci_lower": 44262, "ci_upper": 46781, "consensus": "偏空 (2/3 訊號)",
                        "signals": [], "interval_method": "walk-forward 絕對殘差 90% 分位"},
         "MIDTERM": {"2330": {"trend": "上行",
                              "metrics": {"pct_5d": 4.1, "ma20_dist_pct": 3.6},
                              "forecast": {"5d": {"lower": 2359, "upper": 2546},
                                           "20d": {"lower": 2318, "upper": 2693}}}},
         "TW_DAILY_INTELLIGENCE": {
             "policy": [{"title": f"政策{i}", "published": "2026-06-16", "link": "#",
                         "importance": 5 - i * 0.1} for i in range(5)],
             "medical": [{"title": f"醫界{i}", "published": "2026-06-16", "link": "#",
                          "importance": 6 - i * 0.1} for i in range(5)]}}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-17", "每日報")
    # --- 移除項 ---
    assert "市場警告" not in html and "費半急跌" not in html       # 2. 市場警告
    assert "外資台指期未平倉" not in html                          # 7. 外資台指期未平倉
    assert "中期展望" not in html                                  # 6. 中期展望
    assert "區間方法" not in html and "今日立場：" not in html       # 4. 區間方法/今日立場
    assert "已自我校正" not in html                                # 4. 已自我校正
    assert "非開盤價" not in html and "刻意保守" not in html         # 5. 個股冗長註腳
    # --- 政策/醫界各只留最重要 3 篇 ---
    assert "政策0" in html and "政策2" in html and "政策3" not in html
    assert "醫界0" in html and "醫界2" in html and "醫界3" not in html
    # --- 核心保留 ---
    assert "訊號共識" in html                                      # 訊號共識保留
    assert "一、美股收盤行情" in html and "個股開盤預測" in html


def test_render_html_low_priority_sections_sit_at_bottom():
    """版面順序:體育在前,政策/醫界在最末(Gmail 真要剪先剪低優先,不動體育)。"""
    q = {**_full_quotes(),
         "TW_DAILY_INTELLIGENCE": {"policy": [{"title": "政策X", "published": "2026-06-15", "link": "#"}]},
         "SPORTS": {"news": {}, "cpbl_scores": [
             {"away": "統一", "home": "味全", "away_score": 5, "home_score": 3,
              "winner": "away", "date": "06/14"}]}}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    # 體育(中華職棒)應排在 政策整理 之前
    assert html.index("中華職棒") < html.index("政策整理")


def test_render_html_contains_required_sections():
    html = mr.render_html(_full_quotes(), {"error": "x"}, {"error": "x"},
                          "## 測試分析", "2026-05-14 (Wed)", "每日報")
    assert html.startswith("<!DOCTYPE html>")
    # 2330/00662/0050 已濃縮成「個股開盤預測」一段;資料品質/8-K/回顧已移到後台不顯示
    for section in ("一、美股收盤行情", "個股開盤預測", "2330"):
        assert section in html


def test_render_html_hides_backstage_sections():
    """資料品質 / 8-K / 預測準確度回顧 已移到後台,不應出現在信件。"""
    html = mr.render_html(_full_quotes(), {"error": "x"}, {"error": "x"},
                          "x", "2026-05-14", "每日報")
    assert "資料品質" not in html
    assert "8-K" not in html
    assert "預測準確度回顧" not in html


def test_render_html_survives_full_quotes_dict():
    """回歸測試：quotes 內含 SEC_FILINGS / BACKTEST 等非行情值時不可崩潰。"""
    html = mr.render_html(_full_quotes(), {"error": "資料缺失"},
                          {"error": "資料缺失"}, "內容", "2026-05-14", "每日報")
    assert "資料缺失" in html


def test_data_quality_still_feeds_llm_prompt():
    """資料品質從信件移除,但仍須餵給 LLM prompt(後台保留)。"""
    q = _full_quotes()
    prompt = mr._build_prompt(q, {"error": "x"}, {"error": "x"}, [], [], "")
    assert "夜盤台指期" in prompt   # dq 內容仍在 prompt


# ===== KPI strip + summary bar (頂部美觀區) =====

def test_extract_stance_with_score():
    text = "## 十二、我的明確立場\n淨分 +3\n**立場：中性偏多**（解釋…）"
    s = mr._extract_stance(text)
    assert s["score"] == 3
    assert s["label"] == "中性偏多"


def test_extract_stance_bearish():
    s = mr._extract_stance("淨分 -5\n立場：偏空 / 防守為主")
    assert s["score"] == -5 and s["label"] == "偏空"


def test_extract_stance_uses_explicit_stance_section_only():
    text = (
        "## 市場警告\n立場：偏空\n淨分 -9\n"
        "\n## 十一、我的明確立場\n淨分 +5\n**立場：偏多**\n"
        "\n## 十二、一句話總結\n偏多但控風險"
    )
    s = mr._extract_stance(text)
    assert s["score"] == 5
    assert s["label"] == "偏多"


def test_extract_stance_missing():
    s = mr._extract_stance("沒有立場相關文字")
    assert s == {"label": None, "score": None}


def test_extract_stance_tolerant_to_format_variants():
    """容錯 LLM 格式變異:淨分不同標點(:/為/=)、缺 markdown 標題、立場前後有 ** 皆可解析。"""
    assert mr._extract_stance("我的明確立場\n淨分:+7\n立場：偏多")["score"] == 7
    assert mr._extract_stance("淨分為 -6\n**立場**：偏空")["label"] == "偏空"
    assert mr._extract_stance("我的明確立場\n結果 = 淨分 +4\n立場：偏多")["score"] == 4
    assert mr._extract_stance("立場：中性")["label"] == "中性"
    assert mr._extract_stance("> **立場**：偏多")["label"] == "偏多"   # 引用+粗體行
    # 「我的明確立場」標題不可被誤當立場值(需冒號且錨定行首)
    assert mr._extract_stance("沒有立場相關文字") == {"label": None, "score": None}
    # 標題行帶冒號也不可被誤抓(避免抓到「淨分」當 label)
    assert mr._extract_stance("## 我的明確立場：淨分 +7\n## 一句話總結\n偏多")["label"] is None


def test_analysis_complete_requires_parseable_stance():
    """有段落標題但立場無法解析 → 視為不完整(會觸發重試),避免頂部變「—」。"""
    # 兩段標題都在,但沒有淨分/立場 → 不算完整
    assert not mr._analysis_complete_enough("## 我的明確立場\n(略)\n## 一句話總結\n偏多操作")
    # 補上可解析立場 → 完整
    assert mr._analysis_complete_enough(
        "## 我的明確立場\n淨分 +5\n立場：偏多\n## 一句話總結\n偏多操作")


def test_fallback_stance_from_signals():
    assert mr._fallback_stance_from_signals(
        {"TAIEX_PRED": {"consensus": "偏多 (2/3 訊號)"}})["label"] == "偏多"
    assert mr._fallback_stance_from_signals(
        {"TAIEX_PRED": {"consensus": "全部偏空"}})["label"] == "偏空"
    # 無共識字串 → 用 weighted_pct 正負號
    assert mr._fallback_stance_from_signals(
        {"TAIEX_PRED": {"weighted_pct": 0.1}})["label"] == "偏多"
    assert mr._fallback_stance_from_signals(
        {"TAIEX_PRED": {"weighted_pct": -0.1}})["label"] == "偏空"
    assert mr._fallback_stance_from_signals({"TAIEX_PRED": {}}) == {}


def test_render_html_stance_falls_back_when_llm_incomplete():
    """LLM 分析未含可解析立場時,頂部 KPI 立場用訊號共識保底,不顯示「—」。
    (開盤預測卡的「今日立場」區塊已依使用者要求移除,立場僅留頂部 KPI 條。)"""
    q = {**_full_quotes(), "TAIEX_PRED": {
        "last_close": 45000, "pred_open": 45200, "weighted_pct": 0.44,
        "ci_lower": 44000, "ci_upper": 46000, "consensus": "偏多 (2/3 訊號)",
        "signals": [], "interval_method": "x"}}
    # 分析文沒有「我的明確立場/淨分」→ 立場抽取失敗 → 應退回訊號共識「偏多」
    html = mr.render_html(q, {"error": "x"}, {"error": "x"},
                          "七、昨夜重點\n只有新聞沒有立場段落", "2026-06-16", "每日報")
    assert ">偏多</div>" in html        # 頂部 KPI 立場保底顯示「偏多」(不顯示「—」)
    assert "今日立場：" not in html       # 開盤預測卡的立場區塊已移除


def test_extract_summary_basic():
    text = ("## 十四、一句話總結\n\n"
            "SOX 暴跌 + Fed 升息預期雙殺成長股，減碼 00662 等止穩。\n\n## 其他")
    assert mr._extract_summary(text).startswith("SOX 暴跌")


def test_extract_summary_missing():
    assert mr._extract_summary("沒有總結章節的文字") == ""


def test_sanitize_2330_prices_fixes_adr_confusion():
    """LLM 把 2330 寫成台積電 ADR 美元價(約430)時,sanitizer 用中樞值改回。"""
    preds = {"mid": 2313.24, "last_2330": 2295.0}
    bad = "2330 開盤關鍵價位:守穩 430 元為強,跌破 425 元轉弱\n結論:2330 守穩 430 元逢回加碼"
    out = mr._sanitize_llm_2330_prices(bad, preds)
    assert "430" not in out and "425" not in out
    assert "2313" in out


def test_sanitize_2330_prices_leaves_other_tickers_and_valid_prices():
    preds = {"mid": 2313.24, "last_2330": 2295.0}
    # 不含 2330/台積電 → 完全不動(00662 120、0050 100 保留)
    other = "00662 合理估值 120 元、0050 約 100 元"
    assert mr._sanitize_llm_2330_prices(other, preds) == other
    # 正常的 2330 新台幣價(約 mid)→ 不動
    valid = "2330 守穩 2295 元、站上 2336 元偏強"
    assert mr._sanitize_llm_2330_prices(valid, preds) == valid


def test_sanitize_2330_prices_noop_without_mid():
    assert mr._sanitize_llm_2330_prices("2330 守穩 430 元", {"error": "x"}) == "2330 守穩 430 元"


def test_strip_stance_calculation_hides_score_line_keeps_label():
    """11 維計算行(含淨分+[)隱藏;「立場:中性(淨分 +3…)」結論行保留。"""
    text = ("## 十二、我的明確立場\n\n"
            "```\n"
            "QQQ +3.38% [+1]、SOX +7.91% [+1]、VIX 19.44 [-1] = 淨分 +3\n"
            "```\n\n"
            "立場：中性（淨分 +3，落入 −4∼+4 區間）\n理由:訊號分歧。")
    out = mr._strip_stance_calculation(text)
    assert "[+1]" not in out and "```" not in out
    assert "立場：中性（淨分 +3，落入 −4∼+4 區間）" in out
    assert "理由:訊號分歧。" in out
    # 抽取順序保證:原文先 _extract_stance(用計算行也行),strip 後結論行仍可抽
    stance = mr._extract_stance(out)
    assert stance["label"] == "中性" and stance["score"] == 3


def test_sanitize_2330_prices_keeps_four_digit_thousands_separated():
    """2026-07 回歸:台積電漲破 2000 後,LLM 用千分位寫的四位數 2330 價(2,400)不可被
    誤修成畸形「2,2392」再被 _mask_malformed_numbers 遮成「(數值異常已略)」。"""
    preds = {"mid": 2392.4, "last_2330": 2440.0}
    out = mr._sanitize_llm_2330_prices("2330 守 2,400 元、中樞 2,392 元、昨收 2,440 元", preds)
    assert "2,2392" not in out and "2,2400" not in out        # 不產生畸形
    assert "2,400" in out and "2,392" in out and "2,440" in out  # 合法千分位原樣保留
    assert "(數值異常已略)" not in mr._mask_malformed_numbers(out)  # 下游不遮蔽
    # 真 ADR 誤植(無逗號)仍要修
    assert "2392" in mr._sanitize_llm_2330_prices("2330 守穩 430 元", preds)


def test_sanitize_2330_prices_fixes_malformed_thousands():
    """LLM 寫出「2,2182 元」這種畸形千分位(逗號後非 3 位)時,以中樞值改寫。"""
    preds = {"mid": 2182.26, "last_2330": 2255.0}
    bad = "台積電除息參考價為 2,2182 元"
    out = mr._sanitize_llm_2330_prices(bad, preds)
    assert "2,2182" not in out and "2182 元" in out
    # 合法千分位(逗號後恰 3 位)不誤傷;非 2330 行不動
    ok = "台積電市值 22,182 億元"
    assert mr._sanitize_llm_2330_prices(ok, preds) == ok
    other = "加權指數 4,2328 點"   # 沒提 2330/台積電 → 不動
    assert mr._sanitize_llm_2330_prices(other, preds) == other


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
    # KPI 條:5 個欄位都顯示;批#26 立場只顯示標籤、不顯示淨分數字
    assert "偏空" in html
    _kpi = html.split("一、美股收盤行情")[0]
    assert "-4" not in _kpi                       # 淨分不外露於 KPI
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
    # 0050 在濃縮的「個股開盤預測」段
    assert "個股開盤預測" in html and "0050" in html


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
        "KOSPI": {"close": 3900,  "change_pct": 1.1},
        "VIX_TERM": {"ratio": 1.029, "spread": 0.5, "state": "backwardation"},
    }
    q["BREADTH"] = {
        "total_value_raw": 3.5e11, "total_value_yi": 3500,
        "advance": 700, "decline": 200, "unchanged": 100, "total": 1000,
        "advance_ratio": 70.0, "breadth_state": "broad_rally",
    }
    html = mr.render_html(q, {"error": "x"}, {"error": "x"},
                          "x", "2026-05-21", "每日報")
    # 信件只留「看得懂」的指標:VIX/SOX/DXY/日經/KOSPI/上證/黃金/銅
    for label in ("VIX 恐慌指數", "SOX 費半指數", "DXY 美元指數", "韓國 KOSPI", "黃金"):
        assert label in html, f"missing macro row: {label}"
    # 艱澀指標已從信件移除;WTI/BTC 顯示列 2026-07-16 使用者要求刪除
    # (資料仍在 MACRO dict + LLM prompt 後台保留,照餵 11 維計分)
    for hidden in ("VIX9D", "NQ 期貨", "ES 期貨", "10Y 殖利率", "13W 國庫券",
                   "WTI 原油", "BTC 比特幣"):
        assert hidden not in html, f"should be hidden from email: {hidden}"
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


def test_render_html_shows_attention_candidate_price_forecast(monkeypatch):
    monkeypatch.setattr(mr, "_RENDER_TOP5_CARD", True)   # 卡預設隱藏(2026-07-15),本測試驗保留的渲染碼
    q = _full_quotes()
    q["TW_UNIVERSE_SNAPSHOT"] = [{
        "code": "2330", "name": "台積電", "close": 1000.0, "day_pct": 1.0,
        "attention_score": 72.5, "ranking_score": 72.5, "news_catalyst_score": 2.4,
        "ranking_components": {
            "structure": 60.0, "news_event": 1.9, "industry_neutral": 2.0,
            "beat_market": 4.0, "expected_return": 4.6, "quality_penalty": 0.0,
        },
        "breakout": {"score": 70}, "smart_money": {"score": 60, "tags": ["外資連3買"]},
        "price_forecast": {
            "confidence": "中低",
            "3d": {
                "expected_price": 1010.0, "lower": 970.0, "upper": 1050.0,
                "quality": {"recent_direction_hit_pct": None},
            },
            "5d": {"expected_price": 1020.0, "lower": 960.0, "upper": 1080.0},
        },
    }]
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-02", "每日報")
    assert "台股客觀關注排名 Top 1" in html
    # 使用者回饋:排名分解與模型技術行屬內部細節,不顯示
    assert "客觀排名 #1" not in html
    assert "勝過大盤" not in html
    assert "近期方向命中" not in html
    assert "None%" not in html
    # 批#26:短期參考行(3/5 日)與財報品質已依使用者要求自信件隱藏
    assert "短期參考" not in html
    assert "3日 1010.0" not in html
    assert "財報品質" not in html


def test_render_html_moves_top5_to_bottom_after_taiwan_awareness_sections(monkeypatch):
    monkeypatch.setattr(mr, "_RENDER_TOP5_CARD", True)   # 卡預設隱藏(2026-07-15),本測試驗保留的渲染碼
    q = _full_quotes()
    q["TAIFEX_OI"] = {
        "date": "2026/06/02", "foreign_oi_net": -21000,
        "invest_oi_net": 1000, "dealer_oi_net": -500,
    }
    q["TW_DAILY_INTELLIGENCE"] = {
        "window": "2026-06-02 至 2026-06-02",
        "policy": [{
            "published": "2026-06-02", "topic": "住宅政策",
            "official": True, "source_grade": "A", "status": "confirmed",
            "title": "政策測試標題", "link": "https://example.com/policy",
        }],
        "medical": [{
            "published": "2026-06-02", "topic": "醫療量能",
            "official": False, "source_grade": "B", "status": "confirmed",
            "title": "醫界測試標題", "link": "https://example.com/medical",
        }],
    }
    q["TW_UNIVERSE_SNAPSHOT"] = [{
        "code": "2330", "name": "台積電", "close": 1000.0, "day_pct": 1.0,
        "attention_score": 72.5, "ranking_score": 72.5, "news_catalyst_score": 2.4,
        "ranking_components": {
            "structure": 60.0, "news_event": 1.9, "industry_neutral": 2.0,
            "beat_market": 4.0, "expected_return": 4.6, "quality_penalty": 0.0,
        },
        "breakout": {"score": 70}, "smart_money": {"score": 60, "tags": ["外資連3買"]},
        "price_forecast": {
            "confidence": "中低",
            "3d": {"expected_price": 1010.0, "lower": 970.0, "upper": 1050.0},
            "5d": {"expected_price": 1020.0, "lower": 960.0, "upper": 1080.0},
        },
    }]
    analysis = (
        "## 十一、我的明確立場\n淨分 +5\n**立場：偏多**\n"
        "\n## 十二、今日台股關注五檔\n### 9999 LLM重複段\n- 不應顯示\n"
        "\n## 十三、一句話總結\n偏多但控風險"
    )
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, analysis, "2026-06-03", "每日報")
    assert "LLM重複段" not in html
    # 十二、十三已上移到頂端「今日結論」卡,body 不再有「我的明確立場」標題;
    # 其內容(立場/一句話)應出現在頂端(比 taifex 更早)。
    summary_idx = html.find("今日結論")
    policy_idx = html.find("台灣政策近月走向")
    medical_idx = html.find("台灣醫界昨日走向")
    top5_idx = html.find("台股客觀關注排名 Top 1")
    assert -1 not in (summary_idx, policy_idx, medical_idx, top5_idx)
    assert "外資台指期未平倉" not in html   # 該區塊已依使用者要求隱藏
    # 依使用者犧牲優先序排版(低優先在最末,Gmail 真要剪先剪政策/醫界):
    # 結論在頂端,接著五檔,最後才是醫學文獻、政策、醫界。
    assert summary_idx < top5_idx < policy_idx < medical_idx
    assert "偏多但控風險" in html[:html.find("一、美股收盤行情")]   # 一句話在頂端


def test_render_html_warns_when_watchlist_scores_are_low_confidence(monkeypatch):
    monkeypatch.setattr(mr, "_RENDER_TOP5_CARD", True)   # 卡預設隱藏(2026-07-15),本測試驗保留的渲染碼
    q = _full_quotes()
    q["TW_UNIVERSE_SNAPSHOT"] = [{
        "code": str(2300 + index), "name": f"測試{index}", "close": 100.0,
        "day_pct": 1.0, "ranking_score": 40 + index, "attention_score": 40 + index,
        "news_catalyst_score": 0, "breakout": {"score": 40},
        "smart_money": {"score": 30, "tags": []},
        "price_forecast": {
            "confidence": "低",
            "3d": {"expected_price": 101.0, "lower": 95.0, "upper": 105.0},
            "5d": {"expected_price": 102.0, "lower": 94.0, "upper": 106.0},
        },
    } for index in range(5)]
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-03", "每日報")
    assert "台股波段觀察名單 Top 5" in html         # 低信心→波段框架(標題)
    assert "相對排名" in html                       # 標題仍標相對排名
    assert "不是買進訊號" in html                   # 精簡圖例仍保留風險提示
    assert "中長線(波段)結構觀察" not in html       # 冗長說明已依使用者要求移除
    assert "隔日開" not in html                    # 移除隔日噪音價


def test_render_html_top5_market_state_note_removed_on_big_up_day(monkeypatch):
    monkeypatch.setattr(mr, "_RENDER_TOP5_CARD", True)   # 卡預設隱藏(2026-07-15),本測試驗保留的渲染碼
    """大漲(普漲)但 Top5 仍低分時,卡片仍正常渲染;『為何大漲日也都是觀察』冗長說明已移除。"""
    q = _full_quotes()
    q["BREADTH"] = {"advance_ratio": 67.1, "total": 1000}
    q["TW_UNIVERSE_SNAPSHOT"] = [{
        "code": str(2300 + i), "name": f"測試{i}", "close": 100.0, "day_pct": 5.0,
        "ranking_score": 40 + i, "attention_score": 40 + i, "news_catalyst_score": 0,
        "breakout": {"score": 40}, "smart_money": {"score": 30, "tags": []},
        "price_forecast": {"confidence": "低",
                           "3d": {"expected_price": 105.0, "lower": 95.0, "upper": 115.0},
                           "5d": {"expected_price": 106.0, "lower": 94.0, "upper": 116.0}},
    } for i in range(5)]
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert "台股波段觀察名單 Top 5" in html         # 大漲日仍正常渲染觀察名單
    assert "為何大漲日也都是" not in html           # 冗長說明已依使用者要求移除
    assert "不隨大盤起伏調整" not in html


def test_render_html_00662_labeled_fair_value_not_open():
    """00662 應標為『公允價』(KPI + 表列名);冗長 footnote 說明已依使用者要求移除。"""
    # 標籤不依賴 fair 數值即會渲染,用 error-fair 避開 00662 卡的其他必填欄
    html = mr.render_html(_full_quotes(), {"error": "x"}, {"error": "x"},
                          "x", "2026-06-16", "每日報")
    assert "00662 公允價" in html                       # KPI 標籤
    assert "00662 富邦NASDAQ 公允價" in html             # 六大表列名
    assert "刻意保守" not in html                        # footnote 冗長說明已移除(標籤本身保留)


def test_to_traditional_simplified_to_taiwan():
    """簡體→台灣繁體(opencc s2twp);已是繁體者不變;空字串安全。"""
    assert mr._to_traditional("") == ""
    out = mr._to_traditional("中国终结补贴与价格战")
    assert "中國" in out and "終結" in out and "補貼" in out
    assert "中国" not in out and "终结" not in out      # 確實已轉繁
    assert mr._to_traditional("已是繁體中文") == "已是繁體中文"


def test_render_event_timeline_converts_simplified_to_traditional():
    """延燒中事件:陸媒簡體原標題顯示前須轉成繁體(zh_title 缺也走 latest_title)。"""
    import html as _h
    rows = mr._render_event_timeline_html(
        [{"key": "geopolitical:中国外卖", "days": 6,
          "latest_title": "中国出手终结外卖平台补贴与价格战"}], _h)
    assert "延燒中事件" in rows
    assert "中國" in rows and "終結" in rows and "補貼" in rows
    assert "中国" not in rows and "终结" not in rows      # 簡體不該外漏


def test_render_ma200_status_card():
    """長線趨勢(MA200)卡:站上紅/跌破綠 + 距離%,無資料回空。"""
    assert mr._render_ma200_html({}) == ""
    h = mr._render_ma200_html({
        "0050.TW": {"name": "0050 元大台灣50", "close": 200.0, "ma200": 180.0,
                    "above": True, "dist_pct": 11.1, "leveraged": False},
        "00631L.TW": {"name": "00631L 台灣50正2", "close": 90.0, "ma200": 80.0,
                      "above": True, "dist_pct": 12.5, "leveraged": True},
        "2330.TW": {"name": "2330 台積電", "close": 900.0, "ma200": 950.0,
                    "above": False, "dist_pct": -5.3, "leveraged": False}})
    assert "長線趨勢參考(200 日均線)" in h
    assert "站上(波段偏多)" in h and "跌破(波段轉弱)" in h
    assert "非買賣訊號" not in h     # 註腳已依使用者要求移除(2026-07-14)
    # 槓桿標的(00631L)應帶「槓桿」標記
    assert "槓桿" in h
    assert "抗回撤" not in h        # 定位說明註腳一併移除(2026-07-14)


def test_render_html_includes_ma200_when_present():
    q = {**_full_quotes(), "MA200_STATUS": {
        "0050.TW": {"name": "0050 元大台灣50", "close": 200.0, "ma200": 180.0,
                    "above": True, "dist_pct": 11.1}}}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert "長線趨勢參考(200 日均線)" in html


def test_build_prompt_biotech_and_transmission_rules():
    """prompt 應要求生技/醫療專門著墨,且立場理由要寫傳導機制。"""
    p = mr._build_prompt(_full_quotes(), {"error": "x"}, {"error": "x"}, [], [], "")
    assert "生技/醫療(本報讀者為醫師" in p
    assert "傳導機制" in p and "成長股估值折扣" in p


# ===== 模型實證 walk-forward 區塊 =====

def test_model_evidence_green_verdict():
    q = {"MODEL_WALK_FORWARD": {
            "3d": {"direction_hit_pct": 55.0, "top5_avg_net_return_pct": 0.9,
                   "top5_avg_excess_pct": 0.6, "interval_coverage_pct": 82, "samples": 140},
            "5d": {"direction_hit_pct": 53.0, "top5_avg_net_return_pct": 0.4,
                   "top5_avg_excess_pct": 0.3, "interval_coverage_pct": 80, "samples": 130}},
         "MODEL_MONITORING": {"status": "ok", "alerts": []}}
    h = mr._render_model_evidence_html(q)
    # 使用者要求(2026-07-14):「模型狀態」白話結論也不顯示 → 整卡收掉。
    # 指標仍在後台計算並驅動熔斷/品質警示,僅顯示層歸零。
    assert h == ""


def test_model_evidence_accumulating_verdict():
    q = {"MODEL_WALK_FORWARD": {"3d": {"direction_hit_pct": None, "samples": 0}},
         "MODEL_MONITORING": {"status": "fallback", "alerts": ["calibration samples < 30"]}}
    assert mr._render_model_evidence_html(q) == ""   # 各判決狀態一律不顯示


def test_model_evidence_weak_verdict():
    q = {"MODEL_WALK_FORWARD": {
            "3d": {"direction_hit_pct": 47.0, "top5_avg_net_return_pct": -0.5,
                   "interval_coverage_pct": 70, "samples": 120}},
         "MODEL_MONITORING": {"status": "fallback", "alerts": []}}
    assert mr._render_model_evidence_html(q) == ""   # 黃燈判決也不顯示


def test_new_high_signal_features_registered():
    """新特徵須進 MODEL_FEATURES 與 model snapshot 白名單,否則 ML 學不到。"""
    assert "rel_strength_5d" in mr.MODEL_FEATURES
    assert "inst_buy_vol_ratio" in mr.MODEL_FEATURES
    snap = mr._snapshot_for_model([{
        "code": "2330", "close": 1000.0, "rel_strength_5d": 2.5,
        "inst_buy_vol_ratio": 18.0, "pct_5d": 5.0}])
    assert snap["2330"].get("rel_strength_5d") == 2.5
    assert snap["2330"].get("inst_buy_vol_ratio") == 18.0


def test_cap_analysis_removes_orphan_header():
    """截斷點恰落在節標題後 → 孤兒標題(「## 十、…」+截斷訊息空殼)一併移除。

    2026-07-13 信實見:「十、總體經濟」只剩標題與截斷訊息。
    """
    body = "A" * 5900 + "\n\n## 十、總體經濟與政策環境\n\n" + "B" * 400
    capped = mr._cap_analysis_text(body)
    assert "已截斷" not in capped                  # 截斷註解文字已依使用者要求移除(2026-07-14)
    assert "十、總體經濟" not in capped            # 孤兒標題被清掉
    assert "B" not in capped
    # 短文原樣通過;上限 6000(2026-07-14 起僅防 LLM 跑飛,不再為信件大小服務)
    assert mr._cap_analysis_text("short") == "short"
    ok = "C" * 5900
    assert mr._cap_analysis_text(ok) == ok


def test_render_html_full_mode_default_never_compacts(monkeypatch):
    """預設 full 模式(使用者 2026-07-14 拍板:內容完整優先、接受 Gmail 摺疊):
    即使估算大小遠超 102KB,也不壓條數、不減集、不移除區塊,全部集數照標記已顯示。
    07-13/14 教訓:keep 模式把 10 集擠到剩 1 集,比摺疊更傷。"""
    monkeypatch.delenv("EMAIL_OVERFLOW_MODE", raising=False)   # 預設=full
    pts = [f"PTMARK{j} 重點" for j in range(15)]
    eps = [{"show": f"節目{i}", "title": f"EPMARK{i}",
            "digest": {"summary_points": list(pts), "tickers": []}} for i in range(10)]
    monkeypatch.setattr(mr, "_estimated_email_kb", lambda h: 150.0)   # 永遠「超標」
    q = {**_full_quotes(), "PODCAST_DIGEST": eps}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-06-16", "每日報")
    assert html.count("EPMARK") == 10                  # 10 集全在
    assert html.count("PTMARK") == 150                 # 條數完全沒壓(10 集 × 15 條)
    assert len(q["PODCAST_SHOWN_EPISODES"]) == 10      # 全部標記已顯示
    assert "已暫略" not in html                         # 無任何區塊被移除


def test_top5_card_rendered_above_podcast():
    """Top5 卡已加回(使用者 2026-07-18),位置=Podcast 卡上方。"""
    quotes = {**_full_quotes(), "TW_UNIVERSE_SNAPSHOT": [{
        "code": "2330", "name": "台積電", "close": 2420.0, "day_pct": 0.0,
        "ranking_score": 40.0, "smart_money": {"score": 55, "tags": []},
    }], "PODCAST_DIGEST": [{"show": "股癌", "title": "x", "points": ["a"]}]}
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, "x", "2026-07-18", "每日報")
    assert "台積電" in html
    i_top5 = html.find("客觀關注排名")
    if i_top5 < 0:
        i_top5 = html.find("波段觀察")
    i_pod = html.find("Podcast 重點")
    assert i_top5 >= 0, "Top5 卡必須渲染"
    assert i_pod < 0 or i_top5 < i_pod, "Top5 卡必須在 Podcast 卡上方"


def test_archive_fail_closed_sensitive_scan(monkeypatch, tmp_path):
    """地基批#6(GPT-5.6 P1):去識別後仍含敏感內容 → 拒絕存檔(fail-closed),
    乾淨內容照常存;拒存不影響寄信流程(回 None 而非拋錯)。"""
    monkeypatch.setattr(mr, "EMAIL_ARCHIVE_DIR", tmp_path)
    # 乾淨內容 → 存檔成功
    out = mr.archive_report_html("<html>正常晨報內容</html>", "2026-07-16")
    assert out is not None and out.exists()
    # 金鑰樣式(redaction 不會處理)→ 拒存;含 sk-proj- 專案金鑰(Codex review)
    out2 = mr.archive_report_html(
        "<html>leak sk-" + "a" * 24 + "</html>", "2026-07-17")
    assert out2 is None
    assert mr.archive_report_html(
        "<html>leak sk-proj-" + "Ab1x" * 8 + "</html>", "2026-07-18") is None
    assert not (tmp_path / "2026-07-17.html.gz").exists()
    # 掃描器逐類別:持股標記殘留/私人信箱/金鑰
    assert mr._archive_sensitive_hits("<!--PF_ROW_START-->x") == ["pf_row_marker"]
    monkeypatch.setattr(mr, "GMAIL_USER", "me@example.com")
    monkeypatch.setattr(mr, "RECIPIENTS", ["a@example.com"])
    assert "private_email" in mr._archive_sensitive_hits("mail me@example.com")
    assert "private_email" in mr._archive_sensitive_hits("to a@example.com")
    monkeypatch.setattr(mr, "PORTFOLIO_1_NAME", "老王退休金")
    assert "portfolio_name" in mr._archive_sensitive_hits("老王退休金 +2%")
    assert mr._archive_sensitive_hits("乾淨") == []


# ===== 地基批#5(2026-07-16):預測 delta / 熱度排名 delta / 健康警示行 =====

def test_prediction_delta_note_vs_yesterday():
    history = [
        {"date": "2026-07-14", "weighted_final_2330": 2400.0, "pred_taiex": 44000.0,
         "fair_00662": 120.0, "pred_0050": 104.0},
        {"date": "2026-07-15", "weighted_final_2330": 2440.0, "pred_taiex": 44500.0,
         "fair_00662": 121.0, "pred_0050": 104.5},
        {"date": "2026-07-16", "weighted_final_2330": 9999.0},   # 今日自己,不可當基準
    ]
    note = mr._prediction_delta_note(history, "2026-07-16 (Thu)", {
        "2330": 2452.2, "加權": 44700.0, "00662": 120.5, "0050": 104.5})
    assert "vs 昨日預測" in note and "基準 2026-07-15" in note
    assert "2330 +0.50%" in note and "加權 +0.45%" in note
    assert "00662 -0.41%" in note and "0050 +0.00%" in note
    # 全部 |Δ|<0.05% → 無變化自動抑制
    assert mr._prediction_delta_note(history, "2026-07-16", {
        "2330": 2440.5, "加權": 44510.0, "00662": 121.02, "0050": 104.51}) == ""
    # 無前日紀錄 → 空
    assert mr._prediction_delta_note([], "2026-07-16", {"2330": 2452.0}) == ""


def test_sector_rank_deltas_day_over_day(monkeypatch, tmp_path):
    import datetime as dt
    monkeypatch.setattr(mr, "SECTOR_RANK_FILE", tmp_path / "rank.json")
    d1 = dt.datetime(2026, 7, 16, 6, 0, tzinfo=mr.TPE)
    d2 = dt.datetime(2026, 7, 17, 6, 0, tzinfo=mr.TPE)
    assert mr._sector_rank_deltas(["半導體", "金融", "航運"], d1) == {}   # 首日無基準
    deltas = mr._sector_rank_deltas(["金融", "半導體", "光電"], d2)
    assert deltas["金融"] == {"d": 1, "days": 1}      # 2→1 名(上升1)
    assert deltas["半導體"] == {"d": -1, "days": 1}   # 1→2 名(下降1)
    assert deltas["光電"]["d"] is None                # 昨日不在榜 → 新進
    # 同日重跑:prev 不動,delta 穩定
    deltas2 = mr._sector_rank_deltas(["金融", "半導體"], d2)
    assert deltas2["金融"] == {"d": 1, "days": 1}
    assert mr._sector_rank_deltas([], d2) == {}   # 空輸入不炸不寫
    # 跨多日(空榜日/未推回)→ 揭露實際間隔(Codex review:不偽裝成前一日)
    d5 = dt.datetime(2026, 7, 20, 6, 0, tzinfo=mr.TPE)
    deltas3 = mr._sector_rank_deltas(["半導體", "金融"], d5)
    assert deltas3["半導體"] == {"d": 1, "days": 3}   # 基準=07/17 快照


def test_render_html_health_warning_line_and_heat_rank_arrows():
    q = {**_full_quotes(),
         "HEALTH_WARNINGS": ["模型歷史 143→130 日縮短", "來源連續失敗:TWSE"],
         "BREADTH": {"total_value_raw": 3.5e11, "total_value_yi": 3500,
                     "advance": 700, "decline": 200, "unchanged": 100, "total": 1000,
                     "advance_ratio": 70.0, "breadth_state": "broad_rally"},
         "SECTOR_HEAT": {"ranked": ["半導體", "金融"], "total_value_yi": 3500,
                         "sectors": {
                             "半導體": {"n": 50, "up": 30, "down": 15, "median_pct": 1.2,
                                     "value_yi": 1500, "value_share_pct": 42.0,
                                     "leaders": [{"code": "2330", "name": "台積電",
                                                  "pct": 2.0, "value_yi": 900.0}]},
                             "金融": {"n": 30, "up": 10, "down": 15, "median_pct": -0.5,
                                    "value_yi": 500, "value_share_pct": 14.0,
                                    "leaders": []}}},
         "SECTOR_RANK_DELTA": {"半導體": {"d": 2, "days": 1},
                               "金融": {"d": None, "days": 3}}}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-07-16", "每日報")
    assert "⚙ 系統健康:" in html and "模型歷史 143→130 日縮短" in html
    assert "(↑2)" in html                      # 半導體排名上升
    assert "(新進/3日)" in html                # 金融新進榜(基準非昨日 → 標間隔)
    # 無警示 → 行缺席
    q2 = {**_full_quotes(), "HEALTH_WARNINGS": []}
    assert "⚙ 系統健康" not in mr.render_html(
        q2, {"error": "x"}, {"error": "x"}, "x", "2026-07-16", "每日報")


def test_health_line_zwsp_breaks_gmail_autolink():
    """信件修正(2026-07-17):健康行的網域插入零寬空白,Gmail 不再自動連結化。"""
    q = {**_full_quotes(), "HEALTH_WARNINGS": ["來源連續失敗:news.cnyes.com(11天)"]}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-07-17", "每日報")
    assert "news.​cnyes.​com" in html          # 每個點後有 ZWSP
    assert "news.cnyes.com" not in html                  # 原始連續網域不再出現


def test_batch26_hidden_display_elements():
    """批#26:銅期貨/預測記分卡/立場歸因卡自信件移除。"""
    q = _full_quotes()
    q["MACRO"] = {**(q.get("MACRO") or {}),
                  "COPPER": {"close": 6.26, "change_pct": 0.59}}
    q["FORECAST_LEDGER"] = {"today": [{"label": "2330 開盤高於昨收",
                                       "prob": 0.12, "base_rate": 0.5,
                                       "target": "2026-06-03"}], "stats": {}}
    q["STANCE_ATTRIB"] = {"prev_date": "2026-06-01", "prev_total": -6,
                          "curr_total": -8, "changes": [("qqq", 0, -1)]}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x",
                          "2026-06-02", "每日報")
    assert "銅期貨" not in html
    assert "預測記分卡" not in html
    assert "立場變化歸因" not in html


def test_batch26_summary_strips_net_score():
    """Codex 批#26 r1:一句話總結若含「淨分 -6」,頂部結論卡不得殘留。"""
    q = _full_quotes()
    analysis = ("## 十二、我的明確立場\n> **立場：偏空**\n"
                "## 十三、一句話總結\n偏空(淨分 -6),減碼 00662 待戰事明朗")
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, analysis,
                          "2026-06-02", "每日報")
    assert "淨分" not in html
    assert "偏空" in html                     # 立場標籤必須保留(Codex r2)
    assert "減碼 00662 待戰事明朗" in html    # 結論主體保留
    # 無分隔情況:「偏空（淨分 -6）減碼」→ 立場+動作都在
    from llm_postprocess import _strip_score_phrases
    assert _strip_score_phrases("偏空（淨分 -6）減碼 00662") == "偏空減碼 00662"
    assert _strip_score_phrases("偏多操作 00662") == "偏多操作 00662"  # 無片語不動
    # Codex r4:括號內含動作建議時,只挖淨分、保留動作與括號
    assert _strip_score_phrases("偏空（淨分 -6，建議減碼 00662）") == "偏空（建議減碼 00662）"
    # Codex r5:逗號分隔的獨立「距…門檻…」子句也移除,動作仍保留
    assert (_strip_score_phrases("偏空（淨分 -6，距偏空門檻 2 分，建議減碼 00662）")
            == "偏空（建議減碼 00662）")
    # Codex r6:門檻片語與動作間無標點時也不得吞掉動作
    assert (_strip_score_phrases("偏空（淨分 -6，距偏空門檻 2 分建議減碼 00662）")
            == "偏空（建議減碼 00662）")
    # Codex r6(二):較長維中片語(>8 字)不得截成碎片
    assert (_strip_score_phrases("偏空（11 維中共有 7 項指標偏空，建議減碼 00662）")
            == "偏空（建議減碼 00662）")


def test_batch26_stance_internals_scoped_to_stance_section():
    """Codex 批#26 r4:非立場段的正當「距突破門檻 2%」子句不得被計分過濾誤刪
    (_strip_stance_internals 只套立場詳情段,不套整份 analysis)。"""
    q = _full_quotes()
    analysis = ("## 八、科技板塊脈動\n台積電距突破門檻 2%，量能回升→2330 有撐。\n"
                "## 十二、我的明確立場\n> **立場：偏空**\n> 理由：11 維中 7 項偏空。\n"
                "## 十三、一句話總結\n偏空觀望")
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, analysis,
                          "2026-06-02", "每日報")
    assert "距突破門檻 2%" in html and "量能回升" in html   # 八段正當子句保留
    assert "11 維中" not in html                            # 立場段計分內部仍被移除


def test_batch28_sanitize_debate_section_scoped():
    """批#28(Codex r1):多空交鋒段的計分內部安全網——只過濾該段,八段門檻語言保留。"""
    from llm_postprocess import _sanitize_debate_section
    t = ("## 七之五、多空交鋒\n"
         "- **多方最強**：SOX 反彈，淨分 +6，11 維中 7 項偏多\n"
         "- **空方最強**：油價飆升壓成長股，偏空觀望\n"
         "## 八、科技板塊脈動\n台積電距突破門檻 2%，量能回升→2330 有撐。")
    out = _sanitize_debate_section(t)
    debate = out.split("## 八")[0]
    assert "淨分 +6" not in debate and "11 維" not in debate   # 計分內部移除
    assert "SOX 反彈" in debate and "油價飆升" in debate and "偏空觀望" in debate
    assert "距突破門檻 2%" in out and "量能回升" in out         # 八段不受影響
    # 無多空交鋒段 → 原樣返回
    assert _sanitize_debate_section("## 八、只有科技段\n距突破門檻 2%") == \
        "## 八、只有科技段\n距突破門檻 2%"


def test_batch28_debate_strips_bare_11_dim_but_not_maintain():
    """批#28 r2(Codex):辯論段獨立「11 維(模型/度)」也移除(基本組只認「維中」);
    但「11 維持」(如 VIX 11 維持低檔)為正當論點,不得誤刪。"""
    from llm_postprocess import _sanitize_debate_section, _strip_stance_internals
    t = ("## 七之五、多空交鋒\n"
         "- **多方最強**：11 維模型顯示多方佔優，SOX +2.1%\n"
         "- **空方最強**：VIX 11 維持低檔但油價升，偏空\n## 八、科技\n量能回升")
    d = _sanitize_debate_section(t).split("## 八")[0]
    assert "11 維模型" not in d and "SOX +2.1%" in d      # 計分維度移除、市場數據保留
    assert "維持低檔" in d                                 # 「11 維持」不誤刪
    # 其他段(不傳 extra_bad)行為不變:基本組不刪獨立「11 維」
    assert "11 維模型" in _strip_stance_internals("理由：11 維模型顯示多方")


def test_batch28_render_strips_noncompliant_debate_internals():
    """批#28(Codex r1):LLM 若在多空交鋒段違規寫計分內部,render 後 HTML 不得
    出現「淨分/11 維」,但多空論點本體保留。"""
    q = _full_quotes()
    analysis = ("## 七之五、多空交鋒\n"
                "- **多方最強**：TSM ADR +0.99%，淨分 +6 撐盤\n"
                "- **空方最強**：10Y 升至 4.6%，11 維中 7 項偏空壓估值\n"
                "## 十二、我的明確立場\n> **立場：中性**\n"
                "## 十三、一句話總結\n中性觀望")
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, analysis,
                          "2026-06-02", "每日報")
    assert "淨分" not in html and "11 維" not in html          # 計分內部不外露
    assert "多方最強" in html and "空方最強" in html            # 論點結構保留
    assert "TSM ADR" in html and "10Y 升至 4.6%" in html        # 論點本體保留


def test_batch26_stance_label_line_keeps_label(monkeypatch):
    """Codex 批#26 r8:立場標籤行帶淨分「**立場：偏空**（淨分 -6）」時,
    整段刪除會丟掉「偏空」並留畸形「**立場：」——改外科式,標籤保留。"""
    from llm_postprocess import _strip_stance_internals
    assert _strip_stance_internals("> **立場：偏空**（淨分 -6）") == "> **立場：偏空**"
    assert _strip_stance_internals("立場：中性（淨分 +3，觀望）") == "立場：中性（觀望）"
    # 理由行仍整段刪(計分子句去、傳導鏈留;clause 策略會 strip 尾標點)
    assert (_strip_stance_internals("> 理由：11 維中 7 項偏空。核心：SOX 壓制。")
            == "> 理由：核心：SOX 壓制")
