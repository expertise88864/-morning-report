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
