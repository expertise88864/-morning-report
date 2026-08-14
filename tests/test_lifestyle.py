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
    assert "NBA 冠軍賽" in h and "紐約尼克 leads series 3-1" in h   # 縮寫轉繁中(2026-07-16)
    assert "MLB 戰績" in h and "坦帕灣光芒" in h and "40-25" in h   # 表格化,中文隊名
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
    # 2026-07-27:標題改由**實際有內容的項目**推出,不再寫死。
    # 實信裡世足賽期已於 7/19 結束、整個區塊不出現,標題卻仍寫著
    # 「世足 / MLB / NBA / 中職 / 網球」——讀者會去找一個不存在的區塊。
    assert "世足" in h, "有世足資料時標題必須列出它"
    assert "MLB" not in h.split("</h2>")[0], "沒有 MLB 資料卻列進標題"
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
                    "round": {"displayName": "Round 2"},
                    "status": {"type": {"completed": True}},
                    "competitors": [
                        {"athlete": {"shortName": "A. Player"}, "winner": True},
                        {"athlete": {"shortName": "B. Loser"}, "winner": False}]},
                   {"id": "c2", "date": "2026-06-14T09:00Z",
                    "round": {"displayName": "Qualifying 1st Round"},   # 批#30:資格賽濾掉
                    "status": {"type": {"completed": True}},
                    "competitors": [
                        {"athlete": {"shortName": "Q. Winner"}, "winner": True},
                        {"athlete": {"shortName": "Q. Loser"}, "winner": False}]}]}],
        }]})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    out = mr.fetch_tennis_digest(dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE))
    # 進行中賽事列入「進行中/即將」清單(已完賽者不再列——賽果區已涵蓋)
    t = next(t for t in out["tournaments"] if t["name"] == "Boss Open")
    assert t["status"] == "進行中"
    # 兩端點都回同一場 → 用 competition id 去重,只算一次;賽果帶台北日期+輪次
    matches = [r for r in out["results"] if r["winner"] == "A. Player"]
    assert len(matches) == 1 and matches[0]["tour"] == "ATP"
    assert matches[0]["date"] == "06/14"
    assert matches[0]["round"] == "Round 2"                       # 批#30:輪次記錄
    assert not any(r["winner"] == "Q. Winner" for r in out["results"])  # 資格賽不進賽果


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
    assert len(q) == 16                               # + 房市政策-台股(2026-07-17 批#14)
    assert "新青安" in q["房市政策-台股"]
    assert "藥華藥" in q["生技-台股"] and "臨床" in q["生技-台股"]
    assert "生技股" not in q["生技-台股"]              # 去掉過寬關鍵字
    assert "投資收益" in q["金融-台股"] or "淨息差" in q["金融-台股"]
    # 新增四類齊備,以台股在地事件為主
    for new in ("傳產-台股", "營建-台股", "重電-台股", "觀光-台股",
                "房市-中彰投", "建設-中彰投"):
        assert new in q and q[new]
    assert "台中" in q["房市-中彰投"] and "草屯" in q["房市-中彰投"]   # 在地房市涵蓋


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


# --------------------------------------------------- 中職:當前半季 + 全年兩張表

_WIKI_HALF = """=== 上半球季 ===
{| class = "wikitable"
|-
| 1 || [[味全龍]] ||60||60||39||21||0||{{Winning percentage|39|21}}||–||–
|-
| 2 || [[富邦悍將]] ||60||60||34||26||0||{{Winning percentage|34|26}}||5.0||–
|-
| 3 || [[統一7-ELEVEn獅]] ||60||59||29||29||1||{{Winning percentage|29|29}}||9.5||–
|-
| 4 || [[台鋼雄鷹]] ||60||60||30||29||1||{{Winning percentage|30|29}}||9.0||–
|}
{{中華職棒賽程/表頭|上半球季}}

=== 下半球季 ===
{| class = "wikitable"
|-
! 球隊 !! 主場
|-
| [[味全龍]] || [[天母棒球場]]
|-
| [[樂天桃猿]] || [[樂天桃園棒球場]]
|}
{| class = "wikitable"
|-
| 1 || [[味全龍]] ||60||25||15||10||0||{{Winning percentage|15|10}}||–||–
|-
| 2 || [[樂天桃猿]] ||60||24||14||10||0||{{Winning percentage|14|10}}||0.5||36
|-
| 3 || [[中信兄弟]] ||60||26||13||13||0||{{Winning percentage|13|13}}||2.5||33
|-
| 4 || [[富邦悍將]] ||60||24||10||14||0||{{Winning percentage|10|14}}||4.5||32
|}
{{中華職棒賽程/表頭|下半球季}}

=== 全年球季 ===
{| class = "wikitable"
|-
| 1 || [[味全龍]] ||120||85||54||31||0||{{Winning percentage|54|31}}||–||–
|-
| 2 || [[富邦悍將]] ||120||84||44||40||0||{{Winning percentage|44|40}}||9.5||27
|-
| 3 || [[統一7-ELEVEn獅]] ||120||84||41||42||1||{{Winning percentage|41|42}}||12.0||24
|-
| 4 || [[台鋼雄鷹]] ||120||86||41||44||1||{{Winning percentage|41|44}}||13.0||22
|}
"""


def _wiki_get(wikitext):
    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"parse": {"wikitext": wikitext}}
    return lambda *a, **k: R()


def test_wiki_tables_are_keyed_by_the_declared_section(monkeypatch):
    """**分段名取自章節標題,不從場次推導。** 先前取「已賽總場次最大」者,
    而全年球季永遠最大 —— 生產(海外 IP 走 wiki 備援)顯示的一直是全年,
    下半季的排名戰完全看不到(使用者 2026-08-15 回報)。"""
    monkeypatch.setattr(mr, "_http_get", _wiki_get(_WIKI_HALF))
    t = mr._cpbl_wiki_tables(2026)
    assert set(t) == {"上半季", "下半季", "全年"}
    assert t["下半季"][0]["wdl"] == "15-0-10"      # 下半季的味全,不是全年的
    assert t["全年"][0]["wdl"] == "54-0-31"
    # 節內排在前面的非戰績表(主場地)不得被當成戰績:認表靠**列的形狀**
    assert len(t["下半季"]) == 4 and t["下半季"][0]["games"] == "25"


def test_current_split_is_the_half_being_played_not_the_biggest(monkeypatch):
    """當前分段 = **有打過球的最後一個半季**。已賽最多的是全年段,
    最後一個章節也是全年段 —— 兩種便宜的判準都會挑錯。"""
    monkeypatch.setattr(mr, "_http_get", _wiki_get(_WIKI_HALF))
    meta = {}
    rows = mr.fetch_cpbl_standings(meta)
    assert meta["season_label"] == "下半季"
    assert rows[0]["wdl"] == "15-0-10"
    assert meta["full_year_label"] == "全年"
    assert meta["full_year"][0]["wdl"] == "54-0-31"


def test_first_half_does_not_show_the_same_table_twice(monkeypatch):
    """上半季期間全年度**就等於上半季** —— 同一張表印兩次不是資訊。"""
    first_half = _WIKI_HALF.split("=== 下半球季 ===")[0] + """=== 全年球季 ===
{| class = "wikitable"
|-
| 1 || [[味全龍]] ||120||60||39||21||0||{{Winning percentage|39|21}}||–||–
|-
| 2 || [[富邦悍將]] ||120||60||34||26||0||{{Winning percentage|34|26}}||5.0||–
|-
| 3 || [[統一7-ELEVEn獅]] ||120||59||29||29||1||{{Winning percentage|29|29}}||9.5||–
|-
| 4 || [[台鋼雄鷹]] ||120||60||30||29||1||{{Winning percentage|30|29}}||9.0||–
|}
"""
    monkeypatch.setattr(mr, "_http_get", _wiki_get(first_half))
    meta = {}
    rows = mr.fetch_cpbl_standings(meta)
    assert meta["season_label"] == "上半季"
    assert rows[0]["wdl"] == "39-0-21"
    assert "full_year" not in meta


def test_unknown_sections_fall_back_without_claiming_a_split(monkeypatch):
    """頁面改版認不出章節 —— 表照樣要有(晨報不可斷),但**不得**宣稱
    自己是哪一段(標錯段比不標更糟:讀者會拿半季勝差去想全年門票)。"""
    plain = _WIKI_HALF.replace("=== 上半球季 ===", "=== 例行賽戰績 ===")         .replace("=== 下半球季 ===", "=== 其他 ===")         .replace("=== 全年球季 ===", "=== 累計 ===")
    monkeypatch.setattr(mr, "_http_get", _wiki_get(plain))
    meta = {}
    rows = mr.fetch_cpbl_standings(meta)
    assert rows and meta["season_label"] == ""
    assert "full_year" not in meta


def test_the_official_page_label_comes_from_its_own_h3(monkeypatch):
    """官網預設回**當前分段**、`seasonCode=0` 回全年度,而分段名寫在
    頁面的 `<h3>` 裡 —— 直接用它,不從場次反推(季後賽、補賽的日子
    推導會說謊,而讀者沒有辦法發現)。"""
    half = ('<h3>2026年 下半季<span class="en"></span></h3>'
            '<div class="rank">1</div><a href="/team?TeamNo=A">味全龍</a>'
            '<td class="num">25</td> <td class="num">15-0-10</td> '
            '<td class="num">0.600</td> <td class="num">-</td>')
    year = ('<h3>2026年 全年度<span class="en"></span></h3>'
            '<div class="rank">1</div><a href="/team?TeamNo=A">味全龍</a>'
            '<td class="num">85</td> <td class="num">54-0-31</td> '
            '<td class="num">0.635</td> <td class="num">-</td>')
    seen = []

    class R:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        params = kw.get("params") or {}
        seen.append(params.get("seasonCode"))
        return R(year if params.get("seasonCode") == "0" else half)

    monkeypatch.setattr(mr, "_http_get", fake_get)
    meta = {}
    rows = mr.fetch_cpbl_standings(meta)
    assert meta["source"] == "官網"
    assert meta["season_label"] == "下半季" and rows[0]["wdl"] == "15-0-10"
    assert meta["full_year_label"] == "全年"
    assert meta["full_year"][0]["wdl"] == "54-0-31"
    assert seen == [None, "0"]          # 預設那張沒有帶 seasonCode


def test_a_broken_full_year_does_not_lose_the_current_table(monkeypatch):
    """全年度抓不到只是少一張表 —— **當前分段不得跟著消失**(晨報不可斷)。"""
    half = ('<h3>2026年 下半季<span class="en"></span></h3>'
            '<div class="rank">1</div><a href="/team?TeamNo=A">味全龍</a>'
            '<td class="num">25</td> <td class="num">15-0-10</td> '
            '<td class="num">0.600</td> <td class="num">-</td>')

    class R:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        if (kw.get("params") or {}).get("seasonCode") == "0":
            raise RuntimeError("boom")
        return R(half)

    monkeypatch.setattr(mr, "_http_get", fake_get)
    meta = {}
    rows = mr.fetch_cpbl_standings(meta)
    assert rows[0]["wdl"] == "15-0-10" and meta["season_label"] == "下半季"
    assert "full_year" not in meta


def test_render_shows_both_tables_with_their_own_labels():
    base = [{"rank": 1, "team": "味全龍", "games": "25", "wdl": "15-0-10",
             "pct": "0.600", "gb": "-"}]
    full = [{"rank": 1, "team": "味全龍", "games": "85", "wdl": "54-0-31",
             "pct": "0.635", "gb": "-"}]
    h = mr._render_sports_html({"news": {}, "cpbl": base, "cpbl_label": "下半季",
                                "cpbl_full_year": full,
                                "cpbl_full_year_label": "全年",
                                "cpbl_source": "官網"}, htmllib)
    assert "中華職棒戰績（下半季）" in h and "中華職棒戰績（全年）" in h
    assert "15-0-10" in h and "54-0-31" in h
    # 分段名拿不到時不標,不猜
    plain = mr._render_sports_html({"news": {}, "cpbl": base}, htmllib)
    assert "中華職棒戰績" in plain and "（" not in plain.split("中華職棒戰績")[1][:3]


def test_wiki_source_note_appears_once_under_the_last_table():
    """備援警語是**整組表**的註腳,兩張表各印一次會變成雜訊。"""
    base = [{"rank": 1, "team": "味全龍", "games": "25", "wdl": "15-0-10",
             "pct": "0.600", "gb": "-"}]
    full = [{"rank": 1, "team": "味全龍", "games": "85", "wdl": "54-0-31",
             "pct": "0.635", "gb": "-"}]
    h = mr._render_sports_html({"news": {}, "cpbl": base, "cpbl_label": "下半季",
                                "cpbl_full_year": full,
                                "cpbl_full_year_label": "全年",
                                "cpbl_source": "Wikipedia 備援"}, htmllib)
    assert h.count("可能稍有遲滯") == 1


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
    """預設犧牲序依使用者指定:文獻/五檔先砍、Podcast/體育殿後。
    (2026-08-07:政策/醫界卡整組移除,不再出現在犧牲序。)"""
    order = mr._truncate_order()
    assert "policy" not in order and "medical" not in order
    assert order[-1] == "podcast" and order[-2] == "sports"
    # 醫學文獻/五檔 都排在 體育/Podcast 之前
    for k in ("journals", "top5"):
        assert order.index(k) < order.index("sports") < order.index("podcast")


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
    assert "NBA 關注球隊近況" in h and "波士頓塞爾提克" in h and "Finals G5" in h


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

    del fresh, stale   # 政策/醫界卡已移除(2026-08-07),時效參數不再使用
    # 新內容 → 寄
    assert mr._weekend_digest_has_content(
        {"worldcup": {"results": [1]}}, [], [], now) is True       # 世足昨日完賽
    assert mr._weekend_digest_has_content({}, [{"x": 1}], [], now) is True  # 未顯示過的 podcast
    assert mr._weekend_digest_has_content(
        {"nba": [{"date": yday, "text": "x"}]}, [], [], now) is True        # 昨日 NBA
    assert mr._weekend_digest_has_content(
        {"cpbl_scores": [{"date": yday}]}, [], [], now) is True             # 昨日中職比分

    # 舊內容/純版面內容 → 不寄(避免與週六信重複)
    assert mr._weekend_digest_has_content(
        {"nba": [{"date": "06/09", "text": "x"}]}, [], [], now) is False    # 5 天前 NBA 非新
    assert mr._weekend_digest_has_content(
        {"cpbl": [1, 2], "standings": {"美聯": [1]}}, [], [], now) is False  # 純戰績表
    assert mr._weekend_digest_has_content({}, [], [1], now) is False        # 文獻不單獨觸發
    assert mr._weekend_digest_has_content({}, [], [], now) is False


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


def test_batch30_fetch_final_survives_per_tour_cap(monkeypatch):
    """批#30 r2(Codex):繁忙賽週,某賽事 Final 之後同 tour 又有 3 場更新的普通
    賽果——Final 不得被每巡迴 3 場配額擠掉(否則冠軍行消失、反而逐場列普通輪次)。"""
    import datetime as dt

    class R:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def _match(cid, winner, day, rnd):
        return {"id": cid, "date": f"2026-06-{day}T12:00Z",
                "round": {"displayName": rnd},
                "status": {"type": {"completed": True}},
                "competitors": [{"athlete": {"shortName": winner}, "winner": True},
                                {"athlete": {"shortName": "Loser"}, "winner": False}]}

    def fake_get(url, params=None, timeout=None, **k):
        if "/atp/" in url:
            return R({"events": [
                {"shortName": "Estoril Open", "status": {"type": {}},
                 "groupings": [{"grouping": {"slug": "mens-singles"},
                                "competitions": [_match("f1", "CHAMP", 10, "Final")]}]},
                {"shortName": "Kitzbühel Open", "status": {"type": {}},
                 "groupings": [{"grouping": {"slug": "mens-singles"},
                                "competitions": [
                                    _match(f"r{d}", f"REG{d}", d, "Round 1")
                                    for d in (11, 12, 13)]}]},
            ]})
        return R({"events": []})
    monkeypatch.setattr(mr.requests, "get", fake_get)
    out = mr.fetch_tennis_digest(dt.datetime(2026, 6, 15, 8, 0, tzinfo=mr.TPE))
    winners = [r["winner"] for r in out["results"]]
    assert "CHAMP" in winners                 # Final 雖最舊仍保留(冠軍行不消失)
    assert sum(1 for r in out["results"]) <= 6 and len(
        [w for w in winners if w != "CHAMP"]) <= 2   # 配額仍 3/tour:普通賽果讓位


#: 週日測試用的公報記錄(結構取自 tw_policy_sources.parse_gazette_xml 的實際輸出)。
#: 用**成功**路徑的資料,而不是讓抓取失敗走降級——否則測試驗的是失敗分支。
_GAZETTE_STUB = [{
    "meta_id": "167273", "publisher": "財政部",
    "date_published": "中華民國115年7月24日", "comment_deadline": "",
    "title": "財政部令:修正「金融機構執行稅務用途金融帳戶資訊申報作業要點」",
    "theme_subject": "修正作業要點第8點", "keywords": ["青年安心成家方案"],
    "explain": "配合實務需要修正", "category_codes": ["510"],
    "content": "第一點 適用對象為……", "url": "https://gazette.example/1",
}]


def _stub_weekend_sources(monkeypatch, *, podcast):
    """把週日綜合的抓取/渲染都換成輕量 stub,只測控制流。"""
    monkeypatch.setattr(mr, "fetch_weather", lambda: [])
    monkeypatch.setattr(mr, "fetch_sports_digest", lambda now: {})
    monkeypatch.setattr(mr, "load_podcast_digest", lambda: podcast)
    monkeypatch.setattr(mr, "fetch_medical_journal_articles", lambda: [])
    monkeypatch.setattr(mr, "translate_journal_titles", lambda a: [])
    monkeypatch.setattr(mr, "fetch_event_calendar", lambda now: [])
    # 在地快訊(2026-07-15 新增於週日流程)也要 stub,否則既有週日測試打真 Google News
    # ——5 條查詢的重試/逾時讓測試變慢且看網路臉色(Codex review)
    monkeypatch.setattr(mr, "fetch_local_news", lambda *a, **k: {})
    # 2026-07-28:批#41/#46 之後週日流程多了三個抓取,先前沒 stub ——
    # 批#54 封鎖網路之前它們會**打真實的 gazette.nat.gov.tw / dgpa.gov.tw**;
    # 封鎖之後則固定走降級路徑(測試照樣綠,但驗的是失敗分支)。
    # 這裡給**成功**路徑的確定性資料,讓測試驗的是實際會寄出的那條路。
    monkeypatch.setattr(mr, "fetch_suspension_news", lambda *a, **k: [])
    import tw_policy_sources as _tps_mod
    monkeypatch.setattr(_tps_mod, "fetch_gazette", lambda *a, **k: _GAZETTE_STUB)
    monkeypatch.setattr(mr, "analyze_weekend_policy",
                        lambda *a, **k: "### 測試政策\n測試內容。")
    for fn in ("_render_weather_html", "_render_event_calendar_html"):
        monkeypatch.setattr(mr, fn, lambda *a, **k: "")
    for fn in ("_render_sports_html", "_render_podcast_html",
               "_render_journals_html"):
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
    # §B:週末也 push 信件存檔目錄(仍不含 history/model_history,不污染預測歷史)
    # 批#69 r1(Codex,P1):`run_manifest` 加進週日的 push 清單。原本不在裡面,
    # 而它又寫在 push **之後** → 週日寫出來的 manifest 永遠不會被 commit,
    # repo 裡的檔案停在週六;看門狗讀那個檔判定「今天有沒有跑」,週日必然誤報。
    assert pushes and pushes[0][1] == [str(mr.PODCAST_DIGEST_FILE),
                                       str(mr.POLY_HISTORY_FILE),
                                       str(mr.RUN_MANIFEST_FILE),
                                       str(mr.EMAIL_ARCHIVE_DIR)]
    assert str(mr.RUN_MANIFEST_FILE) in pushes[0][1], "manifest 沒被 push = 寫了白寫"
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
    """無新內容 → 不寄信、不動 podcast/history 狀態,但**仍要更新 manifest**。

    批#69 r2(Codex,P1):看門狗判定「今天有沒有跑」靠的就是 manifest,
    而我寫在看門狗裡的理由正是「週日不寄信是正常的,但 manifest 只要跑過
    就會更新,不會假警報」—— 少了這段,那句話在它**唯一適用的情境**下是假的,
    每個沒有新內容的週日都會收到一封失敗告警。

    只推 manifest:沒寄信就不該標記 podcast 已顯示、也不該動歷史。
    """
    import datetime as dt
    events = []
    _stub_weekend_sources(monkeypatch, podcast=[])
    monkeypatch.setattr(mr, "send_email", lambda *a: events.append("sent"))
    monkeypatch.setattr(mr, "mark_podcast_episodes_shown",
                        lambda eps: events.append("marked"))
    monkeypatch.setattr(mr, "save_history_state", lambda *a, **k: events.append("history"))
    pushes = []
    monkeypatch.setattr(mr, "_git_commit_and_push_state",
                        lambda paths, msg: pushes.append(list(paths)))

    rc = mr.run_weekend_digest(dt.datetime(2026, 6, 14, 6, 0, tzinfo=mr.TPE))

    assert rc == 0
    assert events == [], "不寄信的路徑不得動 podcast/history 狀態"
    assert pushes == [[str(mr.RUN_MANIFEST_FILE)]],         "無內容的週日沒有更新 manifest → 看門狗會誤報"


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
    assert "MLB 戰績（兩聯盟勝率前 5）" in html         # 表格化(2026-07-16)
    assert "坦帕灣光芒" in html and "56-38" in html and "0.596" in html
    assert "MLB 未來一週焦點賽程" in html
    assert "洛杉磯道奇 @ 紐約洋基" in html    # 中文隊名(2026-07-15)
    assert "特別賽事" in html                                     # 明星賽標記


def test_render_nba_week_fixtures():
    html = mr._render_sports_html(
        {"nba_fixtures": [{"text": "LAL @ BOS", "when": "10/22 08:00"}]}, htmllib)
    assert "NBA 未來一週賽程" in html
    assert "洛杉磯湖人 @ 波士頓塞爾提克" in html   # 縮寫轉繁中(2026-07-16)


def test_render_tennis_cleaned_block():
    """網球區:賽果帶日期+賽事名;賽事列表只列進行中/即將(台北日期),不再出現美東原始字串。"""
    tennis = {
        "results": [{"tour": "ATP", "tier": "大滿貫", "winner": "J. Sinner",
                     "loser": "A. Zverev", "event": "Wimbledon", "date": "07/13",
                     "round": "Final"}],   # 批#30:冠軍行以 round=Final 判定
        "tournaments": [{"name": "Canadian Open", "status": "07/20 起", "tier": "1000"}],
    }
    html = mr._render_sports_html({"tennis": tennis}, htmllib)
    # Wimbledon 有 Final 賽果 → 收斂成冠軍行(批#30 改輪次判定,比照世足)
    assert "Wimbledon(溫網)" in html and "冠軍" in html   # 大滿貫附中文(2026-07-16)
    assert "J. Sinner(辛納)" in html
    assert "決賽勝 A. Zverev(茲維列夫)" in html
    assert "進行中/即將" in html and "Canadian Open" in html and "（07/20 起）" in html
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
    assert "text-decoration:none" in h             # 黑字無底線但仍可點(使用者 2026-07-15)
    assert "text-decoration:underline" not in h
    assert "舊格式純字串標題" in h                   # 舊 state 相容


def test_mlb_fixtures_series_merged_into_one_line():
    """同對戰系列賽合併一行(07/18 TB@BOS 連 3 行=雜訊,2026-07-15 使用者要求)。"""
    import html as htmllib
    sports = {"mlb_fixtures": [
        {"when": "07/18 01:35", "text": "TB @ BOS"},
        {"when": "07/18 07:10", "text": "TB @ BOS"},
        {"when": "07/19 04:10", "text": "TB @ BOS"},
        {"when": "07/18 07:05", "text": "LAD @ NYY"},
    ]}
    h = mr._render_sports_html(sports, htmllib)
    assert h.count("坦帕灣光芒 @ 波士頓紅襪") == 1        # 系列合併成一行(中文隊名)
    assert "3 連戰" in h and "07/18、07/19" in h          # 場數+日期彙總
    assert "洛杉磯道奇 @ 紐約洋基" in h                    # 單場照常(中文隊名)


def test_tennis_finished_event_collapses_to_champion_line():
    """有 round=Final 賽果的賽事收斂成冠軍行;無 Final 者逐場列
    (批#30:冠軍判定改輪次,不再用「不在進行中列表」消去法)。"""
    import html as htmllib
    tennis = {
        "results": [
            {"tour": "ATP", "tier": "大滿貫", "winner": "J. Sinner",
             "loser": "N. Djokovic", "event": "Wimbledon", "date": "07/10",
             "round": "Semifinal"},
            {"tour": "ATP", "tier": "大滿貫", "winner": "J. Sinner",
             "loser": "A. Zverev", "event": "Wimbledon", "date": "07/12",
             "round": "Final"},                                  # 決賽 → 冠軍行
            {"tour": "WTA", "tier": "1000", "winner": "I. Swiatek",
             "loser": "A. Sabalenka", "event": "Canadian Open", "date": "07/14",
             "round": "Quarterfinal"},
        ],
        "tournaments": [{"name": "Canadian Open", "status": "進行中"}],
    }
    h = mr._render_sports_html({"tennis": tennis}, htmllib)
    # Wimbledon 有 Final → 只剩冠軍行,早期輪次不再出現
    assert "Wimbledon" in h and "冠軍" in h and "決賽勝 A. Zverev" in h
    assert "N. Djokovic" not in h
    # Canadian Open 無 Final → 逐場列,附輪次標籤
    assert "I. Swiatek" in h and "勝 A. Sabalenka" in h and "8強" in h


def test_tennis_long_named_ongoing_event_not_falsely_collapsed():
    """回歸:進行中賽事(無 Final 賽果)不得收斂成假冠軍行——批#30 起判定看
    輪次,與顯示名截斷/進行中列表完全脫鉤。"""
    import html as htmllib
    long_name = "Cerity Partners Hall of Fame Open presented by Amica Insurance"
    tennis = {
        "results": [{"tour": "ATP", "tier": "250", "winner": "甲",
                     "loser": "乙", "event": long_name[:30] + "…",
                     "event_key": long_name, "date": "07/14", "round": "Round 2"}],
        "tournaments": [{"name": long_name[:40] + "…", "event_key": long_name,
                         "status": "進行中"}],
    }
    h = mr._render_sports_html({"tennis": tennis}, htmllib)
    assert "冠軍" not in h                    # 無 Final → 不得收斂成冠軍行
    assert "甲" in h and "勝 乙" in h          # 逐場列照常


def test_batch30_tennis_no_daily_rotating_champions():
    """批#30 回歸(07/21-23 實信):進行中賽事每天的最新場次被當決賽,三天出三個
    「冠軍」。修正後:無 round=Final 一律不稱冠軍(即使賽事不在進行中列表——
    ESPN 對進行中賽事當日打完也標 post,消去法不可靠)。"""
    import html as htmllib
    tennis = {
        "results": [
            {"tour": "WTA", "tier": "250", "winner": "Y. Kabbaj",
             "loser": "E. Gorgodze", "event": "Palermo Ladies Open",
             "date": "07/23", "round": "Round 1"},
        ],
        "tournaments": [],   # ESPN 標 post → 不在進行中列表(舊邏輯會誤判已結束)
    }
    h = mr._render_sports_html({"tennis": tennis}, htmllib)
    assert "冠軍" not in h and "決賽勝" not in h
    assert "Y. Kabbaj" in h and "第1輪" in h   # 以一般賽果行呈現+輪次標籤


def test_batch30_tennis_round_zh():
    from render_utils import _tennis_round_zh
    assert _tennis_round_zh("Final") == "決賽"
    assert _tennis_round_zh("Semifinal") == "準決賽"
    assert _tennis_round_zh("Quarterfinal") == "8強"
    assert _tennis_round_zh("Round 3") == "第3輪"
    assert _tennis_round_zh("") == "" and _tennis_round_zh("Qualifying Final") == ""


# ═══ 信件調整批#4(2026-07-15)═══
def test_mlb_chinese_team_names():
    import html as htmllib
    assert mr._mlb_zh("TB @ BOS") == "坦帕灣光芒 @ 波士頓紅襪"
    assert mr._mlb_zh("LAD 61-36") == "洛杉磯道奇 61-36"
    assert mr._mlb_zh("XX @ YY") == "XX @ YY"              # 未知縮寫原樣保留
    sports = {"standings": {"美聯": [{"team": "TB", "record": "56-38", "pct": 0.596}]},
              "mlb_fixtures": [{"when": "07/18 01:35", "text": "TB @ BOS"}]}
    h = mr._render_sports_html(sports, htmllib)
    assert "坦帕灣光芒" in h and "56-38" in h and "坦帕灣光芒 @ 波士頓紅襪" in h
    assert ">TB<" not in h


def test_wc_odds_line_conversion_and_render():
    """美式賠率→隱含機率(正規化去抽水);世足賽程行附賭盤;無賠率不附。"""
    import html as htmllib
    comp = {"odds": [{
        "provider": {"name": "DraftKings"},
        "moneyline": {"home": {"close": {"odds": "+175"}},
                      "away": {"close": {"odds": "-120"}}},
        "drawOdds": {"moneyLine": 185},
    }]}
    line = mr._espn_match_odds_line(comp, {"home": "英格蘭", "away": "阿根廷"})
    assert line.startswith("賭盤(90分鐘):") and "(DraftKings 運彩)" in line   # 標明運彩來源(2026-07-16)
    # 隱含:home 100/275=.3636、away 120/220=.5455、draw 100/285=.3509;正規化後 ~29/44/28
    assert "阿根廷 43%" in line and "英格蘭 29%" in line and "和 28%" in line
    assert mr._espn_match_odds_line({}, {}) == ""           # 無賠率安全回空
    wc = {"fixtures": [{"text": "阿根廷 vs 英格蘭", "kickoff": "07/16 03:00",
                        "round": "4 強", "odds": line}]}
    h = mr._render_sports_html({"worldcup": wc}, htmllib)
    assert "賭盤(90分鐘):" in h and "DraftKings" in h


def test_fetch_local_news_and_render(monkeypatch):
    """在地快訊卡:各主題抓 2 則(標題+連結),渲染黑字可點;逐主題失敗略過、無資料回空。"""
    import datetime as dt
    now_gmt = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    class Feed:
        def __init__(self, url):
            # 判別詞用建設 query 獨有的「重大建設」(斗六/雲林已併入建設主題,2026-07-16)
            if "%E9%87%8D%E5%A4%A7%E5%BB%BA%E8%A8%AD" in url:
                self.entries = [{"title": "斗六長照大樓爭取9億經費",
                                 "link": "https://news.example.com/douliu",
                                 "published": now_gmt}]
            else:
                raise TimeoutError("其他主題模擬失敗")
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, *a, **k: Feed(url))
    out = mr.fetch_local_news()
    assert "建設" in out and out["建設"][0]["link"]
    assert len(out) == 1                                   # 失敗主題略過不炸
    h = mr._render_local_news_html(out)
    assert "在地快訊" in h and "斗六長照大樓" in h
    assert "<a href='https://news.example.com/douliu'" in h
    assert "text-decoration:none" in h                     # 黑字可點
    assert mr._render_local_news_html({}) == ""


def test_local_queries_cover_douliu():
    labels = {r[0]: r[1] for r in mr.LOCAL_NEWS_QUERIES}
    # 斗六/雲林獨立主題已撤(2026-07-16):斗六詞散入建設/房市/學區主題
    assert "斗六/雲林" not in labels
    assert "斗六" in labels["建設"] and "斗六" in labels["房市"] and "斗六" in labels["學區/文教"]
    assert "斗六" in mr.OTHER_SECTOR_QUERIES["房市-中彰投"]   # 九段素材也含斗六
    assert "斗六" in mr.OTHER_SECTOR_QUERIES["建設-中彰投"]
    # 批#9:房市加深(預售屋/營建成本)+ 中彰投建商動態查詢
    assert "預售屋" in mr.OTHER_SECTOR_QUERIES["房市-中彰投"]
    assert "營建成本" in mr.OTHER_SECTOR_QUERIES["房市-中彰投"]
    assert "建商-中彰投" in mr.OTHER_SECTOR_QUERIES
    assert "總太" in mr.OTHER_SECTOR_QUERIES["建商-中彰投"]


def test_local_news_keeps_25h_old_items(monkeypatch):
    """回歸(Codex review):when=1d 伺服器端只回 24h 內,24-30h 新聞被吃掉——
    改 when=2d 抓寬、cutoff 30h 精確過濾:25h 前的新聞須保留、31h 前的須剔除。"""
    import datetime as dt

    def parsed(hours_ago):
        # 真 feedparser 會給 published_parsed(struct_time,UTC);cutoff 靠它過濾
        ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
        return ts.timetuple()

    captured = {}

    class Feed:
        def __init__(self, url):
            captured["url"] = url
            if "%E9%87%8D%E5%A4%A7%E5%BB%BA%E8%A8%AD" in url:   # 斗六 query
                self.entries = [{"title": "25小時前的斗六建設新聞", "link": "https://x/a",
                                 "published_parsed": parsed(25)},
                                {"title": "31小時前的過期新聞", "link": "https://x/b",
                                 "published_parsed": parsed(31)}]
            else:
                self.entries = []
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, *a, **k: Feed(url))
    out = mr.fetch_local_news()
    titles = [i["title"] for i in out.get("建設", [])]
    assert "25小時前的斗六建設新聞" in titles     # 24-30h 視窗保留
    assert "31小時前的過期新聞" not in titles     # 30h cutoff 精確剔除
    assert "when%3A2d" in captured["url"] or "when:2d" in captured["url"]   # 查詢抓寬一天


def test_weekend_digest_includes_local_news_card(monkeypatch):
    """週日綜合信也要有在地快訊卡(掛天氣後;Codex review:原先週日流程固定缺席)。"""
    import datetime as dt
    _stub_weekend_sources(monkeypatch, podcast=[{"show": "股癌", "guid": "ep1"}])
    monkeypatch.setattr(mr, "fetch_local_news", lambda *a, **k: {
        "建設": [{"title": "斗六長照大樓新進度", "link": "https://x/d"}]})
    monkeypatch.setattr(mr, "send_email", lambda *a: None)
    monkeypatch.setattr(mr, "mark_podcast_episodes_shown", lambda e: None)
    monkeypatch.setattr(mr, "save_history_state", lambda *a, **k: None)
    monkeypatch.setattr(mr, "_git_commit_and_push_state", lambda *a, **k: None)
    captured = {}
    real_render = mr.render_weekend_digest_html

    def spy(*a, **k):
        html = real_render(*a, **k)
        captured["html"] = html
        return html
    monkeypatch.setattr(mr, "render_weekend_digest_html", spy)
    rc = mr.run_weekend_digest(dt.datetime(2026, 6, 14, 6, 0, tzinfo=mr.TPE))
    assert rc == 0
    assert "在地快訊" in captured["html"] and "斗六長照大樓新進度" in captured["html"]


# ═══ 批#6(2026-07-15)═══
def test_espn_week_fixtures_attach_odds(monkeypatch):
    """MLB/NBA 週賽程附賭盤(縮寫組行,渲染端再中文化);無賠率場次 odds 為空。"""
    import datetime as dt
    now = dt.datetime.now(mr.TPE)
    iso = (now + dt.timedelta(days=2)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [{
                "shortName": "TB @ BOS", "name": "TB @ BOS", "date": iso,
                "season": {"slug": "regular-season"},
                "status": {"type": {"state": "pre"}},
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "BOS"}},
                        {"homeAway": "away", "team": {"abbreviation": "TB"}}],
                    "odds": [{"provider": {"name": "DraftKings"},
                              "moneyline": {"home": {"close": {"odds": "-150"}},
                                            "away": {"close": {"odds": "+130"}}}}],
                }],
            }]}
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: R())
    out = mr.fetch_mlb_week_fixtures(top_teams={"TB", "BOS"})
    assert out and out[0]["odds"].startswith("賭盤:")
    assert "BOS" in out[0]["odds"] and "(DraftKings 運彩)" in out[0]["odds"]
    # 渲染:系列行下方顯示賭盤且中文化
    import html as htmllib
    h = mr._render_sports_html({"mlb_fixtures": out}, htmllib)
    assert "賭盤:" in h and "波士頓紅襪" in h.split("賭盤:")[1][:60]


def test_local_card_renamed_and_above_policy_section():
    """在地快訊卡:標題精簡為「在地快訊」;位置在台灣政策/醫界卡上方(每日報)。"""
    quotes = {**_quotes_for_night(),
              "LOCAL_NEWS": {"交通": [{"title": "台74 崇德匝道夜間交管", "link": "https://x/t"}]},
              "TW_DAILY_INTELLIGENCE": {"policy": [], "medical": [],
                                        "policy_window": "近一月", "medical_window": "昨日"}}
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, "x", "2026-07-16", "每日報")
    assert "在地快訊</h2>" in html                    # 精簡標題(無城市後綴);h2 卡片化(2026-07-16)
    i_local = html.find("在地快訊")
    i_policy = html.find("台灣政策近月走向")
    assert 0 < i_local < i_policy or i_policy == -1    # 在政策卡上方
    assert "台74 崇德匝道夜間交管" in html


# ═══ 批#8(2026-07-15)═══
def test_typhoon_signal_thresholds():
    """颱風風雨門檻:達標(陣風≥89/風≥50/雨≥350)紅字警示、接近(80%)提醒、平日空。"""
    calm = [{"name": "彰化市", "wind": 15, "gust": 41, "rain_sum": 9.8}]
    assert mr._typhoon_signal(calm) == ""
    near = [{"name": "彰化市", "wind": 42, "gust": 75, "rain_sum": 120}]
    out = mr._typhoon_signal(near)
    assert "接近停班停課參考標準" in out
    hit = [{"name": "台中北區", "wind": 55, "gust": 95, "rain_sum": 200}]
    out2 = mr._typhoon_signal(hit)
    assert "已達停班停課參考標準" in out2
    # 免責固定附註(Codex review):任一警示都須聲明以公告為準
    for o in (out, out2):
        assert "以縣市政府公告為準" in o


def test_fetch_suspension_news_filters_regions_and_noise(monkeypatch):
    """停班停課新聞:須含在地縣市名+停班/停課字樣;社論與外縣市剔除。"""
    import datetime as dt
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).timetuple()

    class Feed:
        entries = [
            {"title": "彰化縣明日停止上班停止上課", "link": "https://x/chc",
             "published_parsed": ts},
            {"title": "台中市宣布明天照常上班上課", "link": "https://x/txg",
             "published_parsed": ts},
            {"title": "（社論）讓颱風假回歸科學治理", "link": "https://x/op",
             "published_parsed": ts},
            {"title": "台北市停班停課", "link": "https://x/tpe",
             "published_parsed": ts},
        ]
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout", lambda *a, **k: Feed())
    monkeypatch.setattr(mr, "fetch_dgpa_suspension", lambda: None)   # 官方失敗→新聞備援
    out = mr.fetch_suspension_news()
    titles = [i["title"] for i in out]
    assert "彰化縣明日停止上班停止上課" in titles
    assert "台中市宣布明天照常上班上課" in titles      # 照常公告也要顯示(確定性資訊)
    assert all("社論" not in t and "台北市" not in t for t in titles)


def test_suspension_official_source_wins_and_stale_news_dropped(monkeypatch):
    """批#22(2026-07-19 使用者回報):官方(人事總處)為準——官方頁正常且
    中彰投雲無公告 → 空(新聞一概不收,週六晚「今晚停班停課」不再誤上);
    官方失敗 → 新聞備援,但昨日發布且無「明天/明日」字樣者剔除。"""
    import datetime as dt
    # 官方頁:只有花蓮 → 中彰投雲=確定無公告 → []
    yesterday_evening = (dt.datetime.now(mr.TPE).replace(
        hour=0, minute=0) - dt.timedelta(hours=4)).astimezone(
        dt.timezone.utc).timetuple()   # 昨日 20:00 TPE

    class Feed:
        entries = [
            {"title": "致災性豪雨強襲!台中市今晚停班停課 林佳龍說明原因",
             "link": "https://x/stale", "published_parsed": yesterday_evening},
            {"title": "南投縣宣布明天停止上班上課", "link": "https://x/ok",
             "published_parsed": yesterday_evening},
        ]
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda *a, **k: Feed())
    monkeypatch.setattr(mr, "fetch_dgpa_suspension",
                        lambda: [])   # 官方正常、無中彰投雲公告
    assert mr.fetch_suspension_news() == []
    # 官方有中彰投雲公告 → 原樣採用
    official = [{"title": "人事總處公告:臺中市 今天停止上班、停止上課",
                 "link": mr._DGPA_NDS_URL}]
    monkeypatch.setattr(mr, "fetch_dgpa_suspension", lambda: official)
    assert mr.fetch_suspension_news() == official
    # 官方失敗 → 新聞備援:昨日「今晚」剔除、昨日「明天」保留、
    # 昨日「絕對日期=今日」保留(Codex r1:縣市公告常用絕對日期)
    import datetime as _dt
    _now = _dt.datetime.now(mr.TPE)
    Feed.entries.append(
        {"title": f"雲林縣宣布{_now.month}月{_now.day}日停止上班上課",
         "link": "https://x/abs", "published_parsed": yesterday_evening})
    monkeypatch.setattr(mr, "fetch_dgpa_suspension", lambda: None)
    titles = [i["title"] for i in mr.fetch_suspension_news()]
    assert "致災性豪雨強襲!台中市今晚停班停課 林佳龍說明原因" not in titles
    assert "南投縣宣布明天停止上班上課" in titles
    assert f"雲林縣宣布{_now.month}月{_now.day}日停止上班上課" in titles
    # r2 裸日形式:「19日停班」收;「6月19日」他月不收(月前綴擋)
    Feed.entries.append(
        {"title": f"彰化縣{_now.day}日停止上班上課", "link": "https://x/bare",
         "published_parsed": yesterday_evening})
    _other_month = 6 if _now.month != 6 else 5
    Feed.entries.append(
        {"title": f"台中市{_other_month}月{_now.day}日停班回顧", "link": "https://x/om",
         "published_parsed": yesterday_evening})
    titles2 = [i["title"] for i in mr.fetch_suspension_news()]
    assert f"彰化縣{_now.day}日停止上班上課" in titles2
    assert f"台中市{_other_month}月{_now.day}日停班回顧" not in titles2
    # r4 邊界吞入:斜線長日期不得被今日短日期吞(如今日 7/1 不得誤中 7/19)、
    # 前綴月不得誤中(1月9日 vs 11月9日)——用合成日期直接驗 regex 語意
    import re as _re
    def _hit(title, m, d):
        return bool(_re.search(rf"(?<!\d){m}月{d}日", title)
                    or _re.search(rf"(?<!\d){m}/{d}(?!\d)", title)
                    or _re.search(rf"(?<!\d)(?<!月){d}日", title))
    assert _hit("台中市7/1停班", 7, 1)
    assert not _hit("台中市7/19停班回顧", 7, 1)     # 今日 7/1 不吞 7/19
    assert _hit("南投縣1月9日停課", 1, 9)
    assert not _hit("南投縣11月9日停課回顧", 1, 9)  # 1月9日不吞 11月9日


def test_dgpa_page_parsing(monkeypatch):
    """人事總處頁解析:今日頁+中彰投雲列 → 收;只有外縣市 → [];
    頁面日期非今日 → None(未知,退備援)。"""
    import datetime as dt
    today = dt.datetime.now(mr.TPE).date()
    roc = f"{today.year - 1911}年 {today.month}月 {today.day}日 天然災害停止上班及上課情形"

    def page(date_str, rows):
        trs = "".join(f"<TR><TD>{c}</TD><TD>{s}</TD></TR>" for c, s in rows)
        return (f"<html>{date_str}"
                f'<TABLE id="Table"><TR><TH>縣市名稱</TH><TH>情形</TH></TR>'
                f"{trs}</TABLE></html>").encode("utf-8")

    monkeypatch.setattr(mr, "_http_get_relaxed_strict",
                        lambda url, timeout=15: page(roc, [
                            ("花蓮縣", "萬榮鄉今天停止上班、停止上課。"),
                            ("臺中市", "今天停止上班、停止上課。")]))
    out = mr.fetch_dgpa_suspension()
    assert out and "臺中市" in out[0]["title"] and "花蓮" not in str(out)
    monkeypatch.setattr(mr, "_http_get_relaxed_strict",
                        lambda url, timeout=15: page(roc, [
                            ("花蓮縣", "萬榮鄉今天停止上班、停止上課。")]))
    assert mr.fetch_dgpa_suspension() == []
    stale = "114年 7月 18日 天然災害停止上班及上課情形"
    monkeypatch.setattr(mr, "_http_get_relaxed_strict",
                        lambda url, timeout=15: page(stale, []))
    assert mr.fetch_dgpa_suspension() is None


def test_weather_card_shows_signal_and_suspension():
    locs = [{"name": "彰化市", "t_min": 25, "t_max": 30, "rain_prob": 90,
             "label": "雷雨", "wind": 55, "gust": 95, "rain_sum": 200}]
    susp = [{"title": "彰化縣停止上班上課", "link": "https://x/chc"}]
    h = mr._render_weather_html(locs, susp)
    assert "已達停班停課參考標準" in h and "#b91c1c" in h    # 紅字警示
    assert "彰化縣停止上班上課" in h and "https://x/chc" in h  # 公告連結
    # 平常日:無警示無公告
    calm = [{"name": "彰化市", "t_min": 25, "t_max": 33, "rain_prob": 10,
             "label": "晴", "wind": 10, "gust": 30, "rain_sum": 0}]
    h2 = mr._render_weather_html(calm, [])
    assert "停班停課" not in h2


def test_wc_knockout_upcoming_rows_carry_odds():
    """世足淘汰賽對戰表:未賽列附賭盤(決賽列賭盤=冠軍機率);已完賽列不附。"""
    import html as htmllib
    wc = {"knockout": [{"name": "決賽", "games": [
        {"text": "阿根廷 vs 西班牙", "when": "07/20 03:00", "done": False,
         "odds": "賭盤(90分鐘):阿根廷 45%・和 25%・西班牙 30%(DraftKings)"},
        {"text": "法國 0 : 2 西班牙", "when": "07/15", "done": True,
         "odds": "賭盤:不該顯示"},
    ]}]}
    h = mr._render_sports_html({"worldcup": wc}, htmllib)
    assert "賭盤(90分鐘):阿根廷 45%" in h            # 三向含和=90分鐘市場,明確標示
    assert "不該顯示" not in h                        # 已完賽列不附


def test_wc_odds_line_labels_90min_when_draw_present():
    """含和局=足球 90 分鐘三向市場 → 標「賭盤(90分鐘)」;無和局(美棒籃)標「賭盤」。"""
    soccer = {"odds": [{"provider": {"name": "DK"},
                        "moneyline": {"home": {"close": {"odds": "+175"}},
                                      "away": {"close": {"odds": "-120"}}},
                        "drawOdds": {"moneyLine": 185}}]}
    assert mr._espn_match_odds_line(soccer, {"home": "甲", "away": "乙"}).startswith("賭盤(90分鐘):")
    us = {"odds": [{"provider": {"name": "DK"},
                    "moneyline": {"home": {"close": {"odds": "-150"}},
                                  "away": {"close": {"odds": "+130"}}}}]}
    assert mr._espn_match_odds_line(us, {"home": "甲", "away": "乙"}).startswith("賭盤:")


def test_weather_card_suspension_only_still_renders():
    """天氣源掛掉但有停班停課公告 → 卡仍渲染公告(重要資訊不可消失,Codex review)。"""
    susp = [{"title": "彰化縣停止上班上課", "link": "https://x/chc"}]
    h = mr._render_weather_html([], susp)
    assert "彰化縣停止上班上課" in h and "天氣資料暫缺" in h
    assert mr._render_weather_html([], []) == ""       # 兩者皆空才回空


def test_suspension_window_excludes_stale_daytime_news(monkeypatch):
    """昨日白天發布的「今日照常」(其今日=昨天)不得跨日顯示;昨晚 20 時公告要收
    (Codex review:視窗=台北昨日 16:00 起)。"""
    import datetime as dt
    now_tpe = dt.datetime.now(mr.TPE)

    def parsed_at_tpe(days_ago, hour):
        ts = (now_tpe - dt.timedelta(days=days_ago)).replace(hour=hour, minute=0)
        return ts.astimezone(dt.timezone.utc).timetuple()

    class Feed:
        entries = [
            {"title": "彰化縣今日照常上班上課", "link": "https://x/stale",
             "published_parsed": parsed_at_tpe(1, 10)},   # 昨日上午=指昨天 → 排除
            {"title": "台中市明天停止上班上課", "link": "https://x/fresh",
             "published_parsed": parsed_at_tpe(1, 20)},   # 昨晚 20 時=指今天 → 保留
        ]
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout", lambda *a, **k: Feed())
    monkeypatch.setattr(mr, "fetch_dgpa_suspension", lambda: None)   # 批#22:備援路徑
    titles = [i["title"] for i in mr.fetch_suspension_news()]
    assert "台中市明天停止上班上課" in titles
    assert "彰化縣今日照常上班上課" not in titles


# ===== 批#9(2026-07-16):Polymarket 賭盤 / 在地模糊去重 =====

def test_poly_outright_parses_sorts_and_filters(monkeypatch):
    """outright 解析:Yes 價→機率%、依機率降序、剔除 closed/佔位項/低機率;中文對照。"""
    fake_event = {"markets": [
        {"groupItemTitle": "Argentina", "outcomePrices": '["0.4195", "0.5805"]'},
        {"groupItemTitle": "Spain", "outcomePrices": '["0.5795", "0.4205"]'},
        {"groupItemTitle": "France", "outcomePrices": '["0", "1"]', "closed": True},
        {"groupItemTitle": "Team AG", "outcomePrices": '["0.5", "0.5"]'},
        {"groupItemTitle": "Other", "outcomePrices": '["0.02", "0.98"]'},
        {"groupItemTitle": "England", "outcomePrices": '["0.001", "0.999"]'},   # < min_prob
        {"groupItemTitle": "Brazil", "outcomePrices": None},                    # 解析失敗
    ]}
    monkeypatch.setattr(mr, "_poly_events", lambda params: [fake_event])
    rows = mr._poly_outright("world-cup-winner", mr._WC_TEAM_ZH, top=4)
    assert [(r["name"], r["prob"]) for r in rows] == [("西班牙", 58), ("阿根廷", 42)]
    assert mr._poly_prob_line(rows) == "西班牙 58%・阿根廷 42%"
    # event 缺席安全回空
    monkeypatch.setattr(mr, "_poly_events", lambda params: [])
    assert mr._poly_outright("no-such-slug") == []


def test_fetch_polymarket_sports_cpbl_today_only(monkeypatch):
    """中職單場:只取 slug 日期=今天的場次;0/1 價(已定案/無報價)剔除;隊名轉繁中簡稱。"""
    import datetime as dt
    now = dt.datetime(2026, 7, 16, 6, 0, tzinfo=mr.TPE)

    def fake_events(params):
        if params.get("tag_slug") == "cpbl":
            return [
                {"slug": "cpbl-rak-uni-2026-07-16", "markets": [
                    {"outcomes": '["Rakuten Monkeys", "Uni-President Lions"]',
                     "outcomePrices": '["0.46", "0.54"]'}]},
                {"slug": "cpbl-chi-wei-2026-07-10", "markets": [       # 舊場次(延賽殘留)
                    {"outcomes": '["Chinatrust Brothers", "Wei Chuan Dragons"]',
                     "outcomePrices": '["0.4", "0.6"]'}]},
                {"slug": "cpbl-tsg-fub-2026-07-16", "markets": [       # 已定案 0/1 → 剔除
                    {"outcomes": '["TSG Hawks", "Fubon Guardians"]',
                     "outcomePrices": '["0", "1"]'}]},
            ]
        return []   # 其他盤(世足/futures)這裡不測
    monkeypatch.setattr(mr, "_poly_events", fake_events)
    out = mr.fetch_polymarket_sports(now)
    assert out.get("cpbl_games") == [
        {"teams": ["樂天", "統一"], "probs": [46, 54]}]


def test_attach_cpbl_poly_odds_matches_full_and_short_names():
    """賭盤掛載:Yahoo 全名「統一7-ELEVEn獅」與簡稱「樂天」都要能對上;非今日不掛。"""
    fixtures = [
        {"away": "統一7-ELEVEn獅", "home": "樂天桃猿", "date": "07/16", "start": "18:35"},
        {"away": "台鋼", "home": "中信", "date": "07/21", "start": "18:35"},
    ]
    poly = {"cpbl_games": [{"teams": ["樂天", "統一"], "probs": [46, 54]},
                           {"teams": ["台鋼", "中信"], "probs": [50, 50]}]}
    mr._attach_cpbl_poly_odds(fixtures, poly, "07/16")
    assert fixtures[0]["odds"] == "賭盤:樂天 46%・統一 54%(Polymarket)"
    assert "odds" not in fixtures[1]                       # 非今日場次不掛
    mr._attach_cpbl_poly_odds([], {}, "07/16")             # 空輸入不炸


def test_render_sports_poly_lines():
    """渲染:世足「冠軍機率」(語意≠90分鐘賭盤)、MLB 世界大賽/NBA/美網冠軍盤、中職賽程賭盤。"""
    sports = {
        "worldcup": {"knockout": [{"name": "決賽", "games": [
            {"when": "07/20 03:00", "text": "阿根廷 vs 西班牙", "done": False}]}]},
        "cpbl_fixtures": [{"away": "統一", "home": "樂天", "date": "07/16",
                           "start": "18:35",
                           "odds": "賭盤:統一 54%・樂天 46%(Polymarket)"}],
        "standings": {"美聯": [{"team": "TB", "record": "56-38", "pct": 0.596}]},
        "nba_offseason": "NBA 休賽季:自由市場與夏季聯賽進行中。",
        "tennis": {"tournaments": [{"name": "Generali Open", "status": "07/20 起"}]},
        "poly": {
            "wc_champion": [{"name": "西班牙", "prob": 58}, {"name": "阿根廷", "prob": 42}],
            "mlb_ws": [{"name": "道奇", "prob": 30}, {"name": "洋基", "prob": 13}],
            "nba_champ": [{"name": "雷霆", "prob": 27}, {"name": "馬刺", "prob": 19}],
            "tennis_m": [{"name": "Jannik Sinner", "prob": 52}],
            "tennis_w": [{"name": "Aryna Sabalenka", "prob": 22}],
        },
    }
    h = mr._render_sports_html(sports, htmllib)
    assert "冠軍機率" in h and "西班牙 58% ・ 阿根廷 42%" in h and "Polymarket 預測市場" in h
    assert "世界大賽冠軍盤" in h and "道奇 30% ・ 洋基 13%" in h
    assert "2026-27 冠軍盤" in h and "雷霆 27% ・ 馬刺 19%" in h
    assert "美網冠軍盤" in h and "男:辛納 52%" in h and "女:莎巴倫卡 22%" in h   # 批#14:中文為主;批#15:男女各一行
    assert "賭盤:統一 54%・樂天 46%(Polymarket)" in h
    # 沒有 poly 資料 → 各行自然缺席,不崩
    sports.pop("poly")
    h2 = mr._render_sports_html(sports, htmllib)
    assert "冠軍機率" not in h2 and "世界大賽冠軍盤" not in h2


def test_local_title_fuzzy_dedup(monkeypatch):
    """同一事件被兩家媒體改寫不同標題 → 第二則剔除;不同事件不誤殺(overlap≥0.50)。"""
    a = "中醫大附醫修正性手術 助婦人重拾自然嗓音 - Yahoo新聞"
    b = ("女子甲狀腺手術後失聲二十多年 中醫大附醫修正性手術協助重拾自然嗓音 "
         "| 中廣新聞網 - LINE TODAY")
    b2 = "討論牆 | 中醫大附醫修正性手術 助婦人重拾自然嗓音 - LINE TODAY"
    c = "中捷藍線首件主線土建工程決標 預計8月開工 - 自由時報"
    seen = [mr._local_seen_entry(a)]
    assert mr._local_title_is_dup(b, seen) is True         # 同事件改寫 → 重複
    assert mr._local_title_is_dup(b2, seen) is True        # 同標題加「討論牆 |」前綴 → 重複
    assert mr._local_title_is_dup(c, seen) is False        # 不同事件 → 保留
    assert mr._local_title_is_dup("", seen) is False       # 空標題安全

    # 端到端:同事件兩則進 feed,只留一則
    import datetime as dt
    now_gmt = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    class Feed:
        def __init__(self, url):
            if "%E9%87%8D%E5%A4%A7%E5%BB%BA%E8%A8%AD" in url:   # 建設 query
                self.entries = [{"title": a, "link": "https://x/1", "published": now_gmt},
                                {"title": b, "link": "https://x/2", "published": now_gmt},
                                {"title": c, "link": "https://x/3", "published": now_gmt}]
            else:
                self.entries = []
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, *a_, **k: Feed(url))
    out = mr.fetch_local_news()
    titles = [i["title"] for i in out.get("建設", [])]
    assert len(titles) == 2 and titles[0].startswith("中醫大附醫") and titles[1].startswith("中捷藍線")


def test_render_sports_poly_survives_when_legacy_sources_all_fail():
    """回歸(Codex review 批#9):傳統體育源全掛、只剩 Polymarket → 體育卡不得消失,
    各冠軍盤獨立渲染(不依附戰績/休賽季/賽果區塊)。"""
    sports = {"news": {}, "poly": {
        "wc_champion": [{"name": "西班牙", "prob": 58}],
        "mlb_ws": [{"name": "道奇", "prob": 30}],
        "nba_champ": [{"name": "雷霆", "prob": 27}],
        "tennis_m": [{"name": "Jannik Sinner", "prob": 52}],
    }}
    h = mr._render_sports_html(sports, htmllib)
    assert h != ""                                         # 卡片存活
    assert "世界盃足球賽" in h and "冠軍機率" in h and "西班牙 58%" in h
    assert "世界大賽冠軍盤" in h and "道奇 30%" in h
    assert "2026-27 冠軍盤" in h and "雷霆 27%" in h
    assert "美網冠軍盤" in h and "男:辛納 52%" in h
    # 只有 cpbl_games(無賽程行可掛)→ 無可渲染內容,卡片仍回空
    assert mr._render_sports_html(
        {"news": {}, "poly": {"cpbl_games": [{"teams": ["樂天", "統一"],
                                              "probs": [46, 54]}]}}, htmllib) == ""


def test_render_sports_nba_champ_not_duplicated_when_embedded():
    """冠軍盤已嵌進休賽季區塊 → 不得再獨立渲染一次(NBA 標題只出現一次)。"""
    sports = {"news": {}, "nba_offseason": "NBA 休賽季:自由市場與夏季聯賽進行中。",
              "poly": {"nba_champ": [{"name": "雷霆", "prob": 27}]}}
    h = mr._render_sports_html(sports, htmllib)
    assert h.count("2026-27 冠軍盤") == 1


def test_local_short_titles_same_entity_not_deduped():
    """回歸(Codex review 批#9):兩則短標題共用實體名(台中捷運藍線)但事件不同
    → 不得誤殺;短標題(bigram<12)須近乎全同(≥0.85)才算重複。"""
    a = "台中捷運藍線進度曝光"
    b = "台中捷運藍線大舉徵才"
    seen = [mr._local_seen_entry(a)]
    assert mr._local_title_is_dup(b, seen) is False        # 不同事件 → 保留
    assert mr._local_title_is_dup("台中捷運藍線進度曝光", seen) is True   # 全同 → 重複
    assert mr._local_title_is_dup("台中捷運藍線進度曝光 - 自由時報", seen) is True


# ===== 批#10(2026-07-16):MLB 單場 Polymarket / 預測市場快照 / 排版中文化 =====

def test_attach_mlb_poly_odds_slug_and_market_pick(monkeypatch):
    """MLB 單場:slug=mlb-{客}-{主}-{美東日};CHW→cws 縮寫修正;只認兩隊名市場
    (跳過 Yes/No prop);未命中保留原 DraftKings 行。"""
    captured = []

    def fake_events(params):
        captured.append(params.get("slug"))
        if params.get("slug") == "mlb-lad-nyy-2026-07-17":
            return [{"markets": [
                {"outcomes": '["Yes", "No"]', "outcomePrices": '["0.51", "0.49"]'},
                {"outcomes": '["Los Angeles Dodgers", "New York Yankees"]',
                 "outcomePrices": '["0.515", "0.485"]'},
            ]}]
        return []
    monkeypatch.setattr(mr, "_poly_events", fake_events)
    fixtures = [
        {"text": "LAD @ NYY", "when": "07/18 07:05", "odds": "賭盤:舊行",
         "away_abbr": "LAD", "home_abbr": "NYY", "date_us": "2026-07-17"},
        {"text": "CHW @ TOR", "when": "07/18 07:15", "odds": "賭盤:DK行(DraftKings 運彩)",
         "away_abbr": "CHW", "home_abbr": "TOR", "date_us": "2026-07-17"},
    ]
    mr._attach_mlb_poly_odds(fixtures)
    assert fixtures[0]["odds"] == "賭盤:道奇 52%・洋基 48%(Polymarket)"   # 正規化+暱稱中文
    assert "mlb-cws-tor-2026-07-17" in captured                       # CHW→cws
    assert fixtures[1]["odds"] == "賭盤:DK行(DraftKings 運彩)"          # 未命中保留 DraftKings


def test_fetch_polymarket_pulse_rows(monkeypatch):
    """快照:Fed 決議動態取最近未來場、二元盤 Yes 機率、SPX 前2區間、TSMC 財報盤。"""
    def fake_search(query, limit=8):
        if "Fed" in query:
            return [
                {"title": "Fed Decision in July?", "slug": "fed-jul",
                 "endDate": "2026-07-01T00:00:00Z"},          # 已過 → 不取
                {"title": "Fed Decision in September?", "slug": "fed-sep",
                 "endDate": "2026-09-16T00:00:00Z"},
                {"title": "Fed Decision in October?", "slug": "fed-oct",
                 "endDate": "2026-10-28T00:00:00Z"},
            ]
        if "TSMC" in query:
            return [{"title": "Will TSMC (TSM) beat quarterly earnings?",
                     "slug": "tsm-beat", "endDate": "2026-07-16T23:00:00Z"}]
        return []

    def fake_events(params):
        slug = params.get("slug")
        if slug == "fed-sep":
            return [{"markets": [
                {"groupItemTitle": "No change", "outcomePrices": '["0.655", "0.345"]'},
                {"groupItemTitle": "25 bps increase", "outcomePrices": '["0.285", "0.715"]'},
                {"groupItemTitle": "25 bps decrease", "outcomePrices": '["0.04", "0.96"]'},
            ]}]
        if slug == "fed-rate-hike-in-2026":
            return [{"markets": [{"outcomePrices": '["0.515", "0.485"]'}]}]
        if slug == "spx-close-dec-2026":
            return [{"markets": [
                {"groupItemTitle": ">$8,000", "outcomePrices": '["0.275", "0.725"]'},
                {"groupItemTitle": "$7,500-$8,000", "outcomePrices": '["0.195", "0.805"]'},
                {"groupItemTitle": "<$6,000", "outcomePrices": '["0.14", "0.86"]'},
            ]}]
        if slug == "tsm-beat":
            # question 欄位必填:_poly_binary_detail 依 question_re 確定性選盤
            # (三審 P1-7,不再盲取 markets[0])
            return [{"markets": [{
                "question": "Will TSMC (TSM) beat quarterly earnings?",
                "outcomePrices": '["0.9995", "0.0005"]'}]}]
        return []
    monkeypatch.setattr(mr, "_poly_search_events", fake_search)
    monkeypatch.setattr(mr, "_poly_events", fake_events)
    import datetime as dt
    rows = mr.fetch_polymarket_pulse(dt.datetime(2026, 7, 16, 6, 0, tzinfo=mr.TPE))
    by_label = {r["label"]: r["detail"] for r in rows}
    assert by_label["Fed 9月決議"] == "利率不變 66%・升息1碼 28%・降息1碼 4%"
    assert by_label["2026 年內 Fed 再升息"] == "機率 52%"
    assert by_label["台積電本季財報優於市場預期"] == "機率 100%"
    # 批#14 使用者刪減:衰退/台海/賴清德/S&P/眾參院 不再出現
    for gone in ("美國 2026 年底前衰退", "S&P 500 年底收盤區間",
                 "美國期中選舉眾院多數黨", "中國 2026 年內封鎖台海"):
        assert gone not in by_label

    h = mr._render_poly_pulse_html(rows)
    assert "預測市場觀點(Polymarket)" in h and "Fed 9月決議" in h
    assert "不納入本報任何模型計分" in h
    assert mr._render_poly_pulse_html([]) == ""


def test_local_news_card_styled_like_other_sections():
    """在地卡美化(2026-07-16):h2 標題 + 白底框 + 主題色塊標籤;連結仍黑字可點。"""
    h = mr._render_local_news_html(
        {"建設": [{"title": "中捷藍線決標", "link": "https://x/1"}]})
    assert "在地快訊</h2>" in h                      # 與其他區塊同款 h2
    assert "border-radius:10px" in h                 # 白底框卡
    assert "background:#e0f2fe" in h                 # 主題色塊標籤
    assert "text-decoration:none" in h and "https://x/1" in h


def test_attach_mlb_poly_odds_rejects_non_team_two_outcome_props(monkeypatch):
    """回歸(Codex review 批#10):Over/Under 等兩結果 prop 排在勝負盤前面,
    不得被誤當兩隊勝率——兩個結果都必須是已知 MLB 隊名。"""
    def fake_events(params):
        if params.get("slug") == "mlb-lad-nyy-2026-07-17":
            return [{"markets": [
                {"outcomes": '["Over", "Under"]', "outcomePrices": '["0.6", "0.4"]'},
                {"outcomes": '["Los Angeles Dodgers", "New York Yankees"]',
                 "outcomePrices": '["0.515", "0.485"]'},
            ]}]
        if params.get("slug") == "mlb-tb-bos-2026-07-17":
            return [{"markets": [   # 只有 prop、沒有勝負盤 → 不掛,保留原行
                {"outcomes": '["Over", "Under"]', "outcomePrices": '["0.6", "0.4"]'}]}]
        return []
    monkeypatch.setattr(mr, "_poly_events", fake_events)
    fixtures = [
        {"text": "LAD @ NYY", "when": "07/18 07:05",
         "away_abbr": "LAD", "home_abbr": "NYY", "date_us": "2026-07-17"},
        {"text": "TB @ BOS", "when": "07/18 01:35", "odds": "賭盤:DK(DraftKings 運彩)",
         "away_abbr": "TB", "home_abbr": "BOS", "date_us": "2026-07-17"},
    ]
    mr._attach_mlb_poly_odds(fixtures)
    assert fixtures[0]["odds"] == "賭盤:道奇 52%・洋基 48%(Polymarket)"   # 跳過 O/U 取勝負盤
    assert fixtures[1]["odds"] == "賭盤:DK(DraftKings 運彩)"              # 無勝負盤 → 保留


def test_mlb_series_merge_keeps_per_game_odds():
    """回歸(Codex review 批#10):同對戰連戰合併後,每場各自的賭盤都要渲染(帶日期),
    不得只剩首戰。"""
    sports = {"mlb_fixtures": [
        {"text": "TB @ BOS", "when": "07/18 01:35",
         "odds": "賭盤:光芒 55%・紅襪 45%(Polymarket)"},
        {"text": "TB @ BOS", "when": "07/19 01:35",
         "odds": "賭盤:光芒 48%・紅襪 52%(Polymarket)"},
    ]}
    h = mr._render_sports_html(sports, htmllib)
    assert "2 連戰" in h
    assert "光芒 55% ・ 紅襪 45%" in h and "光芒 48% ・ 紅襪 52%" in h   # 兩場賭盤都在
    # 批#14:連戰賭盤合併為單一「賭盤(Polymarket):07/18 …;07/19 …」行
    assert "07/18:" in h and "07/19:" in h   # 批#15:各比賽日獨立一行
    assert h.count("(Polymarket)") == 1   # 批#15:標籤行只出現一次


def test_poly_event_is_future_uses_instant_not_date(monkeypatch):
    """回歸(Codex review 批#10):同一 UTC 日但已結束(closed 旗標未翻)的事件
    要被擋掉——比「時刻」而非只比日期;純日期字串視為當日末。"""
    import datetime as dt
    now = dt.datetime(2026, 7, 16, 12, 0, tzinfo=dt.timezone.utc)
    assert mr._poly_event_is_future({"endDate": "2026-07-16T08:00:00Z"}, now) is False
    assert mr._poly_event_is_future({"endDate": "2026-07-16T23:00:00Z"}, now) is True
    assert mr._poly_event_is_future({"endDate": "2026-07-16"}, now) is True   # 當日末有效
    assert mr._poly_event_is_future({"endDate": "2026-07-15"}, now) is False
    assert mr._poly_event_is_future({"endDate": ""}, now) is False
    assert mr._poly_event_is_future({"endDate": "garbage"}, now) is False

    # 端到端:同日已結束的 TSMC 盤被跳過 → 取下一場未來盤
    def fake_search(query, limit=8):
        if "TSMC" in query:
            return [
                {"title": "Will TSMC (TSM) beat quarterly earnings?", "slug": "tsm-old",
                 "endDate": "2026-07-16T08:00:00Z"},   # 今晨已結束(closed 未翻)
                {"title": "Will TSMC (TSM) beat quarterly earnings?", "slug": "tsm-next",
                 "endDate": "2026-10-16T23:00:00Z"},
            ]
        return []

    def fake_events(params):
        if params.get("slug") == "tsm-next":
            return [{"markets": [{
                "question": "Will TSMC (TSM) beat quarterly earnings?",
                "outcomePrices": '["0.7", "0.3"]'}]}]
        if params.get("slug") == "tsm-old":
            return [{"markets": [{
                "question": "Will TSMC (TSM) beat quarterly earnings?",
                "outcomePrices": '["0.9995", "0.0005"]'}]}]
        return []
    monkeypatch.setattr(mr, "_poly_search_events", fake_search)
    monkeypatch.setattr(mr, "_poly_events", fake_events)
    rows = mr.fetch_polymarket_pulse(dt.datetime(2026, 7, 16, 20, 0, tzinfo=mr.TPE))
    tsm = [r for r in rows if "台積電" in r["label"]]
    assert tsm and tsm[0]["detail"] == "機率 70%"   # 取 tsm-next 而非已結束的 99.95%


# ===== 批#11(2026-07-16):NBA 單場預接 / MVP・賽揚 / 東西區冠軍 / 政治盤 =====

def test_attach_nba_poly_odds_abbrev_fix_and_membership(monkeypatch):
    """NBA 單場:ESPN 短碼(GS/NY/SA/UTAH/NO/WSH)→ Polymarket 三碼;
    兩結果都須為已知 NBA 隊名;休賽季全 MISS 不掛(開季自動生效)。"""
    captured = []

    def fake_events(params):
        captured.append(params.get("slug"))
        if params.get("slug") == "nba-gsw-lal-2026-10-22":
            return [{"markets": [
                {"outcomes": '["Over", "Under"]', "outcomePrices": '["0.5", "0.5"]'},
                {"outcomes": '["Golden State Warriors", "Los Angeles Lakers"]',
                 "outcomePrices": '["0.44", "0.56"]'},
            ]}]
        return []
    monkeypatch.setattr(mr, "_poly_events", fake_events)
    fixtures = [
        {"text": "GS @ LAL", "when": "10/23 10:30",
         "away_abbr": "GS", "home_abbr": "LAL", "date_us": "2026-10-22"},
        {"text": "NY @ UTAH", "when": "10/23 09:00", "odds": "賭盤:DK(DraftKings 運彩)",
         "away_abbr": "NY", "home_abbr": "UTAH", "date_us": "2026-10-22"},
    ]
    mr._attach_nba_poly_odds(fixtures)
    assert fixtures[0]["odds"] == "賭盤:勇士 44%・湖人 56%(Polymarket)"
    assert "nba-nyk-uta-2026-10-22" in captured            # NY→nyk、UTAH→uta
    assert fixtures[1]["odds"] == "賭盤:DK(DraftKings 運彩)"  # MISS → 保留原行


def test_poly_outright_excludes_party_placeholder(monkeypatch):
    """政治盤佔位項 Party A 要剔除(與 Team A/Player A 同規則)。"""
    fake_event = {"markets": [
        {"groupItemTitle": "Democratic Party", "outcomePrices": '["0.845", "0.155"]'},
        {"groupItemTitle": "Republican Party", "outcomePrices": '["0.165", "0.835"]'},
        {"groupItemTitle": "Party A", "outcomePrices": '["0.5", "0.5"]'},
    ]}
    monkeypatch.setattr(mr, "_poly_events", lambda params: [fake_event])
    rows = mr._poly_outright("which-party-will-win-the-house-in-2026",
                             mr._POLY_PARTY_ZH, top=2)
    assert [(r["name"], r["prob"]) for r in rows] == [("民主黨", 84), ("共和黨", 16)]


def test_render_mlb_awards_and_nba_conference_lines():
    """MVP/賽揚合一行(AL;NL)、NBA 東西區冠軍合一行(東;西);
    傳統源缺席時各自獨立渲染、不重複。"""
    poly = {
        "mlb_al_mvp": [{"name": "Yordan Alvarez", "prob": 61}],
        "mlb_nl_mvp": [{"name": "大谷翔平", "prob": 85}],
        "mlb_al_cy": [{"name": "Cam Schlittler", "prob": 46}],
        "mlb_nl_cy": [{"name": "Jacob Misiorowski", "prob": 63}],
        "nba_east": [{"name": "尼克", "prob": 22}],
        "nba_west": [{"name": "雷霆", "prob": 34}],
        "nba_champ": [{"name": "雷霆", "prob": 27}],
    }
    # 有戰績/休賽季說明 → 嵌入各自區塊
    h = mr._render_sports_html({
        "news": {}, "poly": poly,
        "standings": {"美聯": [{"team": "TB", "record": "56-38", "pct": 0.596}]},
        "nba_offseason": "NBA 休賽季:自由市場進行中。"}, htmllib)
    assert "年度 MVP 盤" in h and "AL:Yordan Alvarez 61%" in h and "NL:大谷翔平 85%" in h
    assert "賽揚獎盤" in h and "AL:Cam Schlittler 46%" in h and "NL:Jacob Misiorowski 63%" in h
    assert "東西區冠軍盤" in h and "東:尼克 22%" in h and "西:雷霆 34%" in h
    assert h.count("東西區冠軍盤") == 1                    # 不重複渲染
    # 傳統源全掛 → MLB/NBA 各自獨立 fallback 區塊,獎項盤仍在
    h2 = mr._render_sports_html({"news": {}, "poly": poly}, htmllib)
    assert "年度 MVP 盤" in h2 and "東西區冠軍盤" in h2
    assert h2.count("東西區冠軍盤") == 1


def test_pulse_includes_politics_rows(monkeypatch):
    """政治盤入預測市場快照(眾院/參院/2028 執政黨,政黨名繁中)。"""
    def fake_events(params):
        slug = str(params.get("slug") or "")
        if "house" in slug or "senate" in slug or "presidential" in slug:
            return [{"markets": [
                {"groupItemTitle": "Democratic Party", "outcomePrices": '["0.84", "0.16"]'},
                {"groupItemTitle": "Republican Party", "outcomePrices": '["0.17", "0.83"]'},
            ]}]
        return []
    monkeypatch.setattr(mr, "_poly_events", fake_events)
    monkeypatch.setattr(mr, "_poly_search_events", lambda q, limit=8: [])
    import datetime as dt
    rows = mr.fetch_polymarket_pulse(dt.datetime(2026, 7, 16, 6, 0, tzinfo=mr.TPE))
    by_label = {r["label"]: r["detail"] for r in rows}
    assert "2028 美國總統大選執政黨" in by_label            # 保留
    for gone in ("美國期中選舉眾院多數黨", "美國期中選舉參院多數黨"):
        assert gone not in by_label                        # 批#14 使用者刪減


def test_poly_guard_trips_after_consecutive_failures(monkeypatch):
    """回歸(Codex review 批#11 P1):Polymarket 逾時級聯不可拖垮晨報——
    連續 2 次失敗即斷路,其後呼叫瞬時拋錯不再打 HTTP;成功會歸零連敗計數。"""
    import pytest
    calls = []

    def boom(url, **kwargs):
        calls.append(url)
        raise mr.requests.ConnectionError("timeout")
    monkeypatch.setattr(mr, "_http_get", boom)
    for _ in range(2):
        with pytest.raises(Exception):
            mr._poly_events({"slug": "x"})
    assert mr._POLY_GUARD["tripped"] is True
    with pytest.raises(RuntimeError):
        mr._poly_events({"slug": "y"})       # 斷路後不再打 HTTP
    assert len(calls) == 2
    # 呼叫端(fetch_polymarket_sports/pulse)靠既有 try 全面降級,不炸
    assert mr.fetch_polymarket_sports() == {}
    assert mr.fetch_polymarket_pulse() == []


def test_poly_guard_budget_and_reset(monkeypatch):
    """總時間預算用罄 → 斷路;單次成功會歸零連敗(1 敗 1 成不斷路)。"""
    import pytest

    class R:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return []
    seq = [mr.requests.ConnectionError("t"), R()]

    def flaky(url, **kwargs):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(mr, "_http_get", flaky)
    with pytest.raises(Exception):
        mr._poly_events({"slug": "a"})       # 第 1 敗
    assert mr._poly_events({"slug": "b"}) == []   # 成功 → 連敗歸零
    assert mr._POLY_GUARD["tripped"] is False
    # 預算用罄 → 立即斷路
    mr._POLY_GUARD["spent"] = 999.0
    with pytest.raises(RuntimeError):
        mr._poly_events({"slug": "c"})
    assert mr._POLY_GUARD["tripped"] is True


def test_pulse_taiwan_markets(monkeypatch):
    """台灣政治盤(批#12,2026-07-16):九合一政黨盤(繁中黨名)+賴清德任期盤;
    2028 總統大選盤未開時動態搜尋不出行、市場一開自動出現。"""
    import datetime as dt

    def fake_events(params):
        slug = str(params.get("slug") or "")
        if slug == "2026-taiwanese-local-elections-party-winner":
            return [{"markets": [
                {"groupItemTitle": "Kuomintang (KMT)", "outcomePrices": '["0.855", "0.145"]'},
                {"groupItemTitle": "Democratic Progressive Party (DPP)",
                 "outcomePrices": '["0.1425", "0.8575"]'},
                {"groupItemTitle": "Taiwan People’s Party (TPP)",
                 "outcomePrices": '["0.055", "0.945"]'},
                {"groupItemTitle": "Party A", "outcomePrices": '["0.5", "0.5"]'},
            ]}]
        if slug == "lai-ching-te-out-as-president-of-taiwan-in-2026":
            return [{"markets": [{"outcomePrices": '["0.0485", "0.9515"]'}]}]
        if slug == "tw-2028":
            return [{"markets": [
                {"groupItemTitle": "Democratic Progressive Party (DPP)",
                 "outcomePrices": '["0.5", "0.5"]'}]}]
        return []

    search_results = {"n": 0}

    def fake_search(query, limit=8):
        if "Taiwan presidential" in query and search_results["n"]:
            return [{"title": "Taiwan Presidential Election 2028: Who will win?",
                     "slug": "tw-2028", "endDate": "2028-01-13T00:00:00Z"}]
        return []
    monkeypatch.setattr(mr, "_poly_events", fake_events)
    monkeypatch.setattr(mr, "_poly_search_events", fake_search)
    now = dt.datetime(2026, 7, 16, 6, 0, tzinfo=mr.TPE)
    rows = {r["label"]: r["detail"] for r in mr.fetch_polymarket_pulse(now)}
    assert rows["2026 台灣九合一選舉最大贏家"] == "國民黨 86%・民進黨 14%・民眾黨 6%"
    assert "賴清德總統 2026 年底前去職" not in rows        # 批#14 使用者刪減
    assert "台灣總統大選" not in rows                      # 2028 盤未開 → 不出行
    search_results["n"] = 1                               # 模擬市場開盤
    rows2 = {r["label"]: r["detail"] for r in mr.fetch_polymarket_pulse(now)}
    assert rows2["台灣總統大選"] == "民進黨 50%"           # 一開盤自動出現


# ===== 地基批#4(2026-07-16):Polymarket delta + 量低標記 =====

def test_poly_track_deltas_day_over_day(monkeypatch, tmp_path):
    """首日無 delta;次日回傳 pp 差;同日重跑 prev 不動(delta 穩定);舊盤修剪。"""
    import datetime as dt
    monkeypatch.setattr(mr, "POLY_HISTORY_FILE", tmp_path / "poly_history.json")
    d1 = dt.datetime(2026, 7, 16, 6, 0, tzinfo=mr.TPE)
    d2 = dt.datetime(2026, 7, 17, 6, 0, tzinfo=mr.TPE)
    assert mr._poly_track_deltas("wc", {"西班牙": 58, "阿根廷": 42}, d1) == {}
    # 同日重跑:仍無 delta(prev 空)
    assert mr._poly_track_deltas("wc", {"西班牙": 60, "阿根廷": 40}, d1) == {}
    # 次日:vs 昨日 curr(60/40)
    deltas = mr._poly_track_deltas("wc", {"西班牙": 65, "阿根廷": 35}, d2)
    assert deltas == {"西班牙": {"pp": 5, "days": 1}, "阿根廷": {"pp": -5, "days": 1}}
    # 次日同日重跑:prev 仍是昨日 → delta 以昨日為基準
    deltas2 = mr._poly_track_deltas("wc", {"西班牙": 66, "阿根廷": 34}, d2)
    assert deltas2 == {"西班牙": {"pp": 6, "days": 1}, "阿根廷": {"pp": -6, "days": 1}}
    # 跨多日(來源失敗/寄信失敗日未輪替)→ 揭露實際間隔,不偽裝成前一日
    d5 = dt.datetime(2026, 7, 20, 6, 0, tzinfo=mr.TPE)
    deltas3 = mr._poly_track_deltas("wc", {"西班牙": 70}, d5)
    assert deltas3 == {"西班牙": {"pp": 4, "days": 3}}   # 基準=07/17 的 66
    assert mr._poly_delta_suffix(4, 3) == "(↑4pp/3日)"
    # 新名字(昨日沒有)不回 delta
    assert "英格蘭" not in mr._poly_track_deltas("wc", {"英格蘭": 10}, d2)
    # 14 天沒更新的死盤被修剪
    import json as _json
    store = _json.loads((tmp_path / "poly_history.json").read_text(encoding="utf-8"))
    store["dead"] = {"curr": {"date": "2026-06-01", "probs": {"x": 1}}}
    (tmp_path / "poly_history.json").write_text(_json.dumps(store), encoding="utf-8")
    mr._poly_track_deltas("wc", {"西班牙": 66}, d2)
    store2 = _json.loads((tmp_path / "poly_history.json").read_text(encoding="utf-8"))
    assert "dead" not in store2 and "wc" in store2


def test_poly_prob_line_renders_delta_and_low_volume():
    line = mr._poly_prob_line([
        {"name": "西班牙", "prob": 58, "delta": 16},
        {"name": "阿根廷", "prob": 42, "delta": -16},
        {"name": "英格蘭", "prob": 3, "low_vol": True},
        {"name": "法國", "prob": 2, "delta": 0.4},   # |d|<1 不顯示
    ])
    # 批#14:量低改行級聚合,不再逐名標
    assert line == "西班牙 58%(↑16pp)・阿根廷 42%(↓16pp)・英格蘭 3%・法國 2%(部分量低⚠)"
    assert mr._poly_prob_line([{"name": "甲", "prob": 60}]) == "甲 60%"


def test_poly_outright_marks_low_volume(monkeypatch):
    fake_event = {"markets": [
        {"groupItemTitle": "Spain", "outcomePrices": '["0.58", "0.42"]',
         "volume24hr": 4729750.1},
        {"groupItemTitle": "Argentina", "outcomePrices": '["0.42", "0.58"]',
         "volume24hr": 120.0},
    ]}
    monkeypatch.setattr(mr, "_poly_events", lambda params: [fake_event])
    rows = mr._poly_outright("world-cup-winner", mr._WC_TEAM_ZH, top=4)
    by = {r["name"]: r for r in rows}
    assert by["西班牙"]["low_vol"] is False
    assert by["阿根廷"]["low_vol"] is True


def test_pulse_binary_detail_with_delta(monkeypatch, tmp_path):
    import datetime as dt
    monkeypatch.setattr(mr, "POLY_HISTORY_FILE", tmp_path / "poly.json")
    markets = [{"outcomePrices": '["0.52", "0.48"]', "volume24hr": 50000}]
    d1 = dt.datetime(2026, 7, 16, 6, 0, tzinfo=mr.TPE)
    d2 = dt.datetime(2026, 7, 17, 6, 0, tzinfo=mr.TPE)
    assert mr._poly_binary_detail("pulse|升息", markets, d1) == "機率 52%"
    markets2 = [{"outcomePrices": '["0.59", "0.41"]', "volume24hr": 900}]
    assert mr._poly_binary_detail("pulse|升息", markets2, d2) == "機率 59%(↑7pp)(量低⚠)"
    assert mr._poly_binary_detail("pulse|升息", [], d2) is None


def test_mlb_doubleheader_odds_not_duplicated():
    """信件修正(2026-07-17):同日雙重賽兩場賭盤相同 → 合併列只印一次;
    不同日/不同賠率仍各自保留。"""
    line = "賭盤:光芒 46%・紅襪 54%(Polymarket)"
    sports = {"mlb_fixtures": [
        {"text": "TB @ BOS", "when": "07/18 01:35", "odds": line},
        {"text": "TB @ BOS", "when": "07/18 07:05", "odds": line},          # 雙重賽同賠率
        {"text": "TB @ BOS", "when": "07/19 01:35",
         "odds": "賭盤:光芒 48%・紅襪 52%(Polymarket)"},
    ]}
    h = mr._render_sports_html(sports, htmllib)
    assert h.count("光芒 46% ・ 紅襪 54%") == 1
    assert "光芒 48% ・ 紅襪 52%" in h


def test_poly_binary_detail_deterministic_market_selection():
    """三審 P1-7:不得盲取 markets[0]——question_re 過濾;多個可取價子盤無從
    辨識時寧缺勿錯回 None;單一候選正常取價。"""
    import datetime as dt
    now = dt.datetime(2026, 7, 17, 6, 0, tzinfo=mr.TPE)
    eps = {"question": "Will TSMC beat EPS estimates?",
           "outcomes": '["Yes", "No"]', "outcomePrices": '["0.7", "0.3"]'}
    rev = {"question": "Will TSMC beat revenue estimates?",
           "outcomes": '["Yes", "No"]', "outcomePrices": '["0.4", "0.6"]'}
    # 多子盤 + question_re 命中唯一 → 取對的那個(不是 markets[0])
    d = mr._poly_binary_detail("t|eps", [rev, eps], now,
                               question_re=r"beat eps")
    assert d is not None and "70%" in d
    # 多子盤且無 question_re → 無從辨識,寧缺勿錯
    assert mr._poly_binary_detail("t|multi", [rev, eps], now) is None
    # 單一子盤 → 正常
    assert "40%" in mr._poly_binary_detail("t|single", [rev], now)
    # 全部無法取價 → None
    assert mr._poly_binary_detail("t|none", [{"question": "x"}], now) is None


def test_local_dup_landmark_prefix_not_killed_but_rewrites_still_are():
    """Codex 批#15 r3:0.35-0.50 弱重疊帶要求共享內容 ≥2 區段——
    同地標不同事件(單一「台中捷運藍線」前綴段)不得誤殺;
    真正同事件的多段式改寫(二林運動館)仍須判重複。"""
    a = "台中捷運藍線工程進度最新曝光"
    b = "台中捷運藍線徵才簡章正式公布"
    seen = [mr._local_seen_entry(a)]
    assert mr._local_title_is_dup(b, seen) is False        # 同地標不同事件 → 保留
    # 同事件多段改寫(實際案例,overlap 0.391/0.435)仍判重複
    c = "活化西南角閒置土地 彰化「二林樂活運動館」斥資3.8億動土 - 全國廣播"
    d = "（有影片）／二林樂活運動館動土 打造西南角首座大型運動場館 - 觀傳媒"
    e = "彰化縣西南角首座大型運動場館 二林樂活運動館工程動土 - 警政時報"
    seen_c = [mr._local_seen_entry(c)]
    assert mr._local_title_is_dup(d, seen_c) is True
    assert mr._local_title_is_dup(e, seen_c) is True
    # 71歲直腸癌兩改寫(共享數字+多區段)仍判重複
    f = "71歲劉姓硬漢，二度檢查終於由彰基找出直腸癌位置。（照片彰基提供）"
    g = "癌藏體內沒感覺 71歲男靠一次檢查揪直腸癌"
    seen_f = [mr._local_seen_entry(f)]
    assert mr._local_title_is_dup(g, seen_f) is True
    # 長標題共享路線號但事件不同(單段+數字)不得誤殺
    h1 = "台74線崇德匝道拓寬工程週五動工改道"
    h2 = "台74線大里段深夜連環車禍釀三傷"
    seen_h = [mr._local_seen_entry(h1)]
    assert mr._local_title_is_dup(h2, seen_h) is False
    # r6:長專案名前綴可衝破 0.50(「台中捷運藍線工程」進度 vs 經費 ≈0.54)——
    # 無條件線提高到 0.70,單段前綴仍不得誤殺
    j1 = "台中捷運藍線工程進度最新曝光"
    j2 = "台中捷運藍線工程經費追加通過"
    seen_j = [mr._local_seen_entry(j1)]
    assert mr._local_title_is_dup(j2, seen_j) is False
    # 「討論牆 |」式整段含入(正規化 containment)仍判重複
    k = "討論牆 | 台中捷運藍線工程進度最新曝光 - LINE TODAY"
    assert mr._local_title_is_dup(k, seen_j) is True
    # r7:超長專案名前綴可衝破任何純門檻(「大埔截水溝堤岸道路拓寬工程」
    # 第一期完工 vs 第二期動工)——非含入且單一區段,不得誤殺
    p1 = "彰化市大埔截水溝堤岸道路拓寬工程第一期完工"
    p2 = "彰化市大埔截水溝堤岸道路拓寬工程第二期動工"
    seen_p = [mr._local_seen_entry(p1)]
    assert mr._local_title_is_dup(p2, seen_p) is False
    # r9:語意後綴(延期/取消)是事件更新,含入不得誤殺;樣板前綴(快訊)才算同一則
    q1 = "彰化百貨開幕"
    seen_q = [mr._local_seen_entry(q1)]
    assert mr._local_title_is_dup("彰化百貨開幕延期", seen_q) is False
    assert mr._local_title_is_dup("快訊/彰化百貨開幕", seen_q) is True
    assert mr._local_title_is_dup("最新快訊｜彰化百貨開幕", seen_q) is True   # 長樣板詞優先
    # r10:樣板詞只剝前綴——標題中段的「更新」是語意內容,不得剝除;
    # 前綴位置的「更新/最新快訊」才是樣板(可連續剝多層)
    assert mr._strip_title_boilerplate("彰化百貨更新營業時間") == "彰化百貨更新營業時間"
    assert mr._strip_title_boilerplate("更新彰化百貨開幕") == "彰化百貨開幕"
    assert mr._strip_title_boilerplate("最新快訊討論牆彰化百貨開幕") == "彰化百貨開幕"


def test_local_region_tokens_cover_township_only_titles():
    """Codex 批#15 r3:只寫鄉鎮名的合法中彰投雲標題不得被地區過濾漏收。"""
    for title in ("和美新建案公開 每坪站上3字頭",
                  "埔里外環道拓寬工程動工",
                  "虎尾產業園區今動土 引進智慧農業",
                  "溪湖果菜市場改建案通過",
                  # r4:歧義地名的無歧義複合形式必須收
                  "田中鎮火車站周邊開發案動工",
                  "東勢林場聯外道路改善工程啟動",
                  "大城鄉海堤補強計畫核定",
                  "斗南車站周邊開發案動工"):   # r12:斗南曾漏列
        assert any(tok in title for tok in mr._LOCAL_REGION_TOKENS), title
    # r9:歧義台中區名複合形式收、裸詞不收
    for title in ("太平區公所遷建案通過", "清水區海線綠廊啟用", "新社花海11月登場"):
        assert any(tok in title for tok in mr._LOCAL_REGION_TOKENS), title
    for title in ("板橋打造新社區公園完工啟用",):   # r10:「新社區」=泛用語,不得誤收
        assert not any(tok in title for tok in mr._LOCAL_REGION_TOKENS), title
    # 跨區誤收樣本仍被擋(含歧義裸詞:台北信義區/日本姓氏田中/「大城市」/
    # 宜蘭太平山/建築工法清水模)
    for title in ("板橋租屋要住哪？車站旁套房溢價17%仍搶手",
                  "信義區豪宅成交創高", "高雄輕軌新進度",
                  "日本首相田中發表談話", "全球大城市房價比較",
                  "宜蘭太平山道路施工封閉", "清水模建築美學特展"):
        assert not any(tok in title for tok in mr._LOCAL_REGION_TOKENS), title


def test_local_region_filter_rejects_ncsist_collision(monkeypatch):
    """Codex 批#15 r6:「中科院」(國防)撞裸「中科」token——剝除後再比對;
    真正的中科園區新聞(中科擴線)不受影響。"""
    import datetime as dt
    now_gmt = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    class Feed:
        def __init__(self, url):
            if "%E5%BD%B0%E6%BF%B1%E5%B7%A5%E6%A5%AD%E5%8D%80" in url:  # 產業/科技 query
                self.entries = [
                    {"title": "中科院無人機飛彈試射成功", "link": "https://x/1",
                     "published": now_gmt},                       # 國防新聞 → 擋
                    {"title": "李長榮先進材料中科擴線動土", "link": "https://x/2",
                     "published": now_gmt},                       # 真中科 → 收
                ]
            else:
                self.entries = []

    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url, *a, **k: Feed(url))
    out = mr.fetch_local_news()
    titles = [t["title"] for v in out.values() for t in v]
    assert "中科院無人機飛彈試射成功" not in titles
    assert "李長榮先進材料中科擴線動土" in titles


def test_poly_outright_stable_ids_and_wide_spread(monkeypatch):
    """批#17:rows 帶 hist_key(market id)與 wide(spread>=5pp);
    delta 以 id 為 key,舊快照譯名 key 走 alias 回退不斷線。"""
    import datetime as dt
    monkeypatch.setattr(mr, "_poly_events", lambda p: [{"markets": [
        {"id": "111", "groupItemTitle": "Anthropic", "closed": False,
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.655","0.345"]',
         "spread": 0.01, "volume24hr": 50000},
        {"id": "222", "groupItemTitle": "Google", "closed": False,
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.125","0.875"]',
         "spread": 0.08, "volume24hr": 50000},
    ]}])
    rows = mr._poly_outright("x", top=5)
    by = {r["name"]: r for r in rows}
    assert by["Anthropic"]["hist_key"] == "111" and by["Anthropic"]["wide"] is False
    assert by["Google"]["hist_key"] == "222" and by["Google"]["wide"] is True
    assert "(部分價差寬⚠)" in mr._poly_prob_line(rows)
    # alias 回退:昨日快照以「譯名」為 key → 今日改 id 仍算得出 delta
    now = dt.datetime(2026, 7, 18, 6, tzinfo=mr.TPE)
    hist = {"pulse|x": {"curr": {"date": "2026-07-17",
                                 "probs": {"Anthropic": 60.0, "Google": 14.0}}}}
    mr.POLY_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    mr.POLY_HISTORY_FILE.write_text(__import__("json").dumps(hist), encoding="utf-8")
    out = mr._poly_annotate_deltas("pulse|x", rows, now)
    assert {r["name"]: r.get("delta") for r in out} == {
        "Anthropic": 5.5, "Google": -1.5}


def test_poly_binary_detail_spread_note(monkeypatch):
    """批#17:價差 ≥5pp 時二元盤附可成交價(買=ask/賣=bid);窄價差不附。"""
    import datetime as dt
    now = dt.datetime(2026, 7, 18, 6, tzinfo=mr.TPE)
    wide = {"question": "q", "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.58","0.42"]',
            "spread": 0.06, "bestBid": 0.56, "bestAsk": 0.62}
    d = mr._poly_binary_detail("t|w", [wide], now)
    assert "機率 58%" in d and "(買62/賣56)" in d
    narrow = dict(wide, spread=0.02)
    assert "買" not in mr._poly_binary_detail("t|n", [narrow], now)


def test_poly_divergence_note_rules():
    """批#17:Fed 再升息定價與本報立場分歧才提示;一致/非方向立場不提示。"""
    rows = [{"label": "2026 年內 Fed 再升息", "detail": "機率 62%(↑3pp)"}]
    assert "分歧提示" in mr._poly_divergence_note(rows, {"label": "偏多"})
    assert mr._poly_divergence_note(rows, {"label": "偏空"}) == ""
    assert mr._poly_divergence_note(rows, {"label": "中性"}) == ""
    low = [{"label": "2026 年內 Fed 再升息", "detail": "機率 8%"}]
    assert "分歧提示" in mr._poly_divergence_note(low, {"label": "偏空"})
    assert mr._poly_divergence_note(low, {"label": "偏多"}) == ""
    assert mr._poly_divergence_note([], {"label": "偏多"}) == ""


def test_monthly_ai_market_removed_annual_kept(monkeypatch):
    """批#26:當月最佳 AI 模型盤已移除(月底盤常一家獨大、資訊量低);
    年底盤保留。"""
    import datetime as dt
    def fake_outright(slug, zh_map=None, top=5, min_prob=0.02):
        if "2026" in slug:                      # 年度盤
            return [{"name": "Anthropic", "prob": 66, "prob_raw": 66.0,
                     "hist_key": "1", "low_vol": False, "wide": False}]
        return []   # 若當月盤仍被查(不該),回空
    monkeypatch.setattr(mr, "_poly_outright", fake_outright)
    monkeypatch.setattr(mr, "_poly_search_events", lambda q, limit=8: [])
    monkeypatch.setattr(mr, "_poly_events", lambda p: [])
    rows = mr.fetch_polymarket_pulse(dt.datetime(2026, 7, 18, 6, tzinfo=mr.TPE))
    labels = [r["label"] for r in rows]
    assert "年底最佳 AI 模型" in labels
    assert not any("月底最佳 AI 模型" in x for x in labels)


def test_batch26_display_removals():
    """批#26 顯示刪除彙總:銅期貨/預測記分卡/立場歸因卡不進信;世足賽期窗
    延到決賽後。"""
    import datetime as dt
    # 世足賽期窗上界=決賽(07/20)後,07/20 當天仍在窗內
    assert mr._WC_WINDOW[1] >= dt.date(2026, 7, 20)
    assert dt.date(2026, 7, 20) <= mr._WC_WINDOW[1]
    # 立場理由散文層過濾:計分內部被移除、傳導鏈保留
    from llm_postprocess import _strip_stance_internals
    txt = "理由：11 維中 7 項偏空、淨分 -6 距門檻尚有空間。核心傳導鏈：SOX 壓制 2330。"
    out = _strip_stance_internals(txt)
    assert "11 維" not in out and "淨分" not in out and "門檻" not in out
    assert "核心傳導鏈：SOX 壓制 2330" in out
    # Codex r2:ASCII 逗號也是子句分隔——傳導鏈保留、計分內部丟、千分位不受害
    a = _strip_stance_internals("理由：SOX 承壓, 11 維中 7 項偏空, 淨分 -6")
    assert a == "理由：SOX 承壓"
    assert _strip_stance_internals("理由：成交 1,234 億, 淨分 -6") == "理由：成交 1,234 億"



def test_two_way_odds_normalize_to_100():
    """批#47:Polymarket 兩邊的最佳報價各自含買賣價差,直接並列會出現
    「台鋼 55%・味全 46%」=101%(2026-07-26 實信)。NBA 單場那條路徑本來就有
    正規化,中職這條沒有——同一個 repo 裡兩種處理。"""
    import morning_report as mr
    a, b = mr._normalized_two_way([55, 46])
    assert a + b == 100, f"未正規化:{a}+{b}"
    a, b = mr._normalized_two_way([49, 51])
    assert a + b == 100


def test_two_way_odds_degrade_without_inventing_probabilities():
    """資料異常時原樣回傳——寧可顯示原始報價,也不要造出假的機率。"""
    import morning_report as mr
    assert mr._normalized_two_way([0, 0]) == (0, 0)
    assert mr._normalized_two_way(["x", "y"]) == ("x", "y")
    assert mr._normalized_two_way([]) == ("—", "—")


def _sports_news_labels_queried(monkeypatch, when):
    """跑 fetch_sports_digest,回傳實際發出的體育新聞查詢。

    r2(Codex):先前用 hasattr 守衛去 patch 一串**不存在**的函式名,結果全被靜默
    跳過,測試實際在打真網路(套件時間從 23s 變 53s)。改為從底層切斷:
    _http_get / requests.get 一律拋連線錯誤(各 fetcher 本來就會降級成空),
    feedparser 一律回空 feed。這樣不必追每個 fetcher 的名字,也不會因日後改名而失效。
    """
    import morning_report as mr
    asked = []

    def _fake_gnews(query, when="2d"):
        asked.append(query)
        return "https://news.google.com/rss/fake"

    class _EmptyFeed:
        entries = []
        bozo = False

    def _no_network(*a, **k):
        raise mr.requests.exceptions.ConnectionError("blocked in test")

    monkeypatch.setattr(mr, "_gnews_rss", _fake_gnews)
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda *a, **k: _EmptyFeed())
    monkeypatch.setattr(mr, "_http_get", _no_network)
    monkeypatch.setattr(mr.requests, "get", _no_network)
    monkeypatch.setattr(mr.requests, "post", _no_network)
    mr.fetch_sports_digest(now_tpe=when)
    return asked


def test_out_of_season_sport_skips_news_query(monkeypatch):
    """批#47:賽期外的賽事不再抓新聞。實信 2026-07-26(決賽後六天)仍有世足專區,
    且混進宗教評論——賽果/賭盤區早就受 _WC_WINDOW 管,新聞查詢卻沒有。

    r1(Codex):閘必須用**本次已解析的 now_tpe**,不可另讀牆上時鐘——
    fetch_worldcup 的賽果閘走 now_tpe.date(),兩處讀不同來源時,重放舊日期會出現
    「賽果照出但新聞被跳過」的錯位。故本測試直接跑 fetch_sports_digest 並傳入
    明確日期,而非只驗日期述詞。"""
    import datetime as _dt
    import morning_report as mr
    lo, hi = mr._SEASONAL_SPORT_WINDOWS["世足"]

    out_of_season = _dt.datetime.combine(hi + _dt.timedelta(days=3),
                                         _dt.time(8, 0), tzinfo=mr.TPE)
    asked = _sports_news_labels_queried(monkeypatch, out_of_season)
    assert not any("World Cup" in q or "世界盃" in q for q in asked),         f"賽期外仍查了世足新聞:{asked}"
    assert any("MLB" in q for q in asked), "其他賽事不該被連坐"

    in_season = _dt.datetime.combine(lo + _dt.timedelta(days=1),
                                     _dt.time(8, 0), tzinfo=mr.TPE)
    asked = _sports_news_labels_queried(monkeypatch, in_season)
    assert any("World Cup" in q or "世界盃" in q for q in asked),         f"賽期內卻沒查世足新聞:{asked}"


def test_sports_header_lists_only_present_sections():
    """賽季性項目本來就會輪流缺席(NBA 休賽季、世足四年一次),標題寫死必然
    對不上。2026-07-27 實信即為此:世足賽期已過、區塊不存在,標題仍列著它。"""
    import html as _h
    import render_utils as ru

    # 自測踩到:cpbl 列需要 rank/gb 等欄位,少給會 KeyError
    # ——用完整結構,免得對照組其實是「渲染失敗」而非「沒有該項目」。
    row = {"rank": 1, "team": "味全龍", "wdl": "46-0-28",
           "pct": "0.622", "gb": "-"}
    only_cpbl = ru._render_sports_html({"cpbl": [row]}, _h)
    head = only_cpbl.split("</h2>")[0]
    assert "中職" in head
    for absent in ("世足", "MLB", "NBA", "網球"):
        assert absent not in head, f"沒有{absent}資料卻列進標題"

    # r1(Codex,P2):**我原本用 {"tennis": {"atp": [...]}}——那個形狀不會產生
    # 任何網球區塊**,等於把「標題列了但區塊沒出」這個缺陷釘成規格。
    # 標題現在由已渲染的區塊推出,所以測試也必須用會真正產生區塊的資料。
    both = ru._render_sports_html(
        {"cpbl": [row],
         "standings": {"美聯": [{"rank": 1, "team": "光芒",
                                 "record": "62-43", "pct": 0.590}]}}, _h)
    head2 = both.split("</h2>")[0]
    assert "中職" in head2 and "MLB" in head2

    # 生產環境的空網球形狀不得被列進標題(這正是實信裡世足那個問題的同型)
    empty_tennis = ru._render_sports_html(
        {"cpbl": [row], "tennis": {"tournaments": [], "results": []}}, _h)
    assert "網球" not in empty_tennis.split("</h2>")[0]

    # 全空時整個區塊不出現(既有行為,不得回歸)
    assert ru._render_sports_html({}, _h) == ""


def test_podcast_card_shows_episode_date_and_flags_stale():
    """2026-07-27 實信:財經M平方 EP.208 講「台股創單日最大漲點」「高檔震盪」,
    而當天實際是普跌(上漲佔比 30.7%)——讀者會以為那是對今天盤勢的判讀。
    這不是 bug(podcast 本來就有時間差),但**沒標日期就看不出它在講哪一天**。"""
    import datetime as _dt
    import render_utils as ru
    TPE = _dt.timezone(_dt.timedelta(hours=8))
    now = _dt.datetime.now(TPE)

    fresh = ru._episode_age_tag(
        {"published": (now - _dt.timedelta(days=2)).isoformat()})
    assert fresh.strip().startswith("・") and "天前" not in fresh, \
        f"近期節目不該掛過舊提示:{fresh}"

    stale = ru._episode_age_tag(
        {"published": (now - _dt.timedelta(days=9)).isoformat()})
    assert "9 天前" in stale and "非當前盤勢" in stale

    # 缺日期或壞日期一律不顯示(不得炸掉整張卡)
    assert ru._episode_age_tag({}) == ""
    assert ru._episode_age_tag({"published": "not-a-date"}) == ""
    assert ru._episode_age_tag({"published": None}) == ""


def test_sports_header_is_not_fooled_by_words_inside_other_blocks():
    """r5(Codex,P2,**同一件事他講了三次**):前兩版我都在掃區塊的 HTML 找關鍵字,
    於是「中職新聞的標題裡剛好提到 NBA」就會讓 NBA 出現在標題,而根本沒有 NBA
    區塊。掃內容永遠會有這種假陽性——正解是**在 append 的當下記下身分**。
    我兩次都選了比較省事的做法(先是寫死字串、再是掃 HTML)。"""
    import html as _h2
    import render_utils as ru
    row = {"rank": 1, "team": "味全龍", "wdl": "46-0-28",
           "pct": "0.622", "gb": "-"}
    out = ru._render_sports_html(
        {"cpbl": [row],
         "news": {"中華職棒": [{"title": "中職球星赴NBA開球 MLB球團也來訪",
                              "link": "https://a"}]}}, _h2)
    head = out.split("</h2>")[0]
    assert "中職" in head
    for ghost in ("NBA", "MLB"):
        assert ghost not in head, f"區塊內文提到 {ghost} 就被列進標題"
    # 內文本身照常保留(只是不影響標題)
    assert "NBA開球" in out
