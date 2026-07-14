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
    assert "MLB 戰績" in h and "TB 40-25" in h
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
            "shortName": "Boss Open", "date": "2026-06-14T10:00Z",
            "status": {"type": {"shortDetail": "2nd Round", "state": "in"}},
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
    # 進行中賽事列入「進行中/即將」清單(已完賽者不再列——賽果區已涵蓋)
    t = next(t for t in out["tournaments"] if t["name"] == "Boss Open")
    assert t["status"] == "進行中"
    # 兩端點都回同一場 → 用 competition id 去重,只算一次;賽果帶台北日期
    matches = [r for r in out["results"] if r["winner"] == "A. Player"]
    assert len(matches) == 1 and matches[0]["tour"] == "ATP"
    assert matches[0]["date"] == "06/14"


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
    """生技收斂到個股+催化、金融偏壽險投資收益;擴充到 8 類(核心四類雙軌 + 新增四類台股)。"""
    q = mr.OTHER_SECTOR_QUERIES
    assert len(q) == 12                               # 核心四類×2 + 傳產/營建/重電/觀光×1
    assert "藥華藥" in q["生技-台股"] and "臨床" in q["生技-台股"]
    assert "生技股" not in q["生技-台股"]              # 去掉過寬關鍵字
    assert "投資收益" in q["金融-台股"] or "淨息差" in q["金融-台股"]
    # 新增四類齊備,以台股在地事件為主
    for new in ("傳產-台股", "營建-台股", "重電-台股", "觀光-台股"):
        assert new in q and q[new]


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
    assert len(capped) < len(long)          # 截斷仍生效
    assert "已截斷" not in capped            # 截斷註解文字已依使用者要求移除(2026-07-14)


def test_estimated_email_kb_measures_decoded_html():
    """Gmail 截斷量的是解碼後 HTML 大小,不可再 ×1.37(那會過早誤判超標砍內容)。"""
    assert abs(mr._estimated_email_kb("a" * 1024) - 1.0) < 0.01      # 純 ASCII:1KB
    # 繁中每字 3 bytes:1024 字 ≈ 3KB(仍量解碼後 UTF-8,不乘編碼膨脹係數)
    assert abs(mr._estimated_email_kb("中" * 1024) - 3.0) < 0.05


def test_truncate_default_order_protects_podcast_and_sports():
    """預設犧牲序依使用者指定:政策先砍、Podcast/體育殿後。"""
    order = mr._truncate_order()
    assert order[0] == "policy" and order[1] == "medical"
    assert order[-1] == "podcast" and order[-2] == "sports"
    # 政策/醫界/醫學文獻/五檔 都排在 體育/Podcast 之前
    for k in ("policy", "medical", "journals", "top5"):
        assert order.index(k) < order.index("sports") < order.index("podcast")


def test_tw_intelligence_include_flags():
    intel = {"policy": [{"title": "政策A", "published": "2026-06-15", "link": "#"}],
             "medical": [{"title": "醫界B", "published": "2026-06-15", "link": "#"}]}
    import html as htmllib
    both = mr._render_tw_intelligence_html(intel, htmllib)
    assert "政策A" in both and "醫界B" in both
    no_policy = mr._render_tw_intelligence_html(intel, htmllib, include_policy=False)
    assert "政策A" not in no_policy and "醫界B" in no_policy        # 砍政策不影響醫界
    assert mr._render_tw_intelligence_html(intel, htmllib, False, False) == ""


def test_render_worldcup_marks_advancing_top2():
    """分組表前 2 名以綠色標示,第 3 名以後不標。
    (此 payload 只有 1 組 3 隊 → 未達 12 組×4 隊,依完整性防護保守視為小組賽進行中。)"""
    sports = {"news": {}, "worldcup": {"results": [], "fixtures": [], "groups": [
        {"name": "A 組", "rows": [
            {"team": "巴西", "gp": 3, "w": 3, "d": 0, "l": 0, "pts": 9},
            {"team": "美國", "gp": 3, "w": 2, "d": 0, "l": 1, "pts": 6},
            {"team": "越南", "gp": 3, "w": 0, "d": 0, "l": 3, "pts": 0},
        ]}]}}
    h = mr._render_sports_html(sports, htmllib)
    assert "晉級區" in h   # 部分 payload → 保守標「暫居小組前 2(晉級區)」
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
    # §B:週末也 push 信件存檔目錄(仍不含 history/model_history,故不污染預測歷史)
    assert pushes and pushes[0][1] == [str(mr.PODCAST_DIGEST_FILE), str(mr.EMAIL_ARCHIVE_DIR)]
    # 寄信必須早於標記/ push(at-least-once:寄成功才落狀態)
    assert events.index("sent") < events.index(("marked", 1))


def test_run_weekend_digest_renders_and_marks_all_loaded_episodes(monkeypatch):
    """回歸(Codex P1):週日載入 >14 集時,渲染集數與標記集數必須一致 —— 全部。

    舊行為:renderer 用預設 14 集上限,卻對 deliver_report 傳入完整清單 → 第 15 集起
    被誤標 shown 卻從未出現在信中;週末信每週僅一次、集在 96h 內過期 → 永久遺失。
    """
    import datetime as dt
    eps = [{"show": "股癌", "guid": f"ep{i}"} for i in range(16)]
    captured = {}
    _stub_weekend_sources(monkeypatch, podcast=eps)

    def _capture_render(episodes, snapshot, _htmllib, *, max_episodes=14, compact_points=None):
        captured["max_episodes"] = max_episodes
        return "<div>pod</div>"
    monkeypatch.setattr(mr, "_render_podcast_html", _capture_render)
    monkeypatch.setattr(mr, "archive_report_html", lambda *a, **k: None)
    monkeypatch.setattr(mr, "send_email", lambda *a: None)
    monkeypatch.setattr(mr, "mark_podcast_episodes_shown",
                        lambda e: captured.__setitem__("marked_n", len(e)))
    monkeypatch.setattr(mr, "save_history_state", lambda *a, **k: None)
    monkeypatch.setattr(mr, "_git_commit_and_push_state", lambda *a, **k: None)

    rc = mr.run_weekend_digest(dt.datetime(2026, 6, 14, 6, 0, tzinfo=mr.TPE))

    assert rc == 0
    # renderer 被要求渲染全部 16 集(非預設 14 上限)
    assert captured["max_episodes"] == 16
    # 標記已顯示的集數 == 渲染集數 == 全部載入(無靜默遺失)
    assert captured["marked_n"] == 16


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


def _wc_groups(gp, n_groups=12, n_teams=4):
    """完整世足 payload:12 組 × 4 隊(2026 賽制)。"""
    return [{"name": f"{chr(65 + g)} 組",
             "rows": [{"team": f"隊{g}-{i}", "pts": 9 - i * 2, "w": 3 - i, "d": 0,
                       "l": i, "gp": gp} for i in range(n_teams)]}
            for g in range(n_groups)]


def _wc_sports(groups):
    return {"worldcup": {"results": [], "fixtures": [], "groups": groups}}


def test_render_sports_worldcup_group_label_switches_when_stage_done():
    """世足:完整 payload 且小組賽全踢完 → 標題改「小組賽最終積分」。
    但**一律列全隊**:2026 為 48 隊制,除各組前 2 直接晉級外另有 8 個最佳第 3 名晉級,
    隱藏第 3 名會藏掉真正晉級的隊伍(Codex review)。"""
    done = mr._render_sports_html(_wc_sports(_wc_groups(3)), htmllib)
    assert "小組賽最終積分" in done and "直接晉級" in done
    for i in range(4):
        assert f"隊0-{i}" in done          # 全 4 隊都在,第 3/4 名不可被藏
    ongoing = mr._render_sports_html(_wc_sports(_wc_groups(2)), htmllib)
    assert "分組累計戰績" in ongoing and "暫居小組前 2" in ongoing
    assert "隊0-3" in ongoing


def test_render_sports_worldcup_partial_payload_not_treated_as_done():
    """Codex 回歸:ESPN 只回部分/重複分組時不可誤判小組賽已結束(標籤會誤導)。"""
    # 只回 3 組(雖各組都踢完)→ 保守視為進行中
    partial = mr._render_sports_html(_wc_sports(_wc_groups(3, n_groups=3)), htmllib)
    assert "分組累計戰績" in partial and "小組賽最終積分" not in partial
    # 12 組但某組只回 3 隊 → 同樣保守
    g = _wc_groups(3)
    g[0]["rows"] = g[0]["rows"][:3]
    short = mr._render_sports_html(_wc_sports(g), htmllib)
    assert "分組累計戰績" in short and "小組賽最終積分" not in short
    # 12 筆但有重複組(唯一組名只有 11)→ 不可當成完整 payload
    dup = _wc_groups(3)
    dup[11] = dup[0]
    d = mr._render_sports_html(_wc_sports(dup), htmllib)
    assert "分組累計戰績" in d and "小組賽最終積分" not in d


def test_render_tw_calendar_dividend_amount_handling():
    """配息卡:數字金額顯示「每股 X 元」;TWSE 未公告文字不可硬套「元」,改「配息待公告」。"""
    import datetime as dt
    ex = dt.date(2026, 7, 21)
    h1 = mr._render_tw_calendar_html({"dividends": [
        {"code": "2603", "name": "長榮", "ex_date": ex, "kind": "息", "amount": "5.0"}]})
    assert "每股 5 元" in h1                                   # 數字照顯示
    h2 = mr._render_tw_calendar_html({"dividends": [
        {"code": "0050", "name": "元大台灣50", "ex_date": ex, "kind": "息",
         "amount": "待公告實際收益分配金額"}]})
    assert "待公告實際收益分配金額 元" not in h2                # 不再硬接「元」
    assert "配息待公告" in h2
    h3 = mr._render_tw_calendar_html({"dividends": [
        {"code": "0056", "name": "元大高股息", "ex_date": ex, "kind": "息", "amount": ""}]})
    assert "配息待公告" not in h3 and "每股" not in h3          # 空金額不顯示金額片段
    # Codex 回歸:TWSE 空/NaN 儲存格 str() 後為 "nan"/float('nan'),float() 不拋 → 不可印「每股 nan 元」
    for bad in ("nan", "inf", float("nan")):
        hb = mr._render_tw_calendar_html({"dividends": [
            {"code": "0050", "name": "元大台灣50", "ex_date": ex, "kind": "息", "amount": bad}]})
        assert "每股" not in hb and "nan" not in hb and "inf" not in hb


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


def test_nba_offseason_note():
    import datetime as dt
    tpe = mr.TPE
    # 球季進行中(冠軍賽期間,6 月初)→ 無休賽季說明
    assert mr._nba_offseason_note(dt.datetime(2026, 6, 10, 8, tzinfo=tpe)) == ""
    # 6 月下旬 → 選秀/自由市場說明(措辭不宣稱冠軍賽已結束,避免賽事仍進行時誤判)
    jun = mr._nba_offseason_note(dt.datetime(2026, 6, 25, 8, tzinfo=tpe))
    assert "選秀" in jun and "已結束" not in jun
    # 7/8/9 月休賽季
    assert "休賽季" in mr._nba_offseason_note(dt.datetime(2026, 8, 1, 8, tzinfo=tpe))
    # 10 月中下旬開季 → 不再是休賽季空白
    assert mr._nba_offseason_note(dt.datetime(2026, 10, 25, 8, tzinfo=tpe)) == ""


def test_render_sports_shows_nba_offseason_when_no_games():
    import html as htmllib
    h = mr._render_sports_html({"nba_offseason": "NBA 休賽季:自由市場與夏季聯賽進行中。"}, htmllib)
    assert "NBA" in h and "休賽季" in h
    # 有實際冠軍賽賽果時,不顯示休賽季說明
    h2 = mr._render_sports_html(
        {"nba": [{"text": "BOS 110:104 NYK", "date": "06/12"}],
         "nba_offseason": "NBA 球季尾聲;選秀即將登場"}, htmllib)
    assert "NBA 冠軍賽" in h2 and "球季尾聲" not in h2 and "選秀" not in h2


def test_audit_dramatic_macro_claims():
    macro = {"VIX": {"close": 17.2, "change_pct": 0.3},   # 沒大跌
             "SOX": {"change_pct": -4.5},                  # 真的大跌
             "QQQ": {"change_pct": 2.8}}                   # 真的大漲
    # VIX 說「跳水」但只動 0.3% → 應被標記
    flags = mr._audit_dramatic_macro_claims("今日 VIX 跳水,市場恐慌降溫", macro)
    assert any("VIX" in f for f in flags)
    # 費半「重挫」-4.5%、那斯達克「大漲」+2.8% 都名實相符 → 不標記
    assert mr._audit_dramatic_macro_claims("費半重挫拖累台股", macro) == []
    assert mr._audit_dramatic_macro_claims("那斯達克大漲帶動科技股", macro) == []
    # 沒提到任何已知指標的戲劇詞 → 不誤報
    assert mr._audit_dramatic_macro_claims("台積電 ADR 暴跌", macro) == []
    # 戲劇詞與指標分屬不同子句(句號隔開)→ 不可跨句誤掛 VIX
    assert mr._audit_dramatic_macro_claims("VIX 變動不大。台股暴跌", macro) == []


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


# ===================== 世足淘汰賽修正(2026-07-13)=====================

def test_fetch_worldcup_results_cover_previous_espn_bucket(monkeypatch):
    """ESPN 以美國日期分桶:台北早上場次在「台北−2」桶也要抓到,日期以開球換算台北為準。

    實測:台北 07/12 09:00 的 8 強戰在 bucket 20260711;舊版只查 back=(1,0) → 永久漏失。
    """
    import datetime as dt
    wend = mr._WC_WINDOW[1]
    now = dt.datetime(wend.year, wend.month, wend.day, 6, 40, tzinfo=mr.TPE) - dt.timedelta(days=2)
    bucket = (now - dt.timedelta(days=2)).strftime("%Y%m%d")     # 「台北−2」桶
    ko_utc = (now - dt.timedelta(days=1)).replace(hour=1, minute=0)  # 台北昨日 09:00
    ko_iso = ko_utc.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    class R:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def fake_get(url, params=None, timeout=15, **k):
        if "standings" in url:
            return R({"children": []})
        if (params or {}).get("dates") == bucket:
            return R({"events": [{
                "id": "qf1", "date": ko_iso,
                "season": {"slug": "quarterfinals"},   # 回合真源是 season.slug(非 notes)
                "status": {"type": {"completed": True, "shortDetail": "FT"}},
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "Argentina"}, "score": "2"},
                        {"homeAway": "away", "team": {"displayName": "Switzerland"}, "score": "0"},
                    ],
                    "notes": [{"headline": "Argentina advance 4-3 on penalties"}],
                }],
            }]})
        return R({"events": []})

    monkeypatch.setattr(mr, "_http_get", fake_get)
    out = mr.fetch_worldcup(now)
    assert len(out["results"]) == 1
    g = out["results"][0]
    assert g["date"] == (now - dt.timedelta(days=1)).strftime("%m/%d")   # 台北日期,非查詢桶日
    assert g["round"] == "8 強"                       # slug 翻譯,不受 PK 註記干擾
    assert "2" in g["text"] and "0" in g["text"]


def test_render_sports_worldcup_hides_groups_after_knockout():
    """淘汰賽已開打(賽果帶非小組賽回合標籤)→ 小組積分表收斂成一行;小組賽賽果不觸發。"""
    res_ko = [{"text": "阿根廷 2 : 0 瑞士", "status": "FT", "date": "07/12",
               "round": "Quarterfinal"}]
    sports = {"worldcup": {"results": res_ko, "fixtures": [], "groups": _wc_groups(3)}}
    html = mr._render_sports_html(sports, htmllib)
    assert "小組賽已結束" in html
    assert "隊0-0" not in html                     # 積分表已收斂
    assert "阿根廷 2 : 0 瑞士" in html             # 淘汰賽果照常顯示
    assert "Quarterfinal" in html                  # 回合標籤顯示
    # 小組賽回合(或無回合標籤)→ 積分表照常
    res_grp = [{"text": "墨西哥 1 : 0 南非", "status": "FT", "date": "06/20",
                "round": "Group A"}]
    sports2 = {"worldcup": {"results": res_grp, "fixtures": [], "groups": _wc_groups(3)}}
    html2 = mr._render_sports_html(sports2, htmllib)
    assert "隊0-0" in html2 and "小組賽已結束" not in html2


# ===================== 網球修正(2026-07-13)=====================

def test_fetch_tennis_slam_survives_event_cap(monkeypatch):
    """大滿貫排在 ESPN 回傳清單末端也不可消失(先依層級排序再截量)。

    實測 2026-07-12 週日信:溫網決賽週,ESPN 前 8 筆全是 Challenger → 溫網整個不見。
    """
    def ev(name):
        return {"shortName": name, "name": name, "date": "2026-07-12T10:00Z",
                "status": {"type": {"shortDetail": "3rd Round", "state": "in"}},
                "groupings": []}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            # 9 個小賽在前,溫網在最後(超出舊版 [:8] 截點)
            return {"events": [ev(f"Small Open {i}") for i in range(9)] + [ev("Wimbledon")]}

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R())
    out = mr.fetch_tennis_digest()
    names = [t["name"] for t in out["tournaments"]]
    assert "Wimbledon" in names
    assert names[0] == "Wimbledon"                 # 大滿貫排最前


def test_cut_word_breaks_at_word_boundary():
    """截字斷在空白處+省略號;不再出現「for th」「AM ED」這種中斷字。"""
    s = "Cerity Partners Hall of Fame Open for the Championships"
    out = mr._cut_word(s, 40)
    assert out.endswith("…") and len(out) <= 41
    assert not out.endswith("th…")                 # 不切在字中間
    assert out[:-1] == s[:len(out) - 1] and s[len(out) - 1] == " " or out[-2] != " "
    assert mr._cut_word("short", 40) == "short"    # 短字串原樣
    cn = mr._cut_word("這是一段沒有空白的中文字串測試內容延伸更長", 10)
    assert cn.endswith("…") and len(cn) == 10      # 中文無空白 → n-1 硬切+省略號


# ===================== 淘汰賽對戰表 + 中職今日賽程(2026-07-14)=====================

def test_wc_round_of_translates_slugs_and_falls_back():
    """season.slug → 中文回合;未知 slug 原樣顯示且排最後(誠實不猜)。"""
    assert mr._wc_round_of({"season": {"slug": "quarterfinals"}}) == (3, "8 強")
    assert mr._wc_round_of({"season": {"slug": "semifinals"}}) == (4, "4 強")
    assert mr._wc_round_of({"season": {"slug": "group-stage"}})[1] == "小組賽"
    rank, name = mr._wc_round_of({"season": {"slug": "mystery-round"}})
    assert rank == 9 and name == "mystery round"
    assert mr._wc_round_of({})[0] == 9                # 無 season 也不炸


def test_fetch_worldcup_builds_knockout_bracket(monkeypatch):
    """範圍查詢組出各回合完整對戰表:已完賽含比分/PK 註記,未賽含台北開球時間;小組賽排除。"""
    import datetime as dt
    wend = mr._WC_WINDOW[1]
    now = dt.datetime(wend.year, wend.month, wend.day, 6, 40, tzinfo=mr.TPE) - dt.timedelta(days=3)

    def ev(slug, iso, done, home, away, hs="1", as_="0", pk=""):
        return {"id": f"{slug}-{home}", "date": iso,
                "season": {"slug": slug},
                "status": {"type": {"completed": done,
                                    "state": "post" if done else "pre",
                                    "shortDetail": "FT" if done else "Sched"}},
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": home}, "score": hs},
                        {"homeAway": "away", "team": {"displayName": away}, "score": as_},
                    ],
                    "notes": ([{"headline": pk}] if pk else []),
                }]}

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def fake_get(url, params=None, timeout=15, **k):
        if "standings" in url:
            return R({"children": []})
        d = (params or {}).get("dates") or ""
        if "-" in d:   # 範圍查詢 → 對戰表資料
            return R({"events": [
                ev("group-stage", "2026-06-20T01:00Z", True, "Mexico", "South Africa"),
                ev("quarterfinals", "2026-07-12T01:00Z", True, "Argentina", "Switzerland",
                   "0", "0", pk="Argentina advance 4-3 on penalties"),
                ev("semifinals", "2026-07-14T19:00Z", False, "France", "Spain"),
            ]})
        return R({"events": []})

    monkeypatch.setattr(mr, "_http_get", fake_get)
    out = mr.fetch_worldcup(now)
    ko = out.get("knockout") or []
    names = [rd["name"] for rd in ko]
    assert names == ["8 強", "4 強"]                  # 依回合順序;小組賽不進對戰表
    qf = ko[0]["games"][0]
    assert qf["done"] and "PK" in qf["text"] and "晉級" in qf["text"]
    sf = ko[1]["games"][0]
    assert not sf["done"]
    # 台北開球時間:07-14T19:00Z = 台北 07/15 03:00
    assert sf["when"] == "07/15 03:00"


def test_render_sports_worldcup_knockout_bracket_supersedes():
    """對戰表存在 → 為世足主視圖:近期戰績/今日賽程不再另列,小組表收斂。"""
    sports = {"worldcup": {
        "results": [{"text": "阿根廷 2 : 0 瑞士", "status": "FT", "date": "07/12",
                     "round": "8 強"}],
        "fixtures": [{"text": "法國 vs 西班牙", "kickoff": "07/15 03:00", "round": ""}],
        "groups": _wc_groups(3),
        "knockout": [
            {"name": "8 強", "games": [
                {"text": "阿根廷 2 : 0 瑞士", "when": "07/12", "done": True}]},
            {"name": "4 強", "games": [
                {"text": "法國 vs 西班牙", "when": "07/15 03:00", "done": False}]},
        ],
    }}
    html = mr._render_sports_html(sports, htmllib)
    assert "淘汰賽對戰表" in html
    assert "8 強" in html and "4 強" in html
    assert "07/15 03:00" in html                     # 未賽場次帶開球時間
    assert "近期戰績" not in html                     # 子集區塊不重複
    assert "今日/近日賽程" not in html
    assert "隊0-0" not in html and "小組賽已結束" in html   # 小組表收斂


def test_fetch_cpbl_week_fixtures(monkeypatch):
    """未來一週未開打場次列日期+開賽時間(台北);已完賽不列;跨日查詢依 game id 去重。"""
    import datetime as dt
    now = dt.datetime.now(mr.TPE).replace(hour=6, minute=30)
    today_evening = now.replace(hour=18, minute=35)
    day3_evening = (now + dt.timedelta(days=3)).replace(hour=17, minute=5)

    def rfc(d):
        return d.astimezone(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    def payload_for(day):
        games = {}
        if day == now.strftime("%Y-%m-%d"):
            games = {
                "g1": {"status_type": "status.type.pregame", "start_time": rfc(today_evening),
                       "away_team_id": "t1", "home_team_id": "t2"},
                "g2": {"status_type": "status.type.final", "start_time": rfc(today_evening),
                       "away_team_id": "t3", "home_team_id": "t4",
                       "total_away_points": "3", "total_home_points": "5"},
            }
        elif day == day3_evening.strftime("%Y-%m-%d"):
            # g1 重複出現在另一天的桶(Yahoo 偶發)→ 應被 id 去重;g3 為新場次
            games = {
                "g1": {"status_type": "status.type.pregame", "start_time": rfc(today_evening),
                       "away_team_id": "t1", "home_team_id": "t2"},
                "g3": {"status_type": "status.type.pregame", "start_time": rfc(day3_evening),
                       "away_team_id": "t3", "home_team_id": "t4"},
            }
        return {"service": {"scoreboard": {
            "teams": {"t1": {"display_name": "味全龍"}, "t2": {"display_name": "統一7-ELEVEn獅"},
                      "t3": {"display_name": "樂天桃猿"}, "t4": {"display_name": "中信兄弟"}},
            "games": games,
        }}}

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    monkeypatch.setattr(mr, "_http_get",
                        lambda url, params=None, **k: R(payload_for((params or {}).get("date"))))
    out = mr.fetch_cpbl_today_fixtures(now)
    assert len(out) == 2                              # final 不列;g1 只出現一次
    assert out[0]["away"] == "味全龍" and out[0]["start"] == "18:35"
    assert out[0]["date"] == now.strftime("%m/%d")
    assert out[1]["away"] == "樂天桃猿" and out[1]["date"] == day3_evening.strftime("%m/%d")


def test_render_cpbl_fixtures_block():
    sports = {"cpbl_fixtures": [{"away": "味全龍", "home": "統一7-ELEVEn獅",
                                 "date": "07/14", "start": "18:35"}]}
    html = mr._render_sports_html(sports, htmllib)
    assert "中華職棒 未來一週賽程" in html and "07/14 18:35" in html
    assert "味全龍 vs 統一7-ELEVEn獅" in html


def test_wc_placeholder_zh():
    """未定隊伍英文佔位翻繁中;非佔位原樣(誠實 fallback)。"""
    assert mr._wc_placeholder_zh("Semifinal 2 Winner") == "4 強戰2勝方"
    assert mr._wc_placeholder_zh("Semifinal 1 Loser") == "4 強戰1負方"
    assert mr._wc_placeholder_zh("Quarterfinal 3 Winner") == "8 強戰3勝方"
    assert mr._wc_placeholder_zh("Argentina") == "Argentina"
    assert mr._wc_placeholder_zh("") == ""


def test_fetch_worldcup_no_bracket_during_group_stage(monkeypatch):
    """小組賽期間不查對戰表(TBD 佔位無資訊+會誤觸發收斂),且範圍查詢不得發出。"""
    import datetime as dt
    ks = mr._WC_KO_START
    now = dt.datetime(ks.year, ks.month, ks.day, 6, 30, tzinfo=mr.TPE) - dt.timedelta(days=5)
    range_calls = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [], "children": []}

    def fake_get(url, params=None, timeout=15, **k):
        d = str((params or {}).get("dates") or "")
        if "-" in d:
            range_calls.append(d)
        return R()

    monkeypatch.setattr(mr, "_http_get", fake_get)
    out = mr.fetch_worldcup(now)
    assert "knockout" not in out
    assert range_calls == []                          # 沒發範圍查詢


def test_fetch_worldcup_bracket_range_uses_fixed_ko_start(monkeypatch):
    """範圍查詢起點固定=淘汰賽首日−1(ESPN 100 場上限;滾動窗在淘汰賽早期會全包 104 場截尾)。"""
    import datetime as dt
    ks = mr._WC_KO_START
    now = dt.datetime(ks.year, ks.month, ks.day, 6, 30, tzinfo=mr.TPE) + dt.timedelta(days=1)
    range_calls = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [], "children": []}

    def fake_get(url, params=None, timeout=15, **k):
        d = str((params or {}).get("dates") or "")
        if "-" in d:
            range_calls.append(d)
        return R()

    monkeypatch.setattr(mr, "_http_get", fake_get)
    mr.fetch_worldcup(now)
    assert len(range_calls) == 1
    start = range_calls[0].split("-")[0]
    assert start == (ks - dt.timedelta(days=1)).strftime("%Y%m%d")


def test_render_sports_worldcup_scheduled_bracket_keeps_groups_and_results():
    """淘汰賽首日早上(對戰表全未賽):積分表不收斂、末日小組賽賽果照常顯示;
    對戰表已含賽程 → 通用「今日/近日賽程」不重複列。"""
    sports = {"worldcup": {
        "results": [{"text": "墨西哥 1 : 0 南非", "status": "FT", "date": "06/27",
                     "round": "小組賽"}],
        "fixtures": [{"text": "加拿大 vs 南非", "kickoff": "06/29 03:00", "round": ""}],
        "groups": _wc_groups(3),
        "knockout": [{"name": "32 強", "games": [
            {"text": "加拿大 vs 南非", "when": "06/29 03:00", "done": False}]}],
    }}
    html = mr._render_sports_html(sports, htmllib)
    assert "隊0-0" in html                            # 積分表未收斂
    assert "墨西哥 1 : 0 南非" in html                 # 小組賽賽果未被吞
    assert "淘汰賽對戰表" in html and "06/29 03:00" in html
    assert "今日/近日賽程" not in html                 # 賽程已在對戰表,不重複


# ===================== 07-14 信件調整批 =====================

def test_render_worldcup_collapses_stale_early_rounds():
    """已全部打完、且後面回合已開打的早期回合 → 收斂一行;最新回合與未來回合完整顯示。"""
    ko = [
        {"name": "32 強", "games": [{"text": f"a{i} 1 : 0 b{i}", "when": "06/29", "done": True}
                                    for i in range(16)]},
        {"name": "8 強", "games": [{"text": "阿根廷 3 : 1 瑞士", "when": "07/12", "done": True}]},
        {"name": "4 強", "games": [{"text": "西班牙 vs 法國", "when": "07/15 03:00", "done": False}]},
    ]
    html = mr._render_sports_html({"worldcup": {"knockout": ko, "groups": [],
                                                "results": [], "fixtures": []}}, htmllib)
    assert "已完賽 16 場" in html and "a3 1 : 0 b3" not in html   # 32 強收斂
    assert "阿根廷 3 : 1 瑞士" in html                            # 最新回合完整
    assert "西班牙 vs 法國" in html                               # 未來回合完整


def test_render_mlb_standings_and_fixtures():
    sports = {
        "standings": {"美聯": [{"team": "TB", "record": "56-38", "pct": 0.596}]},
        "mlb_fixtures": [{"text": "LAD @ NYY", "when": "07/17 23:05", "special": False},
                         {"text": "AL All-Stars @ NL All-Stars", "when": "07/16 08:00",
                          "special": True}],
    }
    html = mr._render_sports_html(sports, htmllib)
    assert "MLB 戰績（勝率前 5）" in html and "TB 56-38(0.596)" in html
    assert "MLB 未來一週焦點賽程" in html and "LAD @ NYY" in html
    assert "特別賽事" in html                                     # 明星賽標記


def test_render_nba_week_fixtures():
    html = mr._render_sports_html(
        {"nba_fixtures": [{"text": "LAL @ BOS", "when": "10/22 08:00"}]}, htmllib)
    assert "NBA 未來一週賽程" in html and "LAL @ BOS" in html


def test_render_tennis_cleaned_block():
    """網球區:賽果帶日期+賽事名;賽事列表只列進行中/即將(台北日期),不再出現美東原始字串。"""
    tennis = {
        "results": [{"tour": "ATP", "tier": "大滿貫", "winner": "J. Sinner",
                     "loser": "A. Zverev", "event": "Wimbledon", "date": "07/13"}],
        "tournaments": [{"name": "Canadian Open", "status": "07/20 起", "tier": "1000"}],
    }
    html = mr._render_sports_html({"tennis": tennis}, htmllib)
    assert "07/13 ATP" in html and "J. Sinner" in html and "（Wimbledon）" in html
    assert "進行中/即將" in html and "Canadian Open（07/20 起）" in html
    assert "EDT" not in html                                      # 美東字串不再出現


def test_fetch_mlb_week_fixtures_filters_top_teams(monkeypatch):
    """MLB 週賽程:只留強隊對戰或特別賽事(一週 ~100 場全列是雜訊)。"""
    def ev(name, iso, slug="regular-season"):
        return {"shortName": name, "name": name, "date": iso,
                "season": {"slug": slug},
                "status": {"type": {"state": "pre"}}}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [
                ev("TB @ BOS", "2026-07-17T17:35Z"),
                ev("PIT @ CLE", "2026-07-17T23:10Z"),
                ev("AL All-Stars @ NL All-Stars", "2026-07-16T00:00Z", "all-star"),
            ]}

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R())
    out = mr.fetch_mlb_week_fixtures(top_teams={"TB", "NYY", "LAD"})
    texts = [g["text"] for g in out]
    assert "TB @ BOS" in texts                        # 強隊對戰保留
    assert "PIT @ CLE" not in texts                   # 非強隊剔除
    assert any(g["special"] for g in out)             # 明星賽保留並標記


def test_fetch_nba_week_fixtures_matches_full_team_names(monkeypatch):
    """NBA_FAVORITE_TEAMS 用全名(文件明載):過濾須比對兩隊全名,不能只看縮寫 shortName
    (Codex review P2:LAL @ BOS 永遠比不中 'lakers')。"""
    def ev(short, home_full, away_full, iso):
        return {"shortName": short, "name": short, "date": iso,
                "season": {"slug": "regular-season"},
                "status": {"type": {"state": "pre"}},
                "competitions": [{"competitors": [
                    {"team": {"displayName": home_full, "abbreviation": short.split(" @ ")[1]}},
                    {"team": {"displayName": away_full, "abbreviation": short.split(" @ ")[0]}},
                ]}]}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [
                ev("LAL @ BOS", "Boston Celtics", "Los Angeles Lakers", "2026-10-22T00:00Z"),
                ev("MIA @ NYK", "New York Knicks", "Miami Heat", "2026-10-22T02:00Z"),
            ]}

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R())
    monkeypatch.setenv("NBA_FAVORITE_TEAMS", "Celtics,Lakers")
    out = mr.fetch_nba_week_fixtures()
    texts = [g["text"] for g in out]
    assert "LAL @ BOS" in texts                       # 全名關鍵字命中兩隊之一
    assert "MIA @ NYK" not in texts                   # 非關注隊剔除
    assert all("_competitors" not in g for g in out)  # 內部欄位不外洩


def test_fetch_nba_week_fixtures_den_not_matching_golden_state(monkeypatch):
    """單字關鍵字整詞比對:'den'(金塊)不得 substring 誤中 'Golden State'
    (Codex review 第二輪;沿用 _nba_team_matches_favorite 既有規則)。"""
    def ev(short, home_full, away_full, iso):
        return {"shortName": short, "name": short, "date": iso,
                "season": {"slug": "regular-season"},
                "status": {"type": {"state": "pre"}},
                "competitions": [{"competitors": [
                    {"team": {"displayName": home_full, "abbreviation": short.split(" @ ")[1]}},
                    {"team": {"displayName": away_full, "abbreviation": short.split(" @ ")[0]}},
                ]}]}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [
                ev("GSW @ MIA", "Miami Heat", "Golden State Warriors", "2026-10-22T00:00Z"),
                ev("DEN @ PHX", "Phoenix Suns", "Denver Nuggets", "2026-10-22T02:00Z"),
            ]}

    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R())
    monkeypatch.setenv("NBA_FAVORITE_TEAMS", "den")
    out = mr.fetch_nba_week_fixtures()
    texts = [g["text"] for g in out]
    assert "DEN @ PHX" in texts                       # 縮寫整詞命中金塊
    assert "GSW @ MIA" not in texts                   # 不誤中 Golden State


def test_medical_org_cap_covers_source_org_key(monkeypatch):
    """G8 回歸(Codex review):TFDA 公告標題常不含「食藥署」——每日一機構 cap 須退回
    來源設定的 org_key 辨識,否則官方 feed 多則公告會繞過 cap 佔滿醫界區。"""
    import datetime as dt

    class Feed:
        def __init__(self, url):
            title = ("藥品全面回收 多批次檢驗不符規範" if "rssNews" in url
                     else "醫材預防性下架 標示不符須改正")   # 皆不含「食藥署」
            self.entries = [{
                "title": title,
                "link": f"https://www.fda.gov.tw/x/{'a' if 'rssNews' in url else 'b'}",
                "published": "Tue, 02 Jun 2026 08:00:00 GMT",
            }]
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, *a, **k: Feed(url))
    # 只留兩條 TFDA 官方 feed(共用 org_key),排除 Google 與其他直連源的干擾
    monkeypatch.setattr(mr, "TW_INTELLIGENCE_QUERIES", {"policy": (), "medical": ()})
    monkeypatch.setattr(mr, "TW_INTELLIGENCE_DIRECT_SOURCES", {
        "policy": (),
        "medical": tuple(s for s in mr.TW_INTELLIGENCE_DIRECT_SOURCES["medical"]
                         if "FDA" in s["name"]),
    })
    out = mr.fetch_tw_daily_intelligence(
        dt.datetime(2026, 6, 3, 6, tzinfo=mr.TPE), per_kind_limit=8)
    # 兩則標題皆無機關名 → 靠 org_key 歸同機構,每日最多 1 條
    assert len(out["medical"]) == 1


# ═══ 信件調整批#2(2026-07-14)═══
def test_ipo_filter_excludes_bonds(monkeypatch):
    """公開申購只留股票抽籤:央債/公司債(代號含字母或名稱含「債」)一律排除。"""
    import datetime as dt
    today = dt.datetime.now(mr.TPE)
    roc = f"{today.year - 1911}/{today.month:02d}/{today.day:02d}"
    fields = ["序號", "抽籤日期", "證券名稱", "證券代號", "發行市場", "申購開始日",
              "申購結束日", "x7", "x8", "承銷價(元)", "x10", "x11", "x12", "申購股數",
              "x14", "x15", "中籤率(%)"]
    def mk(name, code):
        row = [""] * len(fields)
        row[1] = row[5] = row[6] = roc
        row[2], row[3] = name, code
        row[9], row[13], row[16] = "100", "1000", "1.0"
        return row
    payload = {"fields": fields, "data": [
        mk("115央債甲07", "A151GA"),          # 央債:代號含字母 → 排除
        mk("某某公司債", "12345"),             # 名稱含「債」→ 排除
        mk("測試生技", "6789"),                # 股票 → 保留
    ]}
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload
    def fake_get(url, **kw):
        if "publicForm" in url:
            return R()
        raise RuntimeError("其他端點略過")     # TWT48U/市價查詢在本測試不需要
    monkeypatch.setattr(mr, "_http_get", fake_get)
    monkeypatch.setattr(mr, "_fetch_twse_stock_day_all", lambda: [])
    out = mr.fetch_tw_calendar(today)
    names = [i["name"] for i in out["ipo"]]
    assert names == ["測試生技"]


def test_dividend_finmind_fills_announced_amount(monkeypatch):
    """TWSE 對 ETF 回「待公告」文字時,FinMind 已公告金額須補上(含發放日);
    FinMind 也還沒有(=0)→ 維持待公告。"""
    import datetime as dt
    today = dt.datetime.now(mr.TPE)
    ex = (today + dt.timedelta(days=7)).date()
    roc_ex = f"{ex.year - 1911}年{ex.month:02d}月{ex.day:02d}日"
    fields = ["除權除息日期", "股票代號", "名稱", "除權息", "無償配股率",
              "現金增資配股率", "現金增資認購價", "現金股利"]
    payload = {"fields": fields, "data": [
        [roc_ex, "0050", "元大台灣50", "息", "0", "0", "0",
         "<p style= text-align:center;>待公告實際收益分配金額</p>"],
    ]}
    fm = {"data": [{"stock_id": "0050",
                    "CashExDividendTradingDate": ex.isoformat(),
                    "CashEarningsDistribution": 1.35,
                    "CashDividendPaymentDate": (ex + dt.timedelta(days=20)).isoformat()}]}
    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p
    def fake_get(url, **kw):
        if "TWT48U" in url:
            return R(payload)
        if "finmindtrade" in url:
            return R(fm)
        if "publicForm" in url:
            return R({"fields": [], "data": []})
        raise RuntimeError("unexpected")
    monkeypatch.setattr(mr, "_http_get", fake_get)
    out = mr.fetch_tw_calendar(today)
    d = out["dividends"][0]
    assert d["amount"] == "1.35" and d["pay_date"]
    html = mr._render_tw_calendar_html(out)
    assert "每股 1.35 元" in html and "發放" in html
    assert "待公告" not in html


def _quotes_for_night():
    def base(t):
        return {"ticker": t, "date": "2026-07-13", "close": 100.0,
                "prev_close": 99.0, "change_pct": 1.01}
    return {
        "QQQ": base("QQQ"), "TSM": base("TSM"), "SPY": base("SPY"),
        "USDTWD": 31.0, "USDTWD_prev": 31.1, "MACRO": {},
        "SEC_FILINGS": [], "TAIFEX_OI": {}, "MARGIN": {}, "WEEKLY": {},
        "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
        "TAIEX_PRED": {}, "BACKTEST": "", "ALERTS": [], "DATA_QUALITY": [],
    }


def test_night_txf_embedded_in_taiex_section():
    """夜盤台指期併入「五、加權指數開盤預測」表格;第五段存在時不再出現獨立夜盤卡。"""
    q = {**_quotes_for_night(),
         "TAIEX_PRED": {"pred_open": 46200, "last_close": 46000, "weighted_pct": 0.4,
                        "ci_lower": 45900, "ci_upper": 46500, "consensus": "偏多"},
         "NIGHT_TXF": {"date": "2026/07/14", "night_close": 45058.0, "night_pct": -1.11}}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-07-14", "每日報")
    i5 = html.find("五、加權指數開盤預測")
    i_n = html.find("夜盤台指期")
    i6 = html.find("個股開盤預測")
    assert 0 < i5 < i_n < i6                 # 夜盤列位於第五段內
    assert "45058" in html and "-1.11%" in html


def test_night_txf_standalone_fallback_without_taiex_pred():
    """加權預測失敗的降級運行 → 夜盤退回獨立卡,資料不遺失。"""
    q = {**_quotes_for_night(), "TAIEX_PRED": {},
         "NIGHT_TXF": {"date": "2026/07/14", "night_close": 45058.0, "night_pct": -1.11}}
    html = mr.render_html(q, {"error": "x"}, {"error": "x"}, "x", "2026-07-14", "每日報")
    assert "夜盤台指期" in html and "45058" in html


def test_sports_news_titles_render_as_hyperlinks():
    """體育「消息」標題須為可見超連結(dict 格式);舊純字串格式仍相容不崩。"""
    import html as htmllib
    sports = {"news": {"MLB": [
        {"title": "大谷翔平雙響砲", "link": "https://news.example.com/ohtani"},
        "舊格式純字串標題",
    ]}}
    h = mr._render_sports_html(sports, htmllib)
    assert "<a href='https://news.example.com/ohtani'" in h
    assert "text-decoration:underline" in h        # 看得出可點
    assert "舊格式純字串標題" in h                   # 舊 state 相容
