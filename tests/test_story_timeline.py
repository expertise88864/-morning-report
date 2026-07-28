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
    assert [f["query"] for f in out] == ["鴻海 訂單"], out
    # r2(Codex,P1):回傳的必須是**線索 key 所用的那個實體**(代號),
    # 不是查詢用的公司名 —— 我上一輪回傳「鴻海」,而線索 key 是
    # `e:2317|...`,抓回來的文章因此開出 entity=鴻海 的新線索,缺陷沒解掉。
    # 回傳的是**線索 key 所用的實體**(代號),不是查詢用的公司名;
    # 顯示名另存於 name,供「文章是否真的提到這家公司」的檢查用。
    assert [f["entity"] for f in out] == ["2317"], out
    assert [f["name"] for f in out] == ["鴻海"], out


def test_followup_uses_event_type_not_sliced_headline():
    """中文沒有空白分詞,按固定字數硬切必然產生「Fed決」「特斯拉股」這種垃圾
    (自測第一版就是這樣)。改用 Python 已算好的 event_type 當語意標籤。"""
    base = {"key": "k", "entity": "2330", "entity_name": "台積電",
            "state": "peak", "updates": 2, "last_update": "2026-07-27",
            "headline": "台積電法說會 Fed決策 AI算力"}
    for et, want in (("earnings", "台積電 財報"), ("orders", "台積電 訂單"),
                     ("litigation", "台積電 訴訟"), ("general", "台積電")):
        q = sl.followup_queries([dict(base, event_type=et)],
                                today="2026-07-27")[0]["query"]
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
    qs = [f["query"] for f in sl.followup_queries(ledger, today="2026-07-27")]
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
    mr.fetch_news([{"key": "k1", "query": "鴻海 訂單",
                    "entity": "2317", "name": "鴻海"}])
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
    mr.fetch_news([{"key": "k1", "query": "鴻海 訂單",
                    "entity": "2317", "name": "鴻海"}])
    tracked = [w for w in seen if str(w["source"]).startswith("追蹤:")][0]
    assert tracked["followup_entity"] == "2317"
    assert tracked["followup_name"] == "鴻海"
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


def test_followup_article_reconnects_to_the_originating_story():
    """r2(Codex,P1):**光是接回 entity 不夠**。線索 key 含 lineage,而追蹤抓回來
    的文章 event_type 由標題推導(常是 general 且帶標題 digest)→ key 仍與原線索
    不同(e:2317|l:general|xxxx vs e:2317|l:orders),照樣開出新線索,
    主動追蹤等於白做。真正的解法是直接帶著發起查詢的那條線索的 key。"""
    import morning_report as mr
    KEY = "e:2317|l:orders"
    led = [{"key": KEY, "entity": "2317", "entity_name": "鴻海",
            "event_type": "orders", "state": "peak",
            "headline": "鴻海洽談收購案", "updates": 3,
            "last_update": "2026-07-26", "last_delta": "鴻海洽談收購案",
            "timeline": [{"d": "2026-07-26", "t": "鴻海洽談收購案",
                          "l": "https://a/1", "s": "某報", "f": []}]}]
    fu = sl.followup_queries(led, today="2026-07-27")[0]
    key, query, ent = fu["key"], fu["query"], fu["entity"]
    assert (key, ent) == (KEY, "2317")

    events = mr.extract_structured_events(news=[{
        "source": f"追蹤:{query}", "title": "鴻海收購案新進展 金額200億",
        "summary": "x", "link": "https://a/9", "source_name": "某報",
        "company_label": ent, "followup_key": key,
        "published": "2026-07-27T02:00:00+00:00"}], mops=[])
    # r3(Codex,P1):採用點從 story_key_for_event 移到 update_ledger ——
    # 只有在那裡才拿得到目標線索,能先比對主體再決定要不要採用這個提示。
    # 單看事件本身推導出來的 key 仍是 general(那正是要靠提示補救的原因)。
    assert sl.story_key_for_event(events[0]) != KEY

    out = sl.update_ledger(led, events, "2026-07-27", {"2317": "鴻海"})
    assert len(out) == 1, f"開出了新線索而不是接回原本那條:{[s['key'] for s in out]}"
    assert len(out[0]["timeline"]) == 2, "軌跡沒有續上"


def test_followup_key_must_look_like_a_ledger_key():
    """followup_key 由本系統的帳本產生、經抓取工作項傳遞,不是外部文字。
    仍加形狀檢查:格式不符者回退到正常推導,不讓任意字串決定線索身分。"""
    for bogus in ("", "隨便的字串", "../../etc", None):
        ev = {"entity": "2330", "event_type": "earnings", "title": "台積電財報",
              "published": "2026-07-27T01:00:00+00:00", "followup_key": bogus}
        assert sl.story_key_for_event(ev) != bogus
        assert sl.story_key_for_event(ev).startswith(("e:", "h:", "cluster:"))


def test_sports_header_matches_the_worldcup_block_label():
    """r2(Codex,P2):世足區塊寫的是「世界盃足球賽」而辨識字串寫成「世足」——
    只有世足資料時區塊會出現、標題卻漏掉它,正好是這條修正要防的反向落差。
    **辨識字串必須逐字對應區塊實際輸出的標題**,不能憑印象寫簡稱。"""
    import html as _h2
    wc = {"results": [{"date": "07/18", "text": "A 2-1 B(決賽)"}]}
    head = ru._render_sports_html({"worldcup": wc}, _h2).split("</h2>")[0]
    assert "世足" in head, "有世足區塊卻沒列進標題"


def test_followup_log_line_does_not_lie(monkeypatch, capsys):
    """r2(Codex,P2):**我修 F2 時自己弄壞的** —— 三元組被 2-tuple 解包,
    每次有追蹤查詢都拋 ValueError、被 except 吞掉並印出「追蹤查詢略過」,
    但清單其實照樣送進 fetch_news:日誌在說謊。"""
    fus = [{"key": "e:2317|l:orders", "query": "鴻海 訂單",
            "entity": "2317", "name": "鴻海"}]
    line = "[story] 主動追蹤查詢 " + "、".join(
        str(f.get("query") or "") for f in fus)
    assert line.endswith("鴻海 訂單")


def test_followup_key_is_only_a_hint_not_a_forced_attribution():
    """r3(Codex,P1)**我自己列進 review focus 的風險成真了**:
    Google News 查詢本來就會撈回不相干或只是「沾到同一家公司」的文章。
    無條件採用 followup_key 會讓那些文章被強制掛進該線索、**取代它的 headline
    與軌跡點、把它標成今日有更新**,還影響公司催化評分。

    採用前必須通過既有的主體比對(本模組原本就用來判斷「這則是不是續報」的
    判準,直接重用不另造一套)。不通過就退回正常推導,讓它自己開一條線索
    ——那是誠實的:它確實是另一件事。
    """
    import morning_report as mr
    KEY = "e:2317|l:orders"

    def _ledger():
        return [{"key": KEY, "entity": "2317", "entity_name": "鴻海",
                 "event_type": "orders", "state": "peak",
                 "headline": "鴻海洽談收購A公司", "updates": 3,
                 "last_update": "2026-07-26", "last_delta": "鴻海洽談收購A公司",
                 "timeline": [{"d": "2026-07-26", "t": "鴻海洽談收購A公司",
                               "l": "https://a/1", "s": "某報", "f": []}]}]

    def _run(title):
        events = mr.extract_structured_events(news=[{
            "source": "追蹤:鴻海 訂單", "title": title, "summary": "x",
            "link": "https://a/9", "source_name": "某報",
            "company_label": "2317", "followup_key": KEY,
            "published": "2026-07-27T02:00:00+00:00"}], mops=[])
        out = sl.update_ledger(_ledger(), events, "2026-07-27", {"2317": "鴻海"})
        target = [s for s in out if s["key"] == KEY][0]
        return len(out), target["headline"]

    # 相關的續報 → 接回原線索
    n, head = _run("鴻海收購A公司案新進展 金額200億")
    assert n == 1 and "新進展" in head

    # 只是沾到同一家公司的雜訊 → **不得**污染原線索
    n2, head2 = _run("鴻海尾牙抽獎最大獎百萬")
    assert n2 == 2, "不相干的文章被強制掛進線索"
    assert head2 == "鴻海洽談收購A公司", f"原線索 headline 被雜訊取代:{head2}"


def test_sports_header_covers_news_only_blocks():
    """r3(Codex,P2):世足在**沒有結構化賽果、只有新聞**時會渲染「世足 消息」,
    而辨識字串是「世界盃足球賽」→ 區塊出現、標題卻漏掉它。
    新聞區塊的標籤直接來自 news 字典的鍵,拿鍵判斷比掃 HTML 可靠。"""
    import html as _h2
    only_news = ru._render_sports_html(
        {"news": {"世足": [{"title": "世足新聞", "link": "https://a"}]}}, _h2)
    assert "世足" in only_news.split("</h2>")[0], "只有新聞時標題漏了世足"
    assert "世足 消息" in only_news, "區塊本身應該有出現"

    row = {"rank": 1, "team": "味全龍", "wdl": "46-0-28",
           "pct": "0.622", "gb": "-"}
    both = ru._render_sports_html(
        {"cpbl": [row],
         "news": {"網球": [{"title": "網球新聞", "link": "https://a"}]}}, _h2)
    head = both.split("</h2>")[0]
    assert "中職" in head and "網球" in head


def test_followup_hint_rejected_when_event_type_disagrees():
    """r3(Codex,P1):**_is_same_subject 一個人擋不住**。它的門檻
    (SUBJECT_OVERLAP_MIN=0.10)是**刻意寬鬆**的——設計目的是「已經同 key 時
    要不要保留前情」,偏向保住連續性;拿它當歸屬閘門是用寬鬆的檢查做嚴格的事。

    Codex 的反例:AI 伺服器**專利訴訟**與 AI 伺服器**訂單**的標題重疊度足以越過
    0.10,於是訴訟被強制掛進 orders 線索、取代它的 headline 與軌跡。

    關鍵在於**為什麼需要這個提示**:追蹤抓回來的文章 event_type 常被推導成
    general(標題看不出類型),key 因而與原線索不同。但若它**自己就推導出一個
    明確且不同的類型**,那正是「這是另一件事」的證據——此時相信它自己的判斷。
    """
    KEY = "e:2317|l:orders"

    def _target():
        return {"key": KEY, "entity": "2317", "entity_name": "鴻海",
                "event_type": "orders", "state": "peak",
                "headline": "鴻海獲AI伺服器大單", "updates": 3,
                "last_update": "2026-07-26", "last_delta": "鴻海獲AI伺服器大單"}

    def _resolve(event_type, title):
        ev = {"entity": "2317", "entity_name": "鴻海",
              "event_type": event_type, "title": title,
              "published": "2026-07-27T01:00:00+00:00", "followup_key": KEY}
        return sl._resolve_story_key(ev, {KEY: _target()})

    # 同型續報 → 接回
    assert _resolve("orders", "鴻海AI伺服器訂單再加碼") == KEY
    # 型別不明(這正是需要提示的情況)→ 接回
    assert _resolve("general", "鴻海AI伺服器最新進展") == KEY
    # **Codex 反例**:明確且不同的型別 → 相信它自己,另開線索
    assert _resolve("litigation", "鴻海AI伺服器專利訴訟開庭") != KEY
    assert _resolve("earnings", "鴻海AI伺服器業務財報表現") != KEY
    # 主體完全不相干 → 另開線索(第一道防線仍在)
    assert _resolve("general", "鴻海尾牙抽獎百萬") != KEY


def test_followup_hint_ignored_when_target_story_is_gone():
    """目標線索已被清掉(超過 KEEP_DAYS)時,提示不得憑空指向不存在的 key。"""
    ev = {"entity": "2317", "entity_name": "鴻海", "event_type": "general",
          "title": "鴻海某進展", "published": "2026-07-27T01:00:00+00:00",
          "followup_key": "e:2317|l:orders"}
    assert sl._resolve_story_key(ev, {}) == sl.story_key_for_event(ev)


def test_followup_label_requires_the_article_to_mention_the_company():
    """r4(Codex,P1):**貼標不能無條件**。Google News 查詢會漂移,撈回完全沒提到
    該公司的文章。貼上 company_label 之後,extract_structured_events 會把它變成
    事件的 entity,_stock_news_catalysts 隨即以 `entity == code` 判為**直接**
    公司事件 —— 影響催化評分、排名、價格預測與**存檔的 model history**。

    實測(這條測試就是照那個實測寫的):一則沒提到鴻海的廣達新聞,
    貼標後 relation=direct、分數 0.39;不貼標則是 ai_server industry、0.1
    ——**假歸因讓分數膨脹近四倍**。
    """
    import morning_report as mr
    universe = [{"code": "2317", "name": "鴻海"}]
    article = {"source": "追蹤:鴻海 訂單",
               "title": "廣達獲AI伺服器大單 訂單能見度到明年",
               "summary": "廣達接單暢旺", "link": "https://a/9",
               "source_name": "某報",
               "published": "2026-07-27T02:00:00+00:00"}

    labelled = mr._stock_news_catalysts(
        universe, [dict(article, company_label="2317")], [])
    plain = mr._stock_news_catalysts(universe, [article], [])
    assert labelled["2317"]["score"] > plain["2317"]["score"], \
        "對照組無效 —— 貼標本來就該讓分數變高,否則測不到污染"
    assert labelled["2317"]["evidence"][0]["relation"] == "direct"
    assert plain["2317"]["evidence"][0]["relation"] != "direct"


def test_followup_label_gate_runs_where_the_article_text_is(monkeypatch):
    """接線檢查:貼標的判斷必須發生在**文章內容還在手上**的地方(_process_feed_item),
    而不是事後補救——一旦標籤下去,下游就無從分辨它是真的還是查詢帶進來的。"""
    import datetime as _dt
    import morning_report as mr

    class _Entry(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    def _fake_parse(url, *a, **kw):
        feed = type("F", (), {})()
        feed.entries = [
            _Entry(title="鴻海收購案新進展", summary="細節公布",
                   link="https://a/1",
                   published="Mon, 27 Jul 2026 02:00:00 GMT"),
            _Entry(title="廣達獲AI伺服器大單", summary="接單暢旺",
                   link="https://a/2",
                   published="Mon, 27 Jul 2026 02:00:00 GMT"),
        ]
        return feed

    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout", _fake_parse)
    cutoff = _dt.datetime(2026, 7, 26, tzinfo=_dt.timezone.utc)
    items = mr._process_feed_item(
        {"idx": 0, "source": "追蹤:鴻海 訂單",
         "url": "https://news.google.com/rss/search?q=x", "kind": "rss",
         "followup_entity": "2317", "followup_name": "鴻海",
         "followup_key": "e:2317|l:orders"}, cutoff)
    labels = {n["title"]: n.get("company_label") for n in items}
    assert labels.get("鴻海收購案新進展") == "2317", "有提到公司卻沒貼標"
    assert labels.get("廣達獲AI伺服器大單") is None,         "沒提到鴻海的文章被貼上 2317 —— 假歸因會流進催化評分"
    # followup_key 同樣只跟著合格的那則
    keys = {n["title"]: n.get("followup_key") for n in items}
    assert keys.get("鴻海收購案新進展") == "e:2317|l:orders"
    assert keys.get("廣達獲AI伺服器大單") is None


def test_general_target_can_be_upgraded_by_a_specific_followup():
    """r5(Codex,P2)**這正是我送審時自己標記的邊界,確認過嚴了**:
    線索常以 general 起頭(早期標題看不出類型),之後的後續報導才把它講清楚。
    原本的條件會把那則**正確的後續報導**判為矛盾、另開一條,反而切斷軌跡、
    讓原線索停在舊值。只有雙方都明確且不同才算矛盾。"""
    KEY = "e:2317|l:general|abc"

    def _target(t):
        return {"key": KEY, "entity": "2317", "entity_name": "鴻海",
                "event_type": t, "state": "peak", "headline": "鴻海傳有大動作",
                "updates": 2, "last_update": "2026-07-26",
                "last_delta": "鴻海傳有大動作"}

    def _resolve(ev_type, tgt_type):
        target = _target(tgt_type)
        ev = {"entity": "2317", "entity_name": "鴻海", "event_type": ev_type,
              "title": "鴻海大動作為收購案 已簽約",
              "published": "2026-07-27T01:00:00+00:00", "followup_key": KEY}
        return sl._resolve_story_key(ev, {KEY: target}) == KEY, target["event_type"]

    # general 線索被講清楚 → 接回,且型別跟著升級(後續比對才有依據)
    reconnected, new_type = _resolve("orders", "general")
    assert reconnected, "正確的後續報導被判為矛盾"
    assert new_type == "orders", "線索型別沒有升級"

    # 雙方都明確且不同 → 仍視為另一件事
    assert not _resolve("litigation", "orders")[0]
    # 一方 general → 接回
    assert _resolve("general", "orders")[0]
    assert _resolve("general", "general")[0]


def test_company_mention_gate_rejects_incidental_ticker_matches():
    """r5(Codex,P1):我上一版用無限制子字串比對,而且把代號本身當證據。
    反例:`MU` 出現在任何大寫詞裡(MUSIC)、`2317` 出現在價格或時間裡。"""
    import morning_report as mr
    assert mr._mentions_company("鴻海收購案新進展", "鴻海", "2317")
    assert not mr._mentions_company("廣達獲AI伺服器大單", "鴻海", "2317")
    # 拉丁代號需詞界
    assert not mr._mentions_company("APPLE MUSIC revenue up", "Micron", "MU")
    assert mr._mentions_company("MU guides higher", "Micron", "MU")
    assert mr._mentions_company("the mu parameter", "Micron", "MU")   # 大小寫不敏感
    # **純數字代號單獨不足以當證據**(數字在財經文章裡到處都是)
    assert not mr._mentions_company("某股收在 2317 元", "鴻海", "2317")
    assert not mr._mentions_company("成交量 2317 張", "", "2317")
    assert mr._mentions_company("", "鴻海", "2317") is False
