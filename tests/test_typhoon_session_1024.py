"""2026-10-24 颱風休市(10/23)後的實信:觀察點誤報漏回顧、昨日觀點被整包隔離。"""
import analysis_origin as ao
import analysis_recap as rc
import fallback_recap as fr
import fallback_watch_gap as gap


def _watch(watch_id, created, deadline, last_reviewed):
    return {"watch_id": watch_id, "created": created, "deadline": deadline,
            "last_reviewed": last_reviewed, "status": "open", "trigger": "t"}


def test_deadline_on_a_closed_day_counts_the_previous_session_review():
    """w64 期限 10/24(週六),10/23 那班已回顧;10/24 的目標交易日是 10/26,不能再回顧。"""
    state = {"date": "2026-10-23", "watch": [
        _watch("w64", "2026-10-19", "2026-10-24", "2026-10-23"),
        _watch("w90", "2026-10-19", "2026-10-24", "2026-10-21")]}   # 上一交易日沒回顧到:仍要報
    record = gap.due_cases(state, "2026-10-24", "2026-10-26", "2026-10-22")
    assert [c["watch_id"] for c in record["cases"]] == ["w90"], record
    # 期限當天就是交易日(10/21 走備援那天):這一班本身就是回顧機會,照舊要報
    state = {"date": "2026-10-20", "watch": [_watch("w53", "2026-09-23", "2026-10-21", "2026-10-20")]}
    assert gap.due_cases(state, "2026-10-21", "2026-10-21", "2026-10-20")["count"] == 1


def test_a_recap_from_the_unscheduled_closure_day_is_still_yesterday():
    """10/23 颱風休市那班的觀點比上一個交易日(10/22)新,中間沒有漏交易日。"""
    recap = {"date": "2026-10-23", "items": [{"statement": "外資期貨淨空未回補。"}]}
    assert rc.usable(recap, "2026-10-26", "2026-10-22")
    # 仍擋:早於上一個交易日(中間漏了交易日)、以及同日或未來
    assert rc.usable(dict(recap, date="2026-10-21"), "2026-10-26", "2026-10-22") == []
    assert rc.usable(dict(recap, date="2026-10-26"), "2026-10-26", "2026-10-22") == []
    url = "https://example.com/a"
    record = fr.extract(f"## 八、科技板塊脈動\n外資期貨淨空未回補。[來源]({url})\n", [{"link": url}],
                        "2026-10-23", ao.LEGACY_PRIMARY)
    projected = fr.for_prompt(record, "2026-10-22", "2026-10-26")
    assert projected["items"] and projected["date"] == "2026-10-23"
    assert fr.for_prompt(record, "2026-10-26", "2026-10-27") == {}


def test_official_suspension_replaces_the_forecast_and_merges_counties():
    """10-24 實信:人事總處已公告停班停課,卡片仍印「風雨接近停班停課參考標準」預測,
    人事總處四個縣市各一行、內容一字不差 —— 使用者:「解決今日重複訊息」。"""
    import morning_report as mr
    url = "https://www.dgpa.gov.tw/typh/daily/nds.html"
    locs = [{"name": "彰化市", "t_min": 24, "t_max": 27, "rain_prob": 100, "label": "陣雨",
             "wind": 30, "gust": 74, "rain_sum": 9},
            {"name": "台中北區", "t_min": 23, "t_max": 25, "rain_prob": 100, "label": "陣雨",
             "wind": 30, "gust": 76, "rain_sum": 19}]
    status = "今天已達停止上班及上課標準。"
    susp = [{"title": f"人事總處公告:{c} {status}", "link": url} for c in ("臺中市", "彰化縣", "雲林縣", "南投縣")]
    html = mr._render_weather_html(locs, susp)
    assert "接近停班停課參考標準" not in html, html
    assert html.count("人事總處公告") == 1 and "臺中市、彰化縣、雲林縣、南投縣 " + status in html
    # 沒有正式公告的地點仍要看到預測;不同狀態不併
    other = [{"title": f"人事總處公告:臺中市 {status}", "link": url},
             {"title": "人事總處公告:彰化縣 今天照常上班、照常上課。", "link": url}]
    html = mr._render_weather_html(locs + [dict(locs[0], name="南投市")], other)
    assert "南投市 風雨接近停班停課參考標準" in html and "彰化市 風雨" not in html
    assert html.count("人事總處公告") == 2


def test_a_pending_official_row_keeps_the_forecast():
    """PR #27 外審:官方表的「尚未宣布消息」不是決定,台中北區的預測要留著。"""
    import weather_dedupe as wd
    url = "https://www.dgpa.gov.tw/typh/daily/nds.html"
    pending = [{"title": "人事總處公告:臺中市 尚未宣布消息。", "link": url}]
    assert wd.announced(pending) == set()
    locs = [{"name": "台中北區", "gust": 76}]
    assert wd.without_announced(locs, pending) == locs
    assert wd.announced([{"title": "人事總處公告:臺中市 今天停止上班、停止上課", "link": url}]) == {"台中"}


def test_phantom_zero_zero_finals_do_not_take_the_game_number():
    """10-26 實信:被取代的舊場次被 Yahoo 改成「完賽 0:0、僅到第 1 局」,真正的 G5 4:3 排成 G7。"""
    import datetime as dt
    import sports_season as ss
    from test_sports_season_1014 import TPE, cpbl_games, yahoo
    rows = [("cpbl.g.261017001", "Sat, 17 Oct 2026 09:05:00 +0000", "status.type.final", "1", "3"),
            ("cpbl.g.261018002", "Sun, 18 Oct 2026 09:05:00 +0000", "status.type.final", "2", "4"),
            ("cpbl.g.261020003", "Tue, 20 Oct 2026 10:35:00 +0000", "status.type.final", "0", "6"),
            ("cpbl.g.261021004", "Wed, 21 Oct 2026 10:35:00 +0000", "status.type.final", "5", "4"),
            ("cpbl.g.261024006", "Sat, 24 Oct 2026 09:05:00 +0000", "status.type.final", "0", "0"),
            ("cpbl.g.261025005", "Sun, 25 Oct 2026 09:05:00 +0000", "status.type.final", "4", "3"),
            ("cpbl.g.261025006", "Sun, 25 Oct 2026 09:05:00 +0000", "status.type.final", "0", "0"),
            ("cpbl.g.261025007", "Sun, 25 Oct 2026 09:05:00 +0000", "status.type.final", "0", "0"),
            ("cpbl.g.261026006", "Mon, 26 Oct 2026 07:05:00 +0000", "status.type.pregame", None, None),
            ("cpbl.g.261027007", "Tue, 27 Oct 2026 10:35:00 +0000", "status.type.pregame", None, None)]
    games = cpbl_games(*(yahoo(gid, start, status=st, away_pts=a, home_pts=h) for gid, start, st, a, h in rows))
    for g in games:   # Yahoo 把空殼標完賽但只列到第 1 局 → yahoo_cpbl_games 的暫列標記
        if g["away_score"] == g["home_score"] == "0" and g["status"] == "status.type.final":
            g["provisional"] = "來源標示結束，但僅列至第 1 局；結束方式待確認，比分暫列"
    [post] = ss.cpbl_postseason(games, dt.datetime(2026, 10, 26, 6, tzinfo=TPE))["series"]
    assert [(r["game"], r["away_score"], r["home_score"]) for r in post["results"]] == [(5, 4, 3)], post
    assert post["next"]["game"] == 6 and post["next"]["when"] == "10/26 15:05"
    assert post["note"] == "" and dict(post["teams"]) == {"味全": 3, "中信": 2}


def test_twse_holiday_table_skips_closed_days_but_not_trading_markers():
    import tw_holidays
    rows = [{"Name": "臺灣光復暨金門古寧頭大捷紀念日", "Date": "1151026"},
            {"Name": "國曆新年開始交易日", "Date": "1150102"},
            {"Name": "市場無交易，僅辦理結算交割作業", "Date": "1150212"}, {"Date": "bad"}]
    assert tw_holidays.closed_dates(rows) == {"2026-10-26", "2026-02-12"}
    assert tw_holidays.closed_dates({"not": "a list"}) == frozenset()

    class _R:
        def json(self):
            return rows
    assert tw_holidays.target_session("2026-10-26", lambda *a, **k: _R()) == "2026-10-27"
    assert tw_holidays.target_session("2026-10-24", lambda *a, **k: _R()) == "2026-10-27"

    def _down(*a, **k):
        raise OSError("offline")
    assert tw_holidays.target_session("2026-10-26", _down) == "2026-10-26"   # 抓不到:退回只跳週末


def test_category_tail_does_not_hide_a_local_duplicate():
    import morning_report as mr
    a = "影／二林鎮農會龍情蕎薏產業推廣紫晶盃葡萄酒新酒發表- 社會新聞 - PChome Online 新聞"
    b = "影／二林鎮農會龍情蕎薏產業推廣 紫晶盃葡萄酒新酒發表 - 蕃新聞"
    assert mr._local_title_is_dup(b, [mr._local_seen_entry(a)])
    assert not mr._local_title_is_dup("二林鎮農會新酒發表延期 - 蕃新聞", [mr._local_seen_entry(a)])


def test_top_world_local_sources_link_to_the_original():
    """10-26 使用者:「有些新聞連結怎麼沒辦法點」—— 三大重點、世界大事、本地動態的來源是純文字。"""
    import legacy_layout
    pk = {"news": [{"source_item_id": "n1", "source_name": "中央社", "url": "https://x.com/a?b=1"},
                   {"source_item_id": "n2", "source_name": "UDN"}]}
    assert legacy_layout.source_of(pk, "n1") == "（[中央社](https://x.com/a?b=1)）"
    assert legacy_layout.source_of(pk, "n2") == "（UDN）"
    assert legacy_layout.source_of(pk, "") == ""



def test_quoted_source_links_survive_the_collected_url_allowlist():
    """PR #28 外審:href 經 quote 後 `story_(1)` 變 `story_%281%29`,白名單只有原始網址 → 連結被拿掉。"""
    import legacy_layout
    import render_utils
    url = "https://example.com/story_(1)?a=1&b=2"
    pk = {"news": [{"source_item_id": "n1", "source_name": "中央社", "url": url}]}
    md = "- 事件" + legacy_layout.source_of(pk, "n1")
    html = render_utils._md_to_html(md, trusted_urls=[url])
    assert 'href="https://example.com/story_%281%29?a=1&amp;b=2"' in html, html
    # 仍只收已收錄的網址
    assert "<a " not in render_utils._md_to_html(md, trusted_urls=["https://example.com/other"])


def test_the_evidence_packet_uses_the_holiday_adjusted_target(monkeypatch):
    """PR #28 外審:預測卡用 10/27,證據包卻自己重算成 10/26(休市日)→ 模型與昨日觀點對錯日。"""
    import morning_report as mr
    seen = {}

    def fake_build(*args, **kwargs):
        seen["target"] = kwargs.get("target_session_date")
        raise RuntimeError("stop after capturing the target")
    monkeypatch.setattr(mr._ep, "build", fake_build)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(mr, "_prompt_profile_for", lambda *a: "luna56_xhigh_v1")
    try:
        mr._call_llm_analysis_impl({"TARGET_SESSION": "2026-10-27"}, {}, {}, [], [], {})
    except Exception:   # noqa: BLE001 - 只看 build 拿到的日期
        pass
    assert seen.get("target") == "2026-10-27", seen
