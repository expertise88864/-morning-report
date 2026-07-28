"""批#57:線索軌跡 + 主動追蹤(使用者要求「前後連貫性、後續閱讀與比較」)。

**先前的缺口**

批#44 的線索帳本能說「昨天說 X → 今天 Y」,但只存 last_delta / prev_delta
**兩步**:跨週的比較無從寫起,而且沒有連結——讀者看到「這條線索已追蹤 5 次」
卻沒有辦法回去讀那 5 次分別說了什麼。

抓取端則完全是**被動**的:一條正在發展的線索能不能拿到後續消息,取決於它有沒有
剛好出現在那幾十個固定 feed 裡。若當天只有產業媒體報導而不在我們訂的來源,
線索會被判「今日無新進展」並開始降級,最後沉寂——**不是因為事情停了,
是因為我們沒去找**。
"""
import html as _html

import render_utils as ru
import story_ledger as sl

_DAYS = [("2026-07-20", "鴻海洽談收購A公司 斥資100億", "https://a/1", "中央社"),
         ("2026-07-23", "鴻海董事會通過收購案 上修至200億", "https://a/2", "經濟日報"),
         ("2026-07-27", "鴻海收購案完成交割", "https://a/3", "工商時報")]


def _run(days):
    led = []
    for d, title, link, src in days:
        led = sl.update_ledger(led, [{
            "entity": "2317", "entity_name": "鴻海", "event_type": "orders",
            "title": title, "link": link, "source_name": src,
            "published": f"{d}T01:00:00+00:00", "surprise_score": 0.6,
        }], d, {"2317": "鴻海"})
    return led


def test_timeline_records_each_step_with_link_and_facts():
    story = _run(_DAYS)[0]
    tl = story["timeline"]
    assert [e["d"] for e in tl] == ["2026-07-20", "2026-07-23", "2026-07-27"]
    assert [e["l"] for e in tl] == ["https://a/1", "https://a/2", "https://a/3"]
    assert [e["s"] for e in tl] == ["中央社", "經濟日報", "工商時報"]
    # 數字軌跡:100億 → 200億(這正是「比較」要看的東西)
    assert sl.format_fact(tl[0]["f"][0]) == "100億"
    assert sl.format_fact(tl[1]["f"][0]) == "200億"


def test_same_day_repeats_do_not_fill_the_timeline():
    """同日多則報導只留最後一筆,否則忙碌的一天就會把軌跡塞滿、擠掉上週。"""
    led = _run(_DAYS)
    led = sl.update_ledger(led, [{
        "entity": "2317", "entity_name": "鴻海", "event_type": "orders",
        "title": "鴻海收購案完成交割 細節公布 金額300億",
        "link": "https://a/4", "source_name": "自由財經",
        "published": "2026-07-27T05:00:00+00:00", "surprise_score": 0.6,
    }], "2026-07-27", {"2317": "鴻海"})
    tl = led[0]["timeline"]
    assert len([e for e in tl if e["d"] == "2026-07-27"]) == 1
    assert tl[-1]["l"] == "https://a/4", "同日應留最後一筆"


def test_timeline_is_capped():
    days = [(f"2026-07-{d:02d}", f"進展{d} 金額{d}億", f"https://a/{d}", "某報")
            for d in range(10, 25)]
    story = _run(days)[0]
    assert len(story["timeline"]) == sl.TIMELINE_KEEP


def test_dormant_stories_keep_only_first_and_last():
    """479 條線索若每條都留六點,state 檔會膨脹一倍、每天的 commit diff 也變大。"""
    story = {"state": "dormant",
             "timeline": [{"d": f"2026-07-{d}"} for d in
                          ("10", "11", "12", "13", "14", "15")]}
    sl.prune_timeline(story)
    assert [e["d"] for e in story["timeline"]] == ["2026-07-10", "2026-07-15"]
    # 活躍線索不修剪
    alive = {"state": "peak", "timeline": [{"d": "a"}, {"d": "b"}, {"d": "c"}]}
    sl.prune_timeline(alive)
    assert len(alive["timeline"]) == 3


def test_prompt_block_carries_the_trajectory():
    """LLM 要寫得出「上週說 X → 前天 Y → 今天 Z」,就必須看得到軌跡。"""
    block = sl.format_story_block(
        _run(_DAYS), sanitize=lambda x, n=200: str(x or "")[:n],
        today="2026-07-27")
    assert "軌跡:" in block
    assert "2026-07-20" in block and "2026-07-23" in block
    assert "100億" in block and "200億" in block, "數字軌跡沒進 prompt"


def test_fact_numbers_are_rendered_in_chinese_units():
    """帳本存的是正規化後的純數字(才比對得出金額有沒有變),直接印是一串零。"""
    assert sl.format_fact("10000000000") == "100億"
    assert sl.format_fact("200000000") == "2億"
    assert sl.format_fact("15000") == "1.5萬"
    assert sl.format_fact("3.5") == "3.5"
    assert sl.format_fact("x") == "x"


def test_timeline_card_renders_links_and_skips_single_point_stories():
    html = ru._render_story_timeline_html(_run(_DAYS), _html)
    assert "線索追蹤" in html
    for url in ("https://a/1", "https://a/2", "https://a/3"):
        assert url in html, f"{url} 沒出現 —— 讀者無法回去讀原文"
    assert "100億" in html and "200億" in html

    # 只有一個時間點 = 沒有「前後」可言,不佔版面
    assert ru._render_story_timeline_html(
        [{"state": "peak", "timeline": [{"d": "2026-07-27", "t": "x"}]}],
        _html) == ""
    assert ru._render_story_timeline_html([], _html) == ""
    # 沉寂線索不進卡片
    assert ru._render_story_timeline_html(
        [{"state": "dormant", "timeline": [{"d": "a", "t": "x"},
                                           {"d": "b", "t": "y"}]}], _html) == ""


def test_timeline_card_escapes_untrusted_text():
    """標題與來源都是外部文字,且會跨日回流——與 prompt 同一條注入路徑。"""
    led = [{"state": "peak", "entity_name": "<script>x</script>",
            "headline": "<img onerror=1>", "updates": 2,
            "first_seen": "2026-07-20",
            "timeline": [{"d": "2026-07-20", "t": "<b>a</b>", "l": "https://a/1",
                          "s": "<i>s</i>", "f": []},
                         {"d": "2026-07-27", "t": "b", "l": "javascript:alert(1)",
                          "s": "x", "f": []}]}]
    html = ru._render_story_timeline_html(led, _html)
    assert "<script>" not in html and "<img onerror" not in html
    assert "&lt;script&gt;" in html
    # 非 http 開頭的連結不得變成可點的 href
    assert "href='javascript:" not in html


# ===== 主動追蹤查詢 =====

def test_followup_requires_an_entity_anchor():
    """沒有公司名/代號可以錨定的線索(cluster 型 key),查詢只能由標題片段組成,
    而中文切不出乾淨的詞 —— **自測時實際帳本跑出「台女攀富 反覆失去」
    這種查詢**,撈回來的必然是雜訊。寧可不查。"""
    ledger = [
        {"key": "k1", "entity": "2317", "entity_name": "鴻海",
         "event_type": "orders", "state": "peak", "headline": "鴻海收購案",
         "updates": 3, "last_update": "2026-07-27"},
        {"key": "k2", "entity": "cluster:abc", "entity_name": "",
         "event_type": "general", "state": "peak", "headline": "某模糊標題",
         "updates": 2, "last_update": "2026-07-27"},
    ]
    out = sl.followup_queries(ledger, today="2026-07-27")
    assert [q for _, q, _e in out] == ["鴻海 訂單"], out
    # r1(Codex,P1):必須連**實體**一起回傳,結果才接得回這條線索
    assert [e for _, _q, e in out] == ["鴻海"], out


def test_followup_uses_event_type_not_sliced_headline():
    """中文沒有空白分詞,按固定字數硬切必然產生「Fed決」「特斯拉股」這種垃圾
    (自測第一版就是這樣)。改用 Python 已算好的 event_type 當語意標籤。"""
    base = {"key": "k", "entity": "2330", "entity_name": "台積電",
            "state": "peak", "updates": 2, "last_update": "2026-07-27",
            "headline": "台積電法說會 Fed決策 AI算力"}
    for et, want in (("earnings", "台積電 財報"), ("orders", "台積電 訂單"),
                     ("litigation", "台積電 訴訟"), ("general", "台積電")):
        q = sl.followup_queries([dict(base, event_type=et)],
                                today="2026-07-27")[0][1]
        assert q == want, f"{et}: {q}"


def test_followup_only_tracks_live_stories_and_dedupes():
    ledger = [
        {"key": "a", "entity": "2330", "entity_name": "台積電",
         "event_type": "earnings", "state": "peak", "updates": 2,
         "last_update": "2026-07-27", "headline": "x"},
        {"key": "b", "entity": "2330", "entity_name": "台積電",
         "event_type": "earnings", "state": "developing", "updates": 2,
         "last_update": "2026-07-27", "headline": "y"},     # 同公司同類型 → 去重
        {"key": "c", "entity": "2454", "entity_name": "聯發科",
         "event_type": "earnings", "state": "brewing", "updates": 1,
         "last_update": "2026-07-27", "headline": "z"},     # 醞釀中不追
    ]
    qs = [q for _, q, _e in sl.followup_queries(ledger, today="2026-07-27")]
    assert qs == ["台積電 財報"], qs


def test_followup_respects_the_query_budget():
    """每條查詢是一次 Google News 請求,而新聞抓取本來就是 wall-clock 主導者。"""
    ledger = [{"key": f"k{i}", "entity": f"2{i:03d}", "entity_name": f"公司{i}",
               "event_type": "orders", "state": "peak", "updates": 2,
               "last_update": "2026-07-27", "headline": "x"} for i in range(20)]
    assert len(sl.followup_queries(ledger, today="2026-07-27")) \
        == sl.FOLLOWUP_MAX_QUERIES


def test_fetch_news_accepts_followups_without_network(monkeypatch):
    """接線檢查:追蹤查詢要真的變成一筆抓取工作(且來源標明是追蹤)。"""
    import morning_report as mr
    seen = []

    def _fake(w, cutoff):
        seen.append(w)
        return []

    monkeypatch.setattr(mr, "_process_feed_item", _fake)
    monkeypatch.setattr(mr, "NEWS_FETCH_WORKERS", 1)
    mr.fetch_news([("k1", "鴻海 訂單")])
    tracked = [w for w in seen if str(w["source"]).startswith("追蹤:")]
    assert len(tracked) == 1
    assert tracked[0]["source"] == "追蹤:鴻海 訂單"
    assert "news.google.com" in tracked[0]["url"]
    # 不傳 followups 時行為不變
    seen.clear()
    mr.fetch_news()
    assert not [w for w in seen if str(w["source"]).startswith("追蹤:")]


# ===== r1(Codex 補審)確認的五條 =====

def test_link_survives_the_production_normaliser():
    """r1(Codex,P1)**確認且全滅**:批#57 的軌跡要存原文連結,但 `link` 從未被
    保留到 extract_structured_events 產生的事件裡 → 生產帳本
    **539/539 個軌跡點的 `l` 都是空的**,「可點回原文」從第一天就沒生效。

    我原本的測試直接餵 link 給 update_ledger,**繞過了會把它丟掉的正規化步驟**
    ——測試驗的是我蓋的東西,不是生產送進來的東西(本專案第四次)。
    這條走真正的生產路徑。
    """
    import morning_report as mr
    events = mr.extract_structured_events(news=[{
        "source": "Google:鴻海", "title": "鴻海收購案有新進展", "summary": "x",
        "link": "https://example.com/a", "source_name": "某報",
        "company_label": "2317",
        "published": "2026-07-27T02:00:00+00:00"}], mops=[])
    assert events and events[0].get("link") == "https://example.com/a", \
        f"link 沒被保留:{events}"

    led = sl.update_ledger([], events, "2026-07-27", {"2317": "鴻海"})
    assert led[0]["timeline"][0]["l"] == "https://example.com/a", \
        "軌跡點的連結是空的 —— 讀者無法回去讀原文"


def test_only_http_urls_reach_the_ledger():
    """連結會跨日回流進 state 與 HTML,越早收斂越好。
    r1(Codex,P2):`startswith("http")` 會放行 `httpx://`、`httpjavascript:`。"""
    import morning_report as mr
    for bad in ("httpx://evil", "httpjavascript:alert(1)", "javascript:alert(1)",
                "ftp://x/y", "http://", "", None):
        assert mr._safe_source_url(bad) == "", f"{bad!r} 被放行"
    for good in ("https://a.com/1", "http://a.com/1"):
        assert mr._safe_source_url(good) == good


def test_render_layer_also_rejects_deceptive_schemes():
    """縱深防禦:舊資料與手動編輯的 state 都可能帶著不合格的連結回流。"""
    for bad in ("httpx://evil", "httpjavascript:alert(1)", "javascript:x"):
        led = [{"state": "peak", "entity_name": "X", "headline": "h",
                "updates": 2, "first_seen": "2026-07-20",
                "timeline": [{"d": "2026-07-20", "t": "a", "l": bad, "s": "s",
                              "f": []},
                             {"d": "2026-07-27", "t": "b", "l": bad, "s": "s",
                              "f": []}]}]
        html = ru._render_story_timeline_html(led, _html)
        assert "<a href=" not in html, f"{bad!r} 變成可點連結"
    assert not ru._is_web_url("httpx://evil")
    assert ru._is_web_url("https://a.com")


def test_followup_results_carry_the_target_entity(monkeypatch):
    """r1(Codex,P1):結果必須接得回發起查詢的那條線索。
    原本只留查詢文字、丟掉 story key 與實體 → 抓回來的文章在
    extract_structured_events 只能從 entity/code/company_label 推 entity,
    而 RSS 項目沒有那些欄位 → 產生一條**無主的新線索**。
    於是 LLM 抽取關掉/無金鑰/時間預算不足時,追蹤查詢等於白做。"""
    import morning_report as mr
    seen = []

    def _fake(w, cutoff):
        seen.append(w)
        return []

    monkeypatch.setattr(mr, "_process_feed_item", _fake)
    monkeypatch.setattr(mr, "NEWS_FETCH_WORKERS", 1)
    mr.fetch_news([("k1", "鴻海 訂單", "鴻海")])
    tracked = [w for w in seen if str(w["source"]).startswith("追蹤:")][0]
    assert tracked["followup_entity"] == "鴻海"
    assert tracked["followup_key"] == "k1"


def test_same_batch_authoritative_replacement_also_moves_the_timeline():
    """r1(Codex,P2):同一次 update_ledger 裡同 key 的兩則事件,後一則權威到足以
    取代前一則時,原本 headline/last_delta 換了、**當天的軌跡點卻還指向第一則**
    ——「先成立、後取消」會顯示成自相矛盾的軌跡。"""
    base = {"entity": "2317", "entity_name": "鴻海", "event_type": "orders",
            "published": "2026-07-27T01:00:00+00:00"}
    led = sl.update_ledger([], [dict(base, title="鴻海收購案成立",
                                     link="https://a/1", source_name="某媒體",
                                     source_grade="C", surprise_score=0.6)],
                           "2026-07-27", {"2317": "鴻海"})
    led = sl.update_ledger(led, [
        dict(base, title="鴻海收購案成立", link="https://a/1",
             source_name="某媒體", source_grade="C", surprise_score=0.6,
             direction=1),
        dict(base, title="鴻海公告收購案取消", link="https://a/2",
             source_name="MOPS", source_grade="A", surprise_score=0.8,
             direction=-1)], "2026-07-27", {"2317": "鴻海"})
    story = led[0]
    today_pt = [e for e in story["timeline"] if e["d"] == "2026-07-27"]
    assert len(today_pt) == 1
    assert "取消" in today_pt[0]["t"], "軌跡點沒跟著換 —— 與 headline 自相矛盾"
    assert today_pt[0]["l"] == "https://a/2"
    assert "取消" in story["headline"]
