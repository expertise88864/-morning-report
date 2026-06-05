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

    monkeypatch.setattr(mr.feedparser, "parse", lambda url: _FakeFeed(url))
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

    monkeypatch.setattr(mr.feedparser, "parse", lambda url: _Feed())
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
    monkeypatch.setattr(mr.feedparser, "parse", lambda url: _Feed(url))
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
