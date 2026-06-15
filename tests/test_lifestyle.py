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
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def fake_get(url, params=None, timeout=None, **k):
        return R({"events": [{
            "shortName": "Boss Open",
            "status": {"type": {"shortDetail": "Final"}},
            "groupings": [{
                "grouping": {"slug": "mens-singles"},
                "competitions": [{
                    "id": "c1", "date": "2026-06-14T12:00Z",
                    "status": {"type": {"completed": True}},
                    "competitors": [
                        {"athlete": {"shortName": "A. Player"}, "winner": True},
                        {"athlete": {"shortName": "B. Loser"}, "winner": False}]}]}],
        }]})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    out = mr.fetch_tennis_digest(dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE))
    assert any(t["name"] == "Boss Open" for t in out["tournaments"])
    # 兩端點都回同一場 → 用 competition id 去重,只算一次
    matches = [r for r in out["results"] if r["winner"] == "A. Player"]
    assert len(matches) == 1 and matches[0]["tour"] == "ATP"


def test_render_sports_cpbl_scores():
    sports = {"news": {}, "cpbl_scores": [
        {"away": "統一", "home": "味全", "away_score": 5, "home_score": 3,
         "winner": "away", "date": "06/14"}]}
    h = mr._render_sports_html(sports, htmllib)
    assert "中華職棒 最新賽果" in h
    assert "統一 5" in h and "味全 3" in h
    assert "<b style='color:#b91c1c;'>統一 5</b>" in h     # 勝方加粗標紅


def test_render_sports_cpbl_scores_escapes_team_name():
    """隊名含標記字元時必須 HTML 跳脫,不可注入。"""
    sports = {"news": {}, "cpbl_scores": [
        {"away": "<b>X</b>", "home": "味全", "away_score": 1, "home_score": 2,
         "winner": "home", "date": "06/14"}]}
    h = mr._render_sports_html(sports, htmllib)
    assert "&lt;b&gt;X&lt;/b&gt;" in h and "<b>X</b> 1" not in h


def test_fetch_cpbl_scores(monkeypatch):
    """Yahoo 運動 API:解析昨日比分、隊名對照、只取完賽、勝方由比分判定。"""
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    payload = {"service": {"scoreboard": {
        "games": {
            "cpbl.g.1": {"status_type": "status.type.final",
                         "start_time": "Sun, 14 Jun 2026 08:05:00 +0000",  # 台北 16:05
                         "away_team_id": "cpbl.t.2", "home_team_id": "cpbl.t.7",
                         "total_away_points": "5", "total_home_points": "3"},
            "cpbl.g.2": {"status_type": "status.type.inprogress",  # 未完賽 → 略過
                         "away_team_id": "cpbl.t.1", "home_team_id": "cpbl.t.3",
                         "total_away_points": "1", "total_home_points": "0"},
            "cpbl.g.3": {"status_type": "status.type.final",       # 比分缺值 → 略過
                         "away_team_id": "cpbl.t.2", "home_team_id": "cpbl.t.7",
                         "total_away_points": None, "total_home_points": "2"},
        },
        "teams": {"cpbl.t.2": {"display_name": "統一"},
                  "cpbl.t.7": {"display_name": "味全"}},
    }}}

    def fake_get(url, params=None, timeout=None, headers=None, **k):
        return R(payload)
    monkeypatch.setattr(mr.requests, "get", fake_get)
    now = dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE)
    out = mr.fetch_cpbl_scores(now)
    # 昨日+今日兩次查詢回同一場 → 用 game id 去重;未完賽與缺比分都被濾掉
    assert len(out) == 1
    g = out[0]
    assert g["away"] == "統一" and g["home"] == "味全"
    assert g["away_score"] == 5 and g["home_score"] == 3 and g["winner"] == "away"
    assert g["date"] == "06/14"          # 由 start_time 換算台北,非查詢日期桶


def test_fetch_cpbl_scores_missing_in_one_bucket_recovered(monkeypatch):
    """某查詢桶缺比分的同一場,不可擋掉另一桶有效的版本(seen 應在驗證後才標記)。"""
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def _payload(away_pts):
        return {"service": {"scoreboard": {
            "games": {"cpbl.g.9": {
                "status_type": "status.type.final",
                "start_time": "Sun, 14 Jun 2026 08:05:00 +0000",
                "away_team_id": "cpbl.t.2", "home_team_id": "cpbl.t.7",
                "total_away_points": away_pts, "total_home_points": "1"}},
            "teams": {"cpbl.t.2": {"display_name": "統一"},
                      "cpbl.t.7": {"display_name": "味全"}}}}}

    def fake_get(url, params=None, timeout=None, headers=None, **k):
        # 昨日桶缺比分(None);今日桶有有效比分
        return R(_payload(None if (params or {}).get("date") == "2026-06-14" else "4"))
    monkeypatch.setattr(mr.requests, "get", fake_get)
    now = dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE)
    out = mr.fetch_cpbl_scores(now)
    assert len(out) == 1 and out[0]["away_score"] == 4 and out[0]["winner"] == "away"


def test_supply_chain_2330_tag():
    assert "對2330供應鏈" in mr._supply_chain_2330_tag("NVDA")
    assert "對2330供應鏈" in mr._supply_chain_2330_tag("ASML")
    assert "對2330供應鏈" in mr._supply_chain_2330_tag("ARM")
    assert mr._supply_chain_2330_tag("MU") == ""      # MU 不在顯示集
    assert mr._supply_chain_2330_tag("") == ""
    assert mr._supply_chain_2330_tag(None) == ""


def test_supply_chain_tag_does_not_touch_scoring_map():
    """顯示標籤用獨立集合,計分用的 TW_SUPPLY_CHAIN_BY_US_LABEL 不得被新增條目污染。"""
    scoring = mr.TW_SUPPLY_CHAIN_BY_US_LABEL
    # 純顯示新增的這些,絕不可出現在計分 map(否則等於未回測就改模型)
    for t in ("QCOM", "MRVL", "AMAT", "ARM"):
        assert t not in scoring
        assert "對2330供應鏈" in mr._supply_chain_2330_tag(t)   # 但顯示標籤照樣有
    # 計分 map 維持原本 6 個 key
    assert set(scoring) == {"NVDA", "AMD", "AVGO", "MU", "ASML", "AAPL"}


def test_other_sector_queries_precision():
    """生技收斂到個股+催化、金融偏壽險投資收益;且仍維持 8 個類股鍵。"""
    q = mr.OTHER_SECTOR_QUERIES
    assert len(q) == 8
    assert "藥華藥" in q["生技-台股"] and "臨床" in q["生技-台股"]
    assert "生技股" not in q["生技-台股"]              # 去掉過寬關鍵字
    assert "投資收益" in q["金融-台股"] or "淨息差" in q["金融-台股"]


def test_tennis_tier_classification():
    assert mr._tennis_tier("Wimbledon")[0] == 0
    assert mr._tennis_tier("Roland Garros")[0] == 0
    assert mr._tennis_tier("US Open")[0] == 0
    assert mr._tennis_tier("Madrid Open")[0] == 1
    assert mr._tennis_tier("Cincinnati")[0] == 1
    assert mr._tennis_tier("Stuttgart Open")[0] == 2   # ATP250
    assert mr._tennis_tier("")[0] == 2


def test_fetch_tennis_grand_slam_first(monkeypatch):
    """大滿貫結果排在非大滿貫之前(即使日期較舊)。"""
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def _m(cid, w, day):
        return {"id": cid, "date": f"2026-06-{day}T12:00Z",
                "status": {"type": {"completed": True}},
                "competitors": [{"athlete": {"shortName": w}, "winner": True},
                                {"athlete": {"shortName": "L"}, "winner": False}]}

    def fake_get(url, params=None, timeout=None, **k):
        if "/atp/" in url:
            return R({"events": [
                {"shortName": "Stuttgart Open", "status": {"type": {}},   # ATP250,日期較新
                 "groupings": [{"grouping": {"slug": "mens-singles"},
                                "competitions": [_m("s1", "ATP250win", 14)]}]},
                {"shortName": "Wimbledon", "status": {"type": {}},        # 大滿貫,日期較舊
                 "groupings": [{"grouping": {"slug": "mens-singles"},
                                "competitions": [_m("w1", "SLAMwin", 11)]}]},
            ]})
        return R({"events": []})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    out = mr.fetch_tennis_digest(dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE))
    winners = [r["winner"] for r in out["results"]]
    assert winners and winners[0] == "SLAMwin"          # 大滿貫優先於較新的 ATP250
    slam = next(r for r in out["results"] if r["winner"] == "SLAMwin")
    assert slam["tier"] == "大滿貫"


def test_fetch_worldcup_sorts_group_by_standings(monkeypatch):
    """ESPN entries 非名次序時,fetch_worldcup 須自行依積分→淨勝分→進球數排序。"""
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def _entry(name, pts, gd, gf):
        return {"team": {"displayName": name}, "stats": [
            {"name": "gamesPlayed", "value": 1}, {"name": "wins", "value": 0},
            {"name": "ties", "value": 0}, {"name": "losses", "value": 0},
            {"name": "points", "value": pts},
            {"name": "pointDifferential", "value": gd},
            {"name": "pointsFor", "value": gf}]}

    standings = {"children": [{"name": "Group A", "standings": {"entries": [
        _entry("South Africa", 0, -2, 0),   # ESPN 亂序:0 分卻排第一
        _entry("Mexico", 3, 1, 2),          # 3 分、淨勝 +1
        _entry("South Korea", 3, 3, 4),     # 3 分、淨勝 +3(同分應排 Mexico 之前)
        _entry("Czechia", 0, -2, 1)]}}]}

    def fake_get(url, params=None, timeout=None, **k):
        return R(standings if "standings" in url else {"events": []})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    wc = mr.fetch_worldcup(dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE))
    teams = [r["team"] for r in wc["groups"][0]["rows"]]
    # 3分組內南韓淨勝+3 > 墨西哥+1;0分組內捷克進球1 > 南非進球0
    assert teams == ["南韓", "墨西哥", "捷克", "南非"]
    assert teams[:2] == ["南韓", "墨西哥"]               # 前 2 名(晉級線)正確


def test_format_macro_line_prev_value():
    """總經行須帶前值以防 LLM 編造;缺漲跌幅時不可捏造前值==現值。"""
    # 提供 prev_close → 直接用
    assert "前值 19.43" in mr._format_macro_line("VIX", {"close": 17.68, "prev_close": 19.43, "change_pct": -9.05})
    # 無 prev_close、有 change_pct → 反推(17.68 / (1-0.0905) ≈ 19.44)
    line = mr._format_macro_line("VIX", {"close": 17.68, "change_pct": -9.05})
    assert "前值 19.4" in line and "-9.05%" in line
    # 無 change_pct → 不捏造前值、標漲跌不明
    line2 = mr._format_macro_line("X", {"close": 100.0})
    assert "前值" not in line2 and "漲跌不明" in line2
    # -100%(歸零)→ 不除以零、不顯示前值
    line3 = mr._format_macro_line("Z", {"close": 0.0, "change_pct": -100})
    assert line3 == "Z=資料缺失" or "前值" not in line3
    # 資料缺失
    assert mr._format_macro_line("Y", {"error": "x"}) == "Y=資料缺失"


def test_render_sports_cpbl_source_note():
    base = {"rank": 1, "team": "統一", "games": "50", "wdl": "30-0-20",
            "pct": "0.600", "gb": "-"}
    wiki = mr._render_sports_html({"news": {}, "cpbl": [base],
                                   "cpbl_source": "Wikipedia 備援"}, htmllib)
    assert "Wikipedia 備援" in wiki and "可能稍有遲滯" in wiki
    official = mr._render_sports_html({"news": {}, "cpbl": [base],
                                       "cpbl_source": "官網"}, htmllib)
    assert "Wikipedia 備援" not in official              # 官網來源不顯示備援警語


def test_cap_analysis_text():
    short = "第一段。\n\n第二段。"
    assert mr._cap_analysis_text(short, max_chars=999) == short   # 短的不動
    long = "\n\n".join(f"段落{i}内容文字" * 50 for i in range(20))
    capped = mr._cap_analysis_text(long, max_chars=400)
    assert len(capped) < len(long) and "已截斷" in capped


def test_estimated_email_kb_accounts_for_encoding():
    kb = mr._estimated_email_kb("a" * 1024)
    assert 1.3 < kb < 1.45         # ×1.37 / 1024


def test_render_worldcup_marks_advancing_top2():
    """分組表前 2 名以綠色標示(晉級區),第 3 名以後不標。"""
    sports = {"news": {}, "worldcup": {"results": [], "fixtures": [], "groups": [
        {"name": "A 組", "rows": [
            {"team": "巴西", "gp": 3, "w": 3, "d": 0, "l": 0, "pts": 9},
            {"team": "美國", "gp": 3, "w": 2, "d": 0, "l": 1, "pts": 6},
            {"team": "越南", "gp": 3, "w": 0, "d": 0, "l": 3, "pts": 0},
        ]}]}}
    h = mr._render_sports_html(sports, htmllib)
    assert "晉級區" in h
    assert "<b style='color:#16a34a;'>巴西 9(3-0-0)</b>" in h   # 第1名綠字
    assert "<b style='color:#16a34a;'>美國 6(2-0-1)</b>" in h   # 第2名綠字
    assert "<b style='color:#16a34a;'>越南" not in h            # 第3名不標綠


def test_fetch_nba_favorite_games(monkeypatch):
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    payload = {"events": [{
        "status": {"type": {"completed": True}},
        "competitions": [{
            "notes": [{"headline": "NBA Finals Game 5"}],
            "competitors": [
                {"homeAway": "away", "score": "110", "winner": True,
                 "team": {"displayName": "Boston Celtics", "abbreviation": "BOS"}},
                {"homeAway": "home", "score": "105",
                 "team": {"displayName": "New York Knicks", "abbreviation": "NYK"}}]}]}]}

    monkeypatch.setattr(mr.requests, "get",
                        lambda url, params=None, timeout=None, **k: R(payload))
    out = mr.fetch_nba_favorite_games(
        dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE), ["celtics"])
    assert len(out) == 1
    assert "BOS" in out[0]["text"] and "110:105" in out[0]["text"]
    assert out[0]["note"] == "NBA Finals Game 5"


def test_nba_favorite_no_substring_false_positive():
    """'den' 不可誤中 'Golden State';隊名/比分含標記字元須跳脫。"""
    gsw = {"homeAway": "away", "score": "<x>", "winner": True,
           "team": {"displayName": "Golden State Warriors", "abbreviation": "<i>GSW</i>"}}
    lal = {"homeAway": "home", "score": "100",
           "team": {"displayName": "Los Angeles Lakers", "abbreviation": "LAL"}}
    assert mr._nba_team_matches_favorite(gsw, "den") is False        # 不誤中 golDEN
    assert mr._nba_team_matches_favorite(gsw, "warriors") is True
    assert mr._nba_team_matches_favorite(gsw, "golden state") is True  # 多字串比對
    assert mr._nba_team_matches_favorite(lal, "lakers") is True


def test_fetch_nba_favorite_escapes_and_dedupes(monkeypatch):
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    # 一場含「兩支關注隊」對戰 + 惡意標記字元
    payload = {"events": [{
        "id": "g1", "status": {"type": {"completed": True}},
        "competitions": [{"notes": [],
            "competitors": [
                {"homeAway": "away", "score": "<b>9</b>", "winner": True,
                 "team": {"displayName": "Boston Celtics", "abbreviation": "<x>"}},
                {"homeAway": "home", "score": "100",
                 "team": {"displayName": "Los Angeles Lakers", "abbreviation": "LAL"}}]}]}]}
    monkeypatch.setattr(mr.requests, "get",
                        lambda url, params=None, timeout=None, **k: R(payload))
    out = mr.fetch_nba_favorite_games(
        dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE), ["celtics", "lakers"])
    assert len(out) == 1                       # 兩隊同場 → 只列一次(去重)
    assert "&lt;x&gt;" in out[0]["text"] and "<x>" not in out[0]["text"]   # 隊名跳脫
    assert "&lt;b&gt;9&lt;/b&gt;" in out[0]["text"]                        # 比分跳脫


def test_render_nba_favorite_block():
    sports = {"news": {}, "nba_fav": [
        {"text": "<b>BOS</b> 110:105 NYK", "date": "06/14", "note": "Finals G5"}]}
    h = mr._render_sports_html(sports, htmllib)
    assert "NBA 關注球隊近況" in h and "BOS" in h and "Finals G5" in h


def test_nba_favorite_teams_env(monkeypatch):
    monkeypatch.delenv("NBA_FAVORITE_TEAMS", raising=False)
    assert mr._nba_favorite_teams() == []
    monkeypatch.setenv("NBA_FAVORITE_TEAMS", "Celtics, Lakers ,")
    assert mr._nba_favorite_teams() == ["celtics", "lakers"]


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
        {"cpbl_scores": [{"date": yday}]}, [], {}, [], now) is True             # 昨日中職比分
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


def test_fetch_worldcup_fixtures_filters_to_tpe_day(monkeypatch):
    """賽程只留台北今天/明天的場次,且開球時間換算成台北時區。"""
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    now = dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE)
    today_match = {"id": "g1", "date": "2026-06-15T05:00Z",  # 台北 13:00 = 今天
                   "status": {"type": {"completed": False, "state": "pre"}},
                   "competitions": [{"competitors": [
                       {"team": {"displayName": "Brazil"}},
                       {"team": {"displayName": "United States"}}]}]}
    far_match = {"id": "g2", "date": "2026-06-20T05:00Z",     # 5 天後 → 應被濾掉
                 "status": {"type": {"completed": False, "state": "pre"}},
                 "competitions": [{"competitors": [
                     {"team": {"displayName": "France"}},
                     {"team": {"displayName": "Spain"}}]}]}

    def fake_get(url, params=None, timeout=None, **k):
        if "standings" in url:
            return R({"children": []})
        return R({"events": [today_match, far_match]})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    wc = mr.fetch_worldcup(now)
    fx = wc["fixtures"]
    assert len(fx) == 1                                   # 只留今天那場;5 天後被濾掉
    assert fx[0]["text"] == "巴西 vs 美國"
    assert fx[0]["kickoff"] == "06/15 13:00"             # UTC→台北


def test_fetch_tennis_orders_latest_first_per_tour(monkeypatch):
    """各 tour 取最近 3 場、最新在前;性別以 slug 分類;competition id 跨端點去重;雙打略過。"""
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def _match(cid, winner, day):
        return {"id": cid, "date": f"2026-06-{day}T12:00Z",
                "status": {"type": {"completed": True}},
                "competitors": [{"athlete": {"shortName": winner}, "winner": True},
                                {"athlete": {"shortName": "Loser"}, "winner": False}]}

    def fake_get(url, params=None, timeout=None, **k):
        if "/atp/" in url:
            return R({"events": [{"shortName": "ATP Cup", "status": {"type": {}},
                                  "groupings": [
                # 男單四場(舊→新)
                {"grouping": {"slug": "mens-singles"},
                 "competitions": [_match(f"m{d}", f"ATP{d}", d) for d in (10, 11, 12, 13)]},
                # atp 端點夾帶的女單(應標 WTA,非 ATP)
                {"grouping": {"slug": "womens-singles"},
                 "competitions": [_match("w20", "WTA20", 12)]},
                # 雙打應略過
                {"grouping": {"slug": "mens-doubles"},
                 "competitions": [_match("d1", "DOUBLES", 13)]},
            ]}]})
        return R({"events": [{"shortName": "WTA Cup", "status": {"type": {}},
                              "groupings": [{
                                  "grouping": {"slug": "womens-singles"},
                                  "competitions": [
                                      _match("w20", "WTA20", 12),    # 與 atp 端點重複 → 去重
                                      _match("w22", "WTA22", 11)]}]}]})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    out = mr.fetch_tennis_digest(dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE))
    winners = [r["winner"] for r in out["results"]]
    assert winners[0] == "ATP13"                          # 最新在前
    assert "WTA20" in winners                             # atp 端點的女單被正確標為 WTA
    assert winners.count("WTA20") == 1                    # 跨端點同一場去重
    assert "ATP10" not in winners                         # 各 tour 上限 3,最舊被擠掉
    assert "DOUBLES" not in winners                       # 雙打略過
    assert sum(1 for w in winners if w.startswith("ATP")) <= 3
    # tour 標籤正確
    assert all(r["tour"] == ("WTA" if r["winner"].startswith("WTA") else "ATP")
               for r in out["results"])


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


def test_event_category_collapses_fomc_variants():
    """規則式 FOMC 與 ForexFactory 各種 FOMC 寫法歸同類;ECB 不被誤歸 FOMC;
    CPI 與 Core CPI 不被合併。"""
    assert mr._event_category("FOMC 利率決策(台北時間隔日凌晨 2:00 公布)") == "FOMC"
    assert mr._event_category("[USD] FOMC Statement") == "FOMC"
    assert mr._event_category("[USD] Federal Funds Rate") == "FOMC"
    assert mr._event_category("[EUR] ECB Interest Rate Decision") != "FOMC"   # 泛用利率決策不歸 FOMC
    assert mr._event_category("[USD] CPI m/m") != mr._event_category("[USD] Core CPI m/m")
    # 不同國別的同名數據不可塌成一筆(保留國別前綴)
    assert mr._event_category("[USD] CPI m/m") != mr._event_category("[EUR] CPI m/m")


def test_fetch_event_calendar_dedupes_fomc_across_sources(monkeypatch):
    import datetime as dt

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            # 真實情況:FOMC 決策於美東 14:00(夏令 -04:00)公布,換算台北已是「隔日」凌晨。
            # 規則式 FOMC 用美國會議日 6/17,FF 落在台北時間 6/18 → 日期不同,須靠類別收斂。
            day = "2026-06-17T14:00:00-04:00"   # 美東 14:00 = 台北 6/18 02:00
            return [
                {"impact": "High", "country": "USD", "title": "FOMC Statement",
                 "date": day, "forecast": "", "previous": ""},
                {"impact": "High", "country": "USD", "title": "Federal Funds Rate",
                 "date": day, "forecast": "4.50%", "previous": "4.50%"},
                {"impact": "High", "country": "USD", "title": "FOMC Press Conference",
                 "date": day, "forecast": "", "previous": ""},
            ]

    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp())
    # 不打 yfinance 財報
    monkeypatch.setattr(mr.yf, "Ticker", lambda *a, **k: type("T", (), {"calendar": {}})())
    now = dt.datetime(2026, 6, 15, 8, tzinfo=mr.TPE)
    events = mr.fetch_event_calendar(now, horizon_days=7)
    fomc = [e for e in events if mr._event_category(e["title"]) == "FOMC"]
    assert len(fomc) == 1                              # 跨日期 + 三來源 → 收斂成一筆
    assert "FOMC 利率決策" in fomc[0]["title"]          # 保留中文時區說明的規則式那筆
    assert fomc[0]["date"] == dt.date(2026, 6, 17)     # 保留規則式美國會議日
    # 不漏資訊:規則式 note 仍在,且併入 FF 的預期/前值
    assert "決策日前後" in fomc[0]["note"]
    assert "預期 4.50%" in fomc[0]["note"]


def test_event_structural_dedup_keeps_distant_same_category():
    """同類別但相隔較遠(較長 horizon 下不同月份的結算日)不可被塌成一筆。"""
    import datetime as dt
    events = [
        {"date": dt.date(2026, 6, 17), "title": "台指期/選擇權結算日", "note": "a"},
        {"date": dt.date(2026, 7, 15), "title": "台指期/選擇權結算日", "note": "b"},
    ]
    # 相差 28 天 > 1 → 兩筆都保留
    assert mr._event_category(events[0]["title"]) == mr._event_category(events[1]["title"]) == "TW_SETTLE"
    out = mr._dedupe_calendar_events(events)
    assert len(out) == 2
    # 但同類別相差 ≤1 天(規則式美國會議日 vs FF 台北日)→ 收斂成一筆
    near = mr._dedupe_calendar_events([
        {"date": dt.date(2026, 6, 17), "title": "FOMC 利率決策", "note": "x"},
        {"date": dt.date(2026, 6, 18), "title": "[USD] Federal Funds Rate", "note": "預期 4.5%"},
    ])
    assert len(near) == 1 and near[0]["date"] == dt.date(2026, 6, 17)


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
