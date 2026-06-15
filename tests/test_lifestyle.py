"""天氣卡 / ETF 進出參考 / 體育快訊 渲染測試。"""
import html as htmllib

import morning_report as mr


def test_weather_advice_rules():
    hot_rain = [{"name": "彰化市", "t_min": 26, "t_max": 33, "rain_prob": 85, "label": "陣雨"}]
    advice = mr._weather_advice(hot_rain)
    assert "短袖" in advice and "帶傘" in advice
    cool_dry = [{"name": "彰化市", "t_min": 14, "t_max": 20, "rain_prob": 10, "label": "晴朗"}]
    advice2 = mr._weather_advice(cool_dry)
    assert "外套" in advice2 and "不太需要帶傘" in advice2


def test_render_weather_html():
    locs = [{"name": "彰化市", "t_min": 24, "t_max": 29, "rain_prob": 85, "label": "陣雨"},
            {"name": "台中北區", "t_min": 24, "t_max": 29, "rain_prob": 100, "label": "陣雨"}]
    h = mr._render_weather_html(locs)
    assert "早安" in h and "彰化市" in h and "台中北區" in h
    assert "降雨 100%" in h and "帶傘" in h
    assert mr._render_weather_html([]) == ""   # 失敗時整卡消失,不留空殼


def test_render_etf_action_card():
    h = mr._render_etf_action_card(120.87, 100.45)
    assert "ETF 今日進出參考價" in h
    assert "120.27" in h and "121.47" in h     # 00662 ±0.5%
    assert "99.45" in h and "101.45" in h      # 0050 ±1.0%
    # 手機版堆疊文案
    assert "可分批買" in h and "偏貴" in h and "觀望" in h
    assert mr._render_etf_action_card(None, None) == ""


def test_render_sports_html():
    sports = {
        "cpbl": [{"rank": 1, "team": "味全龍", "games": "49", "wdl": "33-0-16",
                  "pct": "0.673", "gb": "-"}],
        "nba": [{"text": "SA 106:<b>107 NY</b>", "series": "NY leads series 3-1",
                 "note": "NBA Finals - Game 4", "date": "06/10"}],
        "standings": {"美聯": [{"team": "TB", "record": "40-25"}]},
        "news": {"中華職棒": ["兄弟逆轉勝 悍將吞三連敗"], "網球": []},
    }
    h = mr._render_sports_html(sports, htmllib)
    assert "體育快訊" in h
    assert "中華職棒戰績" in h and "味全龍" in h and "33-0-16" in h
    assert "NBA 冠軍賽" in h and "NY leads series 3-1" in h
    assert "MLB 戰績前三" in h and "TB 40-25" in h
    assert "MLB 昨日比分" not in h          # 使用者要求移除逐場比分
    assert "兄弟逆轉勝" in h
    assert mr._render_sports_html({}, htmllib) == ""


def test_render_sports_worldcup_block():
    """世足:近期戰績 + 今日賽程 + 分組累計戰績(收合成一行/組)。"""
    sports = {
        "worldcup": {
            "results": [{"text": "美國 4 : 1 巴拉圭", "status": "FT", "date": "06/13"}],
            "fixtures": [{"text": "西班牙 vs 維德角", "kickoff": "06/16 00:00", "round": ""}],
            "groups": [{"name": "A 組", "rows": [
                {"team": "巴西", "gp": 2, "w": 2, "d": 0, "l": 0, "pts": 6},
                {"team": "喀麥隆", "gp": 2, "w": 0, "d": 1, "l": 1, "pts": 1},
            ]}],
        },
        "news": {"世足": ["世界盃32強賽程出爐"]},
    }
    h = mr._render_sports_html(sports, htmllib)
    assert "世足 / MLB" in h                      # 區塊標題已含世足
    assert "世界盃足球賽" in h
    assert "近期戰績" in h and "美國 4 : 1 巴拉圭" in h
    assert "今日/近日賽程" in h and "西班牙 vs 維德角" in h and "06/16 00:00" in h
    assert "分組累計戰績" in h and "A 組" in h
    assert "巴西 6(2-0-0)" in h and "喀麥隆 1(0-1-1)" in h   # 收合成一行/組
    assert "世界盃32強賽程出爐" in h


def test_render_sports_mlb_tw_and_tennis():
    sports = {
        "news": {},
        "mlb_tw": [{"name": "鄧愷威", "en": "Kai-Wei Teng", "role": "投手",
                    "date": "06/14", "summary": "5.0 IP, 1 ER, 6 K"}],
        "tennis": {"tournaments": [{"tour": "ATP", "name": "Boss Open", "status": "Final"}],
                   "results": [{"tour": "ATP", "winner": "A. Player",
                                "loser": "B. Loser", "event": "Boss Open"}]},
    }
    h = mr._render_sports_html(sports, htmllib)
    assert "MLB 台灣旅外球員" in h and "鄧愷威" in h and "5.0 IP, 1 ER, 6 K" in h
    assert "網球 ATP / WTA" in h and "Boss Open" in h
    assert "A. Player" in h and "勝" in h


def test_fetch_mlb_taiwan_players(monkeypatch):
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    monkeypatch.setenv("MLB_TW_PLAYERS", "Kai-Wei Teng:鄧愷威")
    now = dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE)

    def fake_get(url, params=None, timeout=None, **k):
        params = params or {}
        if "search" in url:
            return R({"people": [{"id": 678906, "fullName": "Kai-Wei Teng"}]})
        if "/stats" in url:
            if params.get("group") == "pitching":
                return R({"stats": [{"splits": [
                    {"date": "2026-06-14", "stat": {"summary": "5.0 IP, 1 ER, 6 K"}}]}]})
            return R({"stats": [{"splits": []}]})
        return R({})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    out = mr.fetch_mlb_taiwan_players(now)
    assert len(out) == 1
    assert out[0]["name"] == "鄧愷威" and out[0]["role"] == "投手"
    assert out[0]["date"] == "06/14" and "6 K" in out[0]["summary"]

    # 超過 7 天未出賽 → 略過(不顯示過舊資料)
    def fake_old(url, params=None, timeout=None, **k):
        params = params or {}
        if "search" in url:
            return R({"people": [{"id": 1, "fullName": "X"}]})
        if "/stats" in url and params.get("group") == "pitching":
            return R({"stats": [{"splits": [
                {"date": "2026-06-01", "stat": {"summary": "old"}}]}]})
        return R({"stats": [{"splits": []}]})
    monkeypatch.setattr(mr.requests, "get", fake_old)
    assert mr.fetch_mlb_taiwan_players(now) == []


def test_fetch_tennis_digest(monkeypatch):
    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def fake_get(url, timeout=None, **k):
        return R({"events": [{
            "shortName": "Boss Open",
            "status": {"type": {"shortDetail": "Final"}},
            "groupings": [{"competitions": [{
                "status": {"type": {"completed": True}},
                "competitors": [
                    {"athlete": {"shortName": "A. Player"}, "winner": True},
                    {"athlete": {"shortName": "B. Loser"}, "winner": False}]}]}],
        }]})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    out = mr.fetch_tennis_digest()
    assert any(t["name"] == "Boss Open" for t in out["tournaments"])
    assert any(r["winner"] == "A. Player" and r["loser"] == "B. Loser"
               for r in out["results"])


def test_wc_zh_mapping_and_fallback():
    assert mr._wc_zh("United States") == "美國"
    assert mr._wc_zh("Brazil") == "巴西"
    assert mr._wc_zh("Korea Republic") == "南韓"
    assert mr._wc_zh("Atlantis") == "Atlantis"   # 查無對照保留原名,不漏資料
    assert mr._wc_zh("") == ""


def test_weekend_digest_content_gate():
    """週日輕量信只在『週六信之後才新增』的內容時才寄,避免與週六信重複。"""
    import datetime as dt
    now = dt.datetime(2026, 6, 14, 8, 0, tzinfo=mr.TPE)   # 週日早上
    yday = (now - dt.timedelta(days=1)).strftime("%m/%d")
    fresh = (now - dt.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    stale = (now - dt.timedelta(days=10)).strftime("%Y-%m-%d %H:%M")

    # 新內容 → 寄
    assert mr._weekend_digest_has_content(
        {"worldcup": {"results": [1]}}, [], {}, [], now) is True       # 世足昨日完賽
    assert mr._weekend_digest_has_content({}, [{"x": 1}], {}, [], now) is True  # 未顯示過的 podcast
    assert mr._weekend_digest_has_content(
        {"nba": [{"date": yday, "text": "x"}]}, [], {}, [], now) is True        # 昨日 NBA
    assert mr._weekend_digest_has_content(
        {}, [], {"policy": [{"published": fresh}]}, [], now) is True            # 近 30h 政策
    assert mr._weekend_digest_has_content(
        {}, [], {"medical": [{"published": fresh}]}, [], now) is True           # 近 30h 醫界

    # 舊內容/純版面內容 → 不寄(避免與週六信重複)
    assert mr._weekend_digest_has_content(
        {"nba": [{"date": "06/09", "text": "x"}]}, [], {}, [], now) is False    # 5 天前 NBA 非新
    assert mr._weekend_digest_has_content(
        {}, [], {"policy": [{"published": stale}]}, [], now) is False           # 10 天前政策
    assert mr._weekend_digest_has_content(
        {"cpbl": [1, 2], "standings": {"美聯": [1]}}, [], {}, [], now) is False  # 純戰績表
    assert mr._weekend_digest_has_content({}, [], {}, [1], now) is False        # 文獻不單獨觸發
    assert mr._weekend_digest_has_content({}, [], {}, [], now) is False


def test_weekend_gate_policy_excludes_pre_saturday_items():
    """政策/醫界用 24h 窗 ≈『上一封信之後才出刊』,週六信之前(>24h)的不再觸發。"""
    import datetime as dt
    sun = dt.datetime(2026, 6, 14, 6, 10, tzinfo=mr.TPE)            # 週日早上發信
    after_sat_report = (sun - dt.timedelta(hours=20)).strftime("%Y-%m-%d %H:%M")  # 週六上午之後
    before_sat_report = (sun - dt.timedelta(hours=26)).strftime("%Y-%m-%d %H:%M")  # 週六信之前
    assert mr._weekend_digest_has_content(
        {}, [], {"policy": [{"published": after_sat_report}]}, [], sun) is True
    assert mr._weekend_digest_has_content(
        {}, [], {"policy": [{"published": before_sat_report}]}, [], sun) is False


def test_published_within_hours():
    import datetime as dt
    now = dt.datetime(2026, 6, 14, 8, 0, tzinfo=mr.TPE)
    assert mr._published_within_hours("2026-06-14 06:00", 30, now) is True
    assert mr._published_within_hours("2026-06-13 05:00", 30, now) is True
    assert mr._published_within_hours("2026-06-12 06:00", 30, now) is False   # >30h
    assert mr._published_within_hours("2026-06-14", 30, now) is True          # 純日期
    assert mr._published_within_hours("", 30, now) is False
    assert mr._published_within_hours("not-a-date", 30, now) is False         # 無法解析→False


def test_fetch_worldcup_parses_espn(monkeypatch):
    """以 mock 的 ESPN JSON 驗證 scoreboard/standings 解析(防 schema/端點漂移)。"""
    import datetime as dt

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    scoreboard = {"events": [{
        "id": "401",
        "status": {"type": {"completed": True, "shortDetail": "FT"}},
        "competitions": [{"competitors": [
            {"homeAway": "home", "score": "1", "team": {"displayName": "Brazil"}},
            {"homeAway": "away", "score": "2", "team": {"displayName": "United States"}},
        ]}],
    }]}
    standings = {"children": [{
        "name": "Group A",
        "standings": {"entries": [
            {"team": {"displayName": "United States"},
             "stats": [{"name": "gamesPlayed", "value": 2}, {"name": "wins", "value": 2},
                       {"name": "ties", "value": 0}, {"name": "losses", "value": 0},
                       {"name": "points", "value": 6}]},
            {"team": {"displayName": "Brazil"},
             "stats": [{"name": "gamesPlayed", "value": 2}, {"name": "wins", "value": 0},
                       {"name": "ties", "value": 1}, {"name": "losses", "value": 1},
                       {"name": "points", "value": 1}]},
        ]},
    }]}

    def fake_get(url, **kwargs):
        return _Resp(standings if "standings" in url else scoreboard)

    monkeypatch.setattr(mr.requests, "get", fake_get)
    now = dt.datetime(2026, 6, 14, 8, 0, tzinfo=mr.TPE)   # 賽期內
    wc = mr.fetch_worldcup(now)
    # 兩日 scoreboard 同一場(id 去重)→ 一筆;英文隊名中文化
    assert len(wc["results"]) == 1
    assert wc["results"][0]["text"] == "美國 2 : 1 巴西"
    assert wc["results"][0]["status"] == "FT"
    assert len(wc["groups"]) == 1 and wc["groups"][0]["name"] == "A 組"
    rows = wc["groups"][0]["rows"]
    assert rows[0]["team"] == "美國" and rows[0]["pts"] == 6 and rows[0]["w"] == 2
    assert rows[1]["team"] == "巴西" and rows[1]["d"] == 1


def _stub_weekend_sources(monkeypatch, *, podcast):
    """把週日綜合的抓取/渲染都換成輕量 stub,只測控制流。"""
    monkeypatch.setattr(mr, "fetch_weather", lambda: [])
    monkeypatch.setattr(mr, "fetch_sports_digest", lambda now: {})
    monkeypatch.setattr(mr, "load_podcast_digest", lambda: podcast)
    monkeypatch.setattr(mr, "fetch_tw_daily_intelligence", lambda now: {})
    monkeypatch.setattr(mr, "fetch_medical_journal_articles", lambda: [])
    monkeypatch.setattr(mr, "translate_journal_titles", lambda a: [])
    monkeypatch.setattr(mr, "fetch_event_calendar", lambda now: [])
    for fn in ("_render_weather_html", "_render_event_calendar_html"):
        monkeypatch.setattr(mr, fn, lambda *a, **k: "")
    for fn in ("_render_sports_html", "_render_podcast_html",
               "_render_tw_intelligence_html", "_render_journals_html"):
        monkeypatch.setattr(mr, fn, lambda *a, **k: "")
    monkeypatch.delenv("DRY_RUN", raising=False)


def test_run_weekend_digest_sends_without_history_pollution(monkeypatch):
    """有新 podcast → 寄信 + 標記已顯示 + 只 push podcast 狀態,絕不寫入預測歷史。

    回歸防護:週日筆記若寫進 history.json 會與週六的『週一預測』撞 target,被去重誤刪。
    """
    import datetime as dt
    events = []
    _stub_weekend_sources(monkeypatch, podcast=[{"show": "股癌", "guid": "ep1"}])
    monkeypatch.setattr(mr, "send_email", lambda *a: events.append("sent"))
    monkeypatch.setattr(mr, "mark_podcast_episodes_shown",
                        lambda eps: events.append(("marked", len(eps))))
    monkeypatch.setattr(mr, "save_history_state",
                        lambda *a, **k: events.append("history!"))   # 不該被呼叫
    monkeypatch.setattr(mr, "_git_commit_and_push_state",
                        lambda paths, msg: events.append(("push", list(paths))))

    rc = mr.run_weekend_digest(dt.datetime(2026, 6, 14, 6, 0, tzinfo=mr.TPE))

    assert rc == 0
    assert "sent" in events
    assert ("marked", 1) in events
    assert "history!" not in events                       # 關鍵:不污染預測歷史
    pushes = [e for e in events if isinstance(e, tuple) and e[0] == "push"]
    assert pushes and pushes[0][1] == [str(mr.PODCAST_DIGEST_FILE)]
    # 寄信必須早於標記/ push(at-least-once:寄成功才落狀態)
    assert events.index("sent") < events.index(("marked", 1))


def test_run_weekend_digest_skips_when_no_new_content(monkeypatch):
    """無新內容 → 不寄信、不動任何狀態。"""
    import datetime as dt
    events = []
    _stub_weekend_sources(monkeypatch, podcast=[])
    monkeypatch.setattr(mr, "send_email", lambda *a: events.append("sent"))
    monkeypatch.setattr(mr, "mark_podcast_episodes_shown",
                        lambda eps: events.append("marked"))
    monkeypatch.setattr(mr, "save_history_state", lambda *a, **k: events.append("history"))
    monkeypatch.setattr(mr, "_git_commit_and_push_state",
                        lambda *a, **k: events.append("push"))

    rc = mr.run_weekend_digest(dt.datetime(2026, 6, 14, 6, 0, tzinfo=mr.TPE))

    assert rc == 0 and events == []                       # 完全不動作


def test_fetch_worldcup_off_season_returns_empty(monkeypatch):
    """賽期外不呼叫 ESPN、回空(避免顯示上屆殘留戰績表)。"""
    import datetime as dt

    def boom(*a, **k):
        raise AssertionError("賽期外不應呼叫 ESPN")

    monkeypatch.setattr(mr.requests, "get", boom)
    off = dt.datetime(2026, 3, 1, 8, 0, tzinfo=mr.TPE)
    wc = mr.fetch_worldcup(off)
    assert wc == {"results": [], "groups": []}


def test_render_weekend_digest_html_shell():
    sports_html = mr._render_sports_html(
        {"worldcup": {"results": [{"text": "巴西 2 : 0 喀麥隆", "status": "FT",
                                   "date": "06/13"}], "groups": []}}, htmllib)
    h = mr.render_weekend_digest_html(
        "2026-06-14 (Sun)", "", sports_html, "", "", "", "")
    assert "週日綜合" in h and "WEEKEND DIGEST" in h
    assert "世界盃足球賽" in h and "巴西 2 : 0 喀麥隆" in h
    assert "本日不開盤" in h


def test_rule_based_events_settlement_and_witching():
    import datetime as dt
    # 2026-06 第三個週三 = 6/17(結算)、第三個週五 = 6/19(三巫,6 月適用)
    assert mr._third_weekday_of_month(2026, 6, 2) == dt.date(2026, 6, 17)
    events = mr._rule_based_events(dt.date(2026, 6, 12), horizon_days=7)
    titles = [e["title"] for e in events]
    assert any("台指期" in t for t in titles)
    assert any("三巫" in t for t in titles)
    # 7 月初(非季月)不該有三巫
    events_jul = mr._rule_based_events(dt.date(2026, 7, 1), horizon_days=7)
    assert not any("三巫" in e["title"] for e in events_jul)


def test_event_timeline_counts_days_and_expires(tmp_path, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE", tmp_path / "tl.json")
    ev = [{"event_type": "geopolitical", "entity": "伊朗", "title": "美伊衝突升溫"}]
    d1 = dt.datetime(2026, 6, 10, 6, tzinfo=mr.TPE)
    assert mr.update_event_timeline(ev, d1) == []     # 第 1 天不顯示(尚非連續劇)
    d2 = dt.datetime(2026, 6, 11, 6, tzinfo=mr.TPE)
    active = mr.update_event_timeline(ev, d2)
    assert active and active[0]["days"] == 2          # 第 2 天開始顯示
    # 4 天沒更新 → 退場
    d6 = dt.datetime(2026, 6, 15, 6, tzinfo=mr.TPE)
    assert mr.update_event_timeline([], d6) == []


def test_weekly_recap_html():
    history = [{"target_session_date": "2026-06-09", "pred_taiex": 44445.66,
                "actual_open_taiex": 43687.62, "weighted_final_2330": 2313.24,
                "actual_open_2330": 2305.0}]
    h = mr._render_weekly_recap_html(history)
    assert "本週預測回顧" in h and "-1.71%" in h and "-0.36%" in h
    assert mr._render_weekly_recap_html([]) == ""


def test_medical_entity_cap_one_per_day(monkeypatch):
    """同一機構(中榮)多角度報導,醫界區每天最多 1 條。"""
    class Feed:
        entries = [{
            "title": "神外住院遭停約 中榮研擬申覆",
            "link": "https://news.example.com/a",
            "published": "Tue, 02 Jun 2026 08:00:00 GMT",
        }, {
            "title": "廠商代刀風暴 中榮擬向醫師求償遭裁罰",
            "link": "https://news.example.com/b",
            "published": "Tue, 02 Jun 2026 09:00:00 GMT",
        }]

    import datetime as dt
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda *a, **k: Feed())
    out = mr.fetch_tw_daily_intelligence(
        dt.datetime(2026, 6, 3, 6, tzinfo=mr.TPE), per_kind_limit=8)
    titles = [item["title"] for item in out["medical"]]
    assert sum(1 for t in titles if "中榮" in t) <= 1
