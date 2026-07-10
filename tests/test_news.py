"""dedup_news 去重測試。"""
import morning_report as mr


def test_dedup_exact_duplicate():
    news = [
        {"source": "A", "title": "台積電法說會釋出樂觀展望"},
        {"source": "B", "title": "台積電法說會釋出樂觀展望"},
        {"source": "C", "title": "聯發科天璣晶片出貨創高"},
    ]
    out = mr.dedup_news(news)
    assert len(out) == 2
    assert out[0]["source"] == "A"   # 保留先出現者


def test_dedup_near_duplicate():
    news = [
        {"source": "A", "title": "Fed officials signal possible rate cut in September"},
        {"source": "B", "title": "Fed officials signal possible rate cut in September."},
        {"source": "C", "title": "完全不相關的另一則新聞標題內容"},
    ]
    out = mr.dedup_news(news)
    assert len(out) == 2


def test_dedup_prefers_trusted_richer_source():
    news = [
        {"source": "Google:2330", "title": "台積電上修展望", "summary": "短摘要"},
        {"source": "中央社財經", "title": "台積電上修展望", "summary": "較完整的官方說明與具體數字"},
    ]
    out = mr.dedup_news(news)
    assert len(out) == 1
    assert out[0]["source"] == "中央社財經"


def test_dedup_keeps_distinct():
    news = [
        {"source": "A", "title": "台積電營收成長"},
        {"source": "B", "title": "鴻海擴大電動車布局"},
        {"source": "C", "title": "輝達發表新一代 GPU"},
    ]
    assert len(mr.dedup_news(news)) == 3


def test_dedup_empty_titles_kept():
    news = [{"source": "A", "title": ""}, {"source": "B", "title": ""}]
    # 空標題不做相似度比對，全部保留（避免誤殺）
    assert len(mr.dedup_news(news)) == 2


def test_mops_announcements_empty_when_no_codes():
    assert mr.fetch_tw_major_announcements([]) == []


def test_mops_announcements_skips_failures(monkeypatch):
    """個別公司 RSS 失敗時整體不可崩，回空清單。"""
    def boom(url, **kw):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    out = mr.fetch_tw_major_announcements(["2330", "2317"])
    assert out == []


def test_calibration_note_compact_hides_early_state():
    """『樣本累積中』屬於預期狀態，compact 版應回空字串避免每天噪音。"""
    obj = {"calibration": {"applied": False, "reason": "歷史樣本不足（< 2 天）"}}
    assert mr._calibration_note_compact(obj) == ""
    # 已套用 → 應正常顯示
    obj2 = {"calibration": {"applied": True, "bias_pct": 0.5, "samples": 10, "raw": 100.0}}
    assert "已自我校正" in mr._calibration_note_compact(obj2)


def test_gnews_rss_builds_encoded_url():
    url = mr._gnews_rss("台積電 輝達", when="2d")
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "hl=zh-TW" in url and "ceid=TW:zh-Hant" in url
    assert "when%3A2d" in url            # when:2d URL-encoded
    assert "%E5%8F%B0%E7%A9%8D%E9%9B%BB" in url   # 台積電 已 URL 編碼


def test_news_grade_uses_publisher_not_aggregator():
    """Google/類股 feed 的 source 是代號,真正媒體在 source_name/標題 → 應升級為 B,不是 C。"""
    assert mr._news_source_grade({"source": "Google:NVDA", "source_name": "中央社"}) == "B"
    assert mr._news_source_grade({"source": "類股-金融-台股",
                                  "title": "壽險業大賺 - 經濟日報"}) == "B"
    assert mr._news_source_grade({"source": "Google:2330"}) == "C"  # 無媒體線索仍 C


def test_tech_gate_drops_analyst_and_chipflow_noise():
    """純喊價/純籌碼流向且無具體催化 → 視為科技脈動雜訊。"""
    assert mr._is_low_value_tech_headline(
        {"source_name": "鉅亨", "title": "外資調升評等,目標價上看 1500 元", "summary": ""})
    assert mr._is_low_value_tech_headline(
        {"source_name": "工商時報", "title": "台積電獲三大法人買超 2 萬張", "summary": ""})


def test_tech_gate_keeps_concrete_catalyst_and_official():
    """有具體催化(法說/訂單/出口管制)或 A 級官方來源 → 一律保留。"""
    # 喊價措辭但夾帶具體催化(法說上修)→ 保留
    assert not mr._is_low_value_tech_headline(
        {"source_name": "經濟日報", "title": "法說調升財測,外資目標價上看 1500",
         "summary": "資本支出擴產"})
    # A 級官方來源即使是籌碼字眼也保留
    assert not mr._is_low_value_tech_headline(
        {"source": "TWSE", "title": "三大法人買賣超日報", "summary": ""})
    # 一般中性標題(無喊價、無籌碼字眼)→ 不該被當雜訊砍
    assert not mr._is_low_value_tech_headline(
        {"source_name": "中央社", "title": "輝達執行長訪台與供應鏈會談", "summary": ""})


def test_tech_gate_vague_growth_words_are_not_catalysts():
    """泛詞(成長/獲利/增加)不算具體催化 → 純喊價/純籌碼仍被當雜訊砍。"""
    assert mr._is_low_value_tech_headline(
        {"source_name": "鉅亨", "title": "外資調升目標價,預估獲利成長", "summary": ""})
    assert mr._is_low_value_tech_headline(
        {"source_name": "工商時報", "title": "外資買超增加,投信同步加碼", "summary": ""})
    # 裸「上修」不可放行喊價:「外資上修目標價」仍是雜訊(真正財測上修由「財測」涵蓋)
    assert mr._is_low_value_tech_headline(
        {"source_name": "鉅亨", "title": "外資上修目標價至 1600 元", "summary": ""})
    assert not mr._is_low_value_tech_headline(
        {"source_name": "經濟日報", "title": "公司法說上修財測,目標價同步調升", "summary": ""})


def test_tech_gate_short_english_terms_respect_word_boundary():
    """短英文催化詞(ban/miss/beat)走 _matches_any 的詞界比對,不可子字串誤中
    (bank/bandwidth/mission)→ 純喊價的英文標題仍被當雜訊砍。"""
    assert mr._matches_any("Bank of America bandwidth note", mr.TECH_GATE_CATALYST) is None
    assert mr._matches_any("US ban on chip exports", mr.TECH_GATE_CATALYST) == "ban"
    # 「Bank…reiterates price target」是純喊價(且 ban 不誤中 Bank)→ 應被砍
    assert mr._is_low_value_tech_headline(
        {"source_name": "Reuters", "title": "Bank of America reiterates buy rating, price target",
         "summary": ""})


def test_tech_gate_does_not_drop_real_subscription_action():
    """『認購私募/增資』是實質公司動作,不可當權證籌碼雜訊砍掉。"""
    assert not mr._is_low_value_tech_headline(
        {"source_name": "經濟日報", "title": "鴻海認購某新創私募增資案取得董事席次",
         "summary": ""})
    # 但「認購權證」仍屬籌碼雜訊
    assert mr._is_low_value_tech_headline(
        {"source_name": "鉅亨", "title": "台積電認購權證掛牌交易熱絡", "summary": ""})


def test_dedup_preserves_company_label():
    """個股新聞被去重時,company_label 必須保留在留下來的那筆(否則股票從科技板塊消失)。"""
    news = [
        {"source": "中央社財經", "title": "台積電法說釋樂觀展望", "summary": "完整官方說明與數字"},
        {"source": "Google:2330", "source_name": "鉅亨", "company_label": "2330",
         "title": "台積電法說釋樂觀展望", "summary": "短"},
    ]
    out = mr.dedup_news(news)
    assert len(out) == 1
    assert out[0].get("company_label") == "2330"  # 標籤被保留


def test_other_sector_feeds_registered():
    """『九、其他類股資訊』取材的非科技類股來源,須以「類股-」前綴併入 RSS_FEEDS。"""
    expected = {
        "金融-台股", "金融-全球", "航運-台股", "航運-全球",
        "生技-台股", "生技-全球", "汽車-台股", "汽車-全球",
    }
    assert expected.issubset(set(mr.OTHER_SECTOR_QUERIES))
    for label in mr.OTHER_SECTOR_QUERIES:
        key = f"類股-{label}"
        assert key in mr.RSS_FEEDS, f"{key} 未併入 RSS_FEEDS"
        assert mr.RSS_FEEDS[key].startswith("https://news.google.com/rss/search?q=")


def test_fetch_news_includes_company_queries(monkeypatch):
    """fetch_news 應對 GOOGLE_NEWS_COMPANIES 每家查詢,產出帶 company_label 的項目。"""
    import time as _t

    class _FakeEntry(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    class _FakeFeed:
        def __init__(self, url):
            # 公司查詢 URL 含 news.google.com/rss/search
            self.entries = [{
                "title": "輝達GB300出貨超預期 台積電CoWoS滿載",
                "summary": "具體內容：訂單能見度到2027",
                "link": "https://news.google.com/rss/articles/ABC123",
                "published": "Mon, 01 Jun 2026 01:00:00 GMT",
                "published_parsed": _t.gmtime(),   # 現在 → 不會被 cutoff 濾掉
            }]

    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, **kwargs: _FakeFeed(url))
    # 避免真的打 cnyes JSON / 其他 requests
    monkeypatch.setattr(mr.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
    items = mr.fetch_news()
    company_items = [n for n in items if n.get("company_label")]
    assert company_items, "應有 company_label 的個股新聞"
    # 至少涵蓋我們查詢清單裡的標籤
    labels = {n["company_label"] for n in company_items}
    assert labels & {lbl for _, lbl in mr.GOOGLE_NEWS_COMPANIES}


def test_fetch_news_skips_undated_other_sector_items(monkeypatch):
    monkeypatch.setattr(mr, "RSS_FEEDS", {
        f"類股-{next(iter(mr.OTHER_SECTOR_QUERIES))}": "https://news.google.com/rss/search?q=x"
    })
    monkeypatch.setattr(mr, "GOOGLE_NEWS_COMPANIES", [])

    class _Feed:
        entries = [{
            "title": "sector headline without date",
            "summary": "",
            "link": "https://example.com/sector",
        }]

    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, **kwargs: _Feed())
    assert mr.fetch_news() == []


def test_classify_geopolitical_critical():
    # 川習會 / 台海 / 晶片出口管制 → critical（會抓全文 + prompt 強制分析）
    news = [
        {"title": "川習會落幕 習近平稱台灣問題處理不當恐致衝突", "summary": ""},
        {"title": "美國對中國祭出新一輪晶片出口管制措施", "summary": ""},
        {"title": "中國公布稀土出口配額調整", "summary": ""},
        {"title": "某公司推出新款掃地機器人", "summary": ""},
    ]
    out = mr.classify_news_importance(news)
    assert out[0]["importance"] == "critical" and out[0]["category"] == "geo_critical"
    assert out[1]["importance"] == "critical" and out[1]["category"] == "geo_critical"
    assert out[2]["importance"] == "high" and out[2]["category"] == "geo"   # 稀土屬一般地緣
    assert out[3]["importance"] == "normal"


def test_classify_war_keyword_requires_word_boundary():
    """英文 war 不可誤中 Warren / software / hardware。"""
    news = [
        {"title": "Warren Buffett disclosed a tiny purchase", "summary": ""},
        {"title": "Software maker cuts workforce", "summary": ""},
        {"title": "Hardware demand rebounds for AI servers", "summary": ""},
        {"title": "Iran war risk pushes oil higher", "summary": ""},
    ]
    out = mr.classify_news_importance(news)
    assert [n["importance"] for n in out[:3]] == ["normal", "normal", "normal"]
    assert out[3]["importance"] == "critical"
    assert out[3]["category"] == "geo_critical"


def test_fetch_news_fulltext_resolves_google_news_target(monkeypatch):
    requested = []

    class Resp:
        status_code = 200
        text = "<html>" + ("important full text " * 20) + "</html>"

    def fake_get(url, **kwargs):
        requested.append(url)
        return Resp()

    monkeypatch.setattr(mr.requests, "get", fake_get)
    news = [{
        "importance": "critical",
        "link": "https://news.google.com/rss/articles/abc?url=https%3A%2F%2Fexample.com%2Farticle",
    }]
    out = mr.fetch_news_fulltext(news, max_critical=1, max_high=0)
    assert requested == ["https://example.com/article"]
    assert "important full text" in out[0]["fulltext"]


# ---------- fetch_candidate_company_news（候選股動態新聞）----------

def test_fetch_candidate_company_news(monkeypatch):
    import time as _t

    class _Feed:
        def __init__(self, url):
            self.entries = [{
                "title": "緯創 GB300 出貨超預期", "summary": "訂單能見度到 2027",
                "link": "https://news.google.com/rss/articles/X",
                "published": "Mon, 01 Jun 2026 01:00:00 GMT",
                "published_parsed": _t.gmtime(),
            }]
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, **kwargs: _Feed(url))
    snap = [
        {"code": "3231", "name": "緯創", "breakout": {"score": 80}},
        {"code": "2330", "name": "台積電", "breakout": {"score": 74}},   # exclude
        {"code": "6770", "name": "力積電", "breakout": {"score": 50}},
        {"code": "9999", "name": "低分股", "breakout": {"score": 0}},     # 0 分跳過
    ]
    out = mr.fetch_candidate_company_news(snap, top_n=20, exclude_codes={"2330"})
    labels = {n["company_label"] for n in out}
    assert labels == {"3231", "6770"}                  # 排除 2330、跳過 0 分
    assert all(n.get("company_label") and n.get("code") for n in out)
    assert all(n["source"].startswith("Google:") for n in out)


def test_fetch_candidate_company_news_empty():
    assert mr.fetch_candidate_company_news([]) == []


# ---------- 官方情報源:良性 bozo(編碼/content-type 警告)應採用 entries ----------

class _FakeFeed:
    def __init__(self, entries, bozo=False, exc_name=None):
        self.entries = entries
        self.bozo = bozo
        self.bozo_exception = type(exc_name, (Exception,), {})() if exc_name else None


def test_feed_usable_benign_bozo_with_entries():
    # CharacterEncodingOverride / NonXMLContentType 是警告,有 entries 就算可用
    for benign in ("CharacterEncodingOverride", "NonXMLContentType"):
        entries, usable = mr._feed_usable(_FakeFeed([{"title": "x"}], True, benign))
        assert usable is True and len(entries) == 1


def test_feed_usable_fatal_bozo_not_usable():
    # SAXParseException 是真的解析失敗 → 不可用(會走 fallback)
    _, usable = mr._feed_usable(_FakeFeed([{"title": "x"}], True, "SAXParseException"))
    assert usable is False


def test_feed_usable_clean_feed():
    _, usable = mr._feed_usable(_FakeFeed([{"title": "x"}], False, None))
    assert usable is True
    _, usable_empty = mr._feed_usable(_FakeFeed([], False, None))
    assert usable_empty is False


def test_official_source_entries_accepts_benign_bozo(monkeypatch):
    """EY/CDC 類:feedparser 設 CharacterEncodingOverride/NonXMLContentType 但有 entries
    → 直接採用,不再誤判失敗、不記為 error。"""
    monkeypatch.setattr(
        mr, "_feedparser_parse_url_with_timeout",
        lambda url, timeout=12: _FakeFeed(
            [{"title": "行政院公告", "link": "https://ey.gov.tw/x"}],
            True, "CharacterEncodingOverride"))
    stats = {}
    out = mr._official_source_entries(
        {"name": "EY News", "url": "https://www.ey.gov.tw/x"}, stats)
    assert len(out) == 1
    assert stats.get("feed_ok") == 1
    assert not stats.get("errors")     # 良性警告不記為錯誤


def test_mops_roc_datetime():
    """MOPS 民國發言日期+時間 → 台北時區 datetime。"""
    d = mr._mops_roc_datetime("1150702", "70003")   # 115/07/02 07:00:03
    assert d is not None and (d.year, d.month, d.day, d.hour) == (2026, 7, 2, 7)
    assert mr._mops_roc_datetime("", "") is None
    assert mr._mops_roc_datetime("abc", "1") is None


def test_fetch_tw_major_announcements_openapi(monkeypatch):
    """重大訊息改用 OpenAPI t187ap04_L:依代號過濾、主旨/說明/時間映射、時間 desc。"""
    rows = [
        {"公司代號": "2330", "主旨 ": "台積電 公告子公司增資", "說明": "1.事實發生日：...",
         "發言日期": "1150702", "發言時間": "70003"},
        {"公司代號": "2454", "主旨 ": "聯發科 董事會決議", "說明": "說明內容",
         "發言日期": "1150701", "發言時間": "143000"},
        {"公司代號": "9999", "主旨 ": "非目標公司", "說明": "x",
         "發言日期": "1150702", "發言時間": "90000"},
    ]

    class _R:
        def json(self):
            return rows

    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _R())
    out = mr.fetch_tw_major_announcements(["2330", "2454"], hours=10 ** 7)  # 大窗→不因日期被濾
    codes = [o["code"] for o in out]
    assert "2330" in codes and "2454" in codes and "9999" not in codes   # 只留目標公司
    top = next(o for o in out if o["code"] == "2330")
    assert top["title"] == "台積電 公告子公司增資"          # 「主旨 」尾空白鍵處理
    assert top["summary"].startswith("1.事實發生日")        # 說明 → summary
    assert top["published"].startswith("2026-07-02T07:00")  # 民國+時間 → ISO(可被解析器讀)
    assert codes.index("2330") < codes.index("2454")         # 時間 desc


def test_http_get_retries_on_5xx(monkeypatch):
    """5xx 會重試,下一次成功即回傳。"""
    calls = {"n": 0}

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake(url, **kw):
        calls["n"] += 1
        return _R(500 if calls["n"] == 1 else 200)

    monkeypatch.setattr(mr.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mr.requests, "get", fake)
    r = mr._http_get("https://x", retries=2)
    assert r.status_code == 200 and calls["n"] == 2


def test_http_get_passthrough_fake_without_status(monkeypatch):
    """測試假物件無 status_code → 視為 200、直接回(不重試),向後相容既有 monkeypatch。"""
    class _Fake:
        def json(self):
            return {"ok": 1}

    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Fake())
    assert mr._http_get("https://x").json() == {"ok": 1}


def test_http_get_raises_after_exhausting_retries(monkeypatch):
    import pytest
    monkeypatch.setattr(mr.time, "sleep", lambda *_: None)

    def boom(*a, **k):
        raise mr.requests.ConnectionError("down")

    monkeypatch.setattr(mr.requests, "get", boom)
    with pytest.raises(mr.requests.RequestException):
        mr._http_get("https://x", retries=1)


def test_feed_host_circuit_breaker_stops_hammering_dead_host(monkeypatch):
    """host 連續失敗達門檻且零成功 → 後續同 host 查詢直接熔斷,不再送 HTTP(避免整批 503
    把 job timeout 耗光導致晨報未寄,2026-07-08 事故)。"""
    calls = {"n": 0}

    def _boom(url, **kw):
        calls["n"] += 1
        raise RuntimeError("simulated 503")

    monkeypatch.setattr(mr, "_http_get", _boom)
    n = mr._FEED_HOST_CIRCUIT_BREAK
    for i in range(n + 5):
        try:   # 不同 URL 避開內容快取;同一 host(news.google.com)共用 _FEED_STATS
            mr._feedparser_parse_url_with_timeout(f"https://news.google.com/rss/search?q=q{i}")
        except Exception:
            pass
    assert calls["n"] == n   # 只有前 n 次真的送 HTTP,之後全被熔斷跳過


def test_feed_host_circuit_breaker_resets_streak_on_success(monkeypatch):
    """連續失敗才熔斷:先成功數次(streak 歸零)、之後才整批 503,仍能正確熔斷
    (涵蓋 Google News 跑到一半才被限流的情境)。"""
    n = mr._FEED_HOST_CIRCUIT_BREAK
    calls = {"n": 0}
    mode = {"ok": True}

    class _Resp:
        content = b"<rss></rss>"

        def raise_for_status(self):
            pass

    def _fake(url, **kw):
        calls["n"] += 1
        if mode["ok"]:
            return _Resp()
        raise RuntimeError("simulated 503")

    monkeypatch.setattr(mr, "_http_get", _fake)
    for i in range(2):   # 先 2 次成功 → streak 保持 0
        mr._feedparser_parse_url_with_timeout(f"https://news.google.com/rss/search?q=ok{i}")
    mode["ok"] = False
    for i in range(n + 3):   # 之後連續失敗
        try:
            mr._feedparser_parse_url_with_timeout(f"https://news.google.com/rss/search?q=bad{i}")
        except Exception:
            pass
    assert calls["n"] == 2 + n   # 2 成功 + n 次真失敗後熔斷,其餘不送 HTTP


def test_fetch_tw_major_announcements_dedups_only_exact_duplicate_rows(monkeypatch):
    """MOPS 去重:只移除「完全相同的重複列」。主旨相同但實質不同的公告必須保留——
    顯示用 summary 會截到 600 字,故鍵必須用整列原始資料(Codex review)。"""
    class _R:
        def __init__(self, p):
            self._p = p

        def json(self):
            return self._p

    base = "設備採購說明內容" * 120        # 遠超過 600 字
    t = "取得營業用機器設備達十億元"
    rows = [
        {"公司代號": "3711", "發言日期": "1150709", "發言時間": "17:51:00", "主旨 ": t,
         "說明": base + "尾A"},
        {"公司代號": "3711", "發言日期": "1150709", "發言時間": "17:51:00", "主旨 ": t,
         "說明": base + "尾A"},                                    # 完全相同 → 合併
        {"公司代號": "3711", "發言日期": "1150709", "發言時間": "17:51:00", "主旨 ": t,
         "說明": base + "尾B"},                                    # 600 字後不同 → 保留
        {"公司代號": "3711", "發言日期": "1150709", "發言時間": "17:51:00", "主旨 ": t,
         "說明": base + "尾A", "事實發生日": "1150708"},            # 多一欄不同 → 保留
        {"公司代號": "3711", "發言日期": "壞日期", "發言時間": "壞時間", "主旨 ": t,
         "說明": base + "尾A"},                                    # 時間無法解析 → 保留
    ]
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _R(rows))
    out = mr.fetch_tw_major_announcements(["3711"], hours=100000)
    assert len(out) == 4                       # 5 筆中僅一組完全相同被合併
    assert all(o["code"] == "3711" and o["title"] == t for o in out)


def test_mask_malformed_numbers():
    """畸形千分位(逗號後 ≥4 位)遮蔽;合法千分位/小數不受影響。"""
    assert "(數值異常已略)" in mr._mask_malformed_numbers("瑞銀目標價 3,2424 元")
    assert "3,2424" not in mr._mask_malformed_numbers("目標價 3,2424 元")
    # 合法數字保留
    assert mr._mask_malformed_numbers("市值 12,345,678 元、股價 1,234 元") == "市值 12,345,678 元、股價 1,234 元"
    assert mr._mask_malformed_numbers("EPS 22.08") == "EPS 22.08"
    assert mr._mask_malformed_numbers("no commas here") == "no commas here"


def test_rss_content_cache_dedups_same_url(monkeypatch):
    """N5:同一 URL 一個 run 只抓一次(內容快取);不同 URL 各抓一次。"""
    calls = {"n": 0}

    class _R:
        status_code = 200
        content = b"<rss><channel><item><title>x</title></item></channel></rss>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kw):
        calls["n"] += 1
        return _R()

    monkeypatch.setattr(mr, "_http_get", fake_get)
    mr._feedparser_parse_url_with_timeout("https://news.example/A")
    mr._feedparser_parse_url_with_timeout("https://news.example/A")   # 命中快取、不再抓
    assert calls["n"] == 1
    mr._feedparser_parse_url_with_timeout("https://news.example/B")   # 不同 URL
    assert calls["n"] == 2


def test_feed_label_aggregates_to_host():
    assert mr._feed_label("https://news.google.com/rss/search?q=abc") == "news.google.com"
    assert mr._feed_label("https://WWW.EY.GOV.TW/x/y") == "www.ey.gov.tw"


def test_feed_stats_records_ok_and_fail(monkeypatch):
    """V2-N1:成功抓取記 ok、例外記 fail(依 host 聚合)。"""
    class _R:
        content = b"<rss/>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(mr, "_http_get", lambda url, **k: _R())
    mr._feedparser_parse_url_with_timeout("https://news.google.com/rss/search?q=x")
    assert mr._FEED_STATS["news.google.com"]["ok"] == 1

    def boom(url, **k):
        raise mr.requests.ConnectionError("down")

    monkeypatch.setattr(mr, "_http_get", boom)
    try:
        mr._feedparser_parse_url_with_timeout("https://ey.gov.tw/feed")
    except Exception:
        pass
    assert mr._FEED_STATS["ey.gov.tw"]["fail"] == 1
