"""使用者 2026-10-14 的體育區塊需求:季後賽系列戰況與昨日比分、台灣大賽 G 編號、
季前賽縮短、網球新鮮度與進行中排序、獎項盤大熱門、低資訊新聞、今日焦點、依賽季排序。

資料形狀取自 2026-10-14 實測的 ESPN / Yahoo 回應(生產呼叫形狀,不是自編結構)。
"""
import datetime as dt
import html

import morning_report as mr
import sports_quality
import sports_season as ss

TPE = ss.TPE
NOW = dt.datetime(2026, 10, 15, 6, tzinfo=TPE)


def mlb_event(headline, date, state, away=("5", "CLE", "1", False), home=("30", "TB", "3", True),
              wins=(("30", 1), ("5", 1)), season=3, completed_series=False):
    def team(entry, side):
        tid, abbr, score, winner = entry
        return {"id": tid, "homeAway": side, "score": score, "winner": winner,
                "team": {"id": tid, "abbreviation": abbr}}
    return {"date": date, "season": {"type": season},
            "status": {"type": {"state": state, "completed": state == "post"}},
            "competitions": [{"notes": [{"headline": headline}],
                              "competitors": [team(home, "home"), team(away, "away")],
                              "series": {"type": "playoff", "totalCompetitions": 7, "completed": completed_series,
                                         "competitors": [{"id": i, "wins": w} for i, w in wins]}}]}


def test_mlb_postseason_series_results_and_next_game():
    events = [
        mlb_event("ALCS - Game 2", "2026-10-14T00:00Z", "post"),                       # 昨日 08:00
        mlb_event("ALCS - Game 3", "2026-10-15T22:00Z", "pre", away=("30", "TB", "0", False),
                  home=("5", "CLE", "0", False)),                                       # 明日 06:00
        mlb_event("NLCS - Game 2", "2026-10-12T21:00Z", "post", away=("19", "LAD", "4", False),
                  home=("8", "MIL", "7", True), wins=(("8", 2), ("19", 0))),            # 33 小時前
        mlb_event("NLCS - Game 3", "2026-10-15T00:00Z", "pre", away=("8", "MIL", "0", False),
                  home=("19", "LAD", "0", False), wins=(("8", 2), ("19", 0))),
        mlb_event("ALCS - Game 2", "2026-10-14T00:00Z", "post", season=2),             # 非季後賽:忽略
    ]
    post = ss.mlb_postseason(events, NOW)
    assert [s["round"] for s in post["series"]] == ["美聯冠軍戰", "國聯冠軍戰"]
    assert post["series"][1]["teams"] == [("MIL", 2), ("LAD", 0)]
    assert len(post["results"]) == 1, "30 小時外的比分不是「昨日」"
    result = post["results"][0]
    assert (result["game"], result["away_score"], result["home_score"], result["winner"]) == (2, "1", "3", "TB")
    assert [g["game"] for g in post["upcoming"]] == [3, 3]
    assert ss.mlb_postseason([e for e in events if e["season"]["type"] != 3], NOW) == {}


def test_finished_series_only_shown_while_its_last_game_is_fresh():
    done = mlb_event("NLCS - Game 4", "2026-10-11T00:00Z", "post", wins=(("8", 4), ("19", 0)),
                     completed_series=True)
    assert ss.mlb_postseason([done], NOW) == {}
    post = ss.mlb_postseason([done], dt.datetime(2026, 10, 11, 12, tzinfo=TPE))
    assert post["series"][0]["completed"] is True


def yahoo(gid, start, game_type="Taiwan Series", status="status.type.pregame", away_pts=None, home_pts=None,
          away="cpbl.t.1", home="cpbl.t.7"):
    return {"gameid": gid, "start_time": start, "season_phase_id": "season.phase.postseason",
            "game_type": game_type, "status_type": status, "away_team_id": away,
            "home_team_id": home, "total_away_points": away_pts, "total_home_points": home_pts}


TEAMS = {"cpbl.t.1": {"display_name": "中信"}, "cpbl.t.7": {"display_name": "味全"},
         "cpbl.t.2": {"display_name": "統一"}, "cpbl.t.3": {"display_name": "樂天"}}


def playoff(gid, start, **kw):   # 季後挑戰賽:統一 vs 樂天(第一名在台灣大賽等)
    return yahoo(gid, start, game_type="Playoff Series", away="cpbl.t.3", home="cpbl.t.2", **kw)


LEAGUES = {"cpbl": {"league_id": "cpbl", "name": "CPBL"}}   # 2026-10-14 實測:每天都有,休兵日也有


def cpbl_games(*games):
    return ss.yahoo_cpbl_games({"games": {g["gameid"]: g for g in games}, "teams": TEAMS, "leagues": LEAGUES})


def test_cpbl_series_record_game_numbers_and_next_game():
    games = cpbl_games(
        yahoo("g1", "Sat, 17 Oct 2026 09:05:00 +0000", status="status.type.final", away_pts="3", home_pts="5"),
        yahoo("g2", "Sun, 18 Oct 2026 09:05:00 +0000", status="status.type.final", away_pts="", home_pts="2"),
        yahoo("g3", "Tue, 20 Oct 2026 10:35:00 +0000"),
        yahoo("p1", "Mon, 12 Oct 2026 10:35:00 +0000", game_type="Playoff Series",
              status="status.type.final", away_pts="2", home_pts="8"))
    now = dt.datetime(2026, 10, 19, 6, tzinfo=TPE)
    [post] = ss.cpbl_postseason(games, now)["series"]
    assert post["label"] == "台灣大賽" and post["best_of"] == 7
    # gpt-6.1-sol C2 第二輪:少算的戰績仍是錯的戰績 —— 有比分缺漏就不列數字、不判定結束
    assert post["teams"] == [] and "比分缺漏" in post["note"] and not post["decided"]
    assert post["numbers"] == {"g1": 1, "g2": 2, "g3": 3}
    assert post["next"]["game"] == 3 and post["next"]["when"] == "10/20 18:35"
    # 日期已過仍未完賽、系列又還沒分勝負 = 狀態不明 → 整段放棄(xhigh 第九輪:不可刪掉再重編號)
    try:
        ss.cpbl_postseason(games, dt.datetime(2026, 10, 25, 6, tzinfo=TPE))
    except ValueError:
        pass
    else:
        raise AssertionError("過期未完賽的場次讓系列永遠「進行中」或被默默刪掉")


def test_decided_series_drops_if_necessary_games():
    """七戰四勝:4-0 結束後 Yahoo 仍列著「如有必要」的 G5,不可當成下一場。"""
    finals = [yahoo(f"g{i}", f"{day}, {17 + i} Oct 2026 09:05:00 +0000", status="status.type.final",
                    away_pts="1", home_pts="5") for i, day in ((1, "Sun"), (2, "Mon"), (3, "Tue"), (4, "Wed"))]
    games = cpbl_games(*finals, yahoo("g5", "Thu, 22 Oct 2026 09:05:00 +0000"))
    now = dt.datetime(2026, 10, 22, 6, tzinfo=TPE)
    [post] = ss.cpbl_postseason(games, now)["series"]
    assert post["decided"] and post["teams"][0] == ("味全", 4) and post["next"] is None
    assert "g5" not in post["numbers"], "如有必要的場次不編號"
    assert ss.cpbl_postseason(games, dt.datetime(2026, 10, 24, 6, tzinfo=TPE)) == {}
    # gpt-6.1-sol C2 P2:渲染端也不可把 G5 跟「系列結束」一起列出來
    fixtures = [{"away": "中信", "home": "味全", "gid": "g5", "date": "10/22", "start": "17:05"}]
    out = mr._render_sports_html({"cpbl_post": {"series": [post]}, "cpbl_fixtures": fixtures}, html)
    assert "系列結束" in out and "10/22 17:05" not in out


def test_future_taiwan_series_does_not_hide_the_playoff_still_in_progress():
    """gpt-6.1-sol C2 P2:季後挑戰賽還在打時台灣大賽賽程已公布 → 兩個都列,昨天有比分的先列。"""
    games = cpbl_games(
        playoff("p3", "Sun, 11 Oct 2026 09:05:00 +0000", status="status.type.final", away_pts="4", home_pts="2"),
        playoff("p4", "Tue, 13 Oct 2026 10:35:00 +0000"),
        yahoo("t1", "Sat, 17 Oct 2026 09:05:00 +0000", away="cpbl.t.1", home="cpbl.t.9"))   # 對手未定
    now = dt.datetime(2026, 10, 12, 6, tzinfo=TPE)
    series = ss.cpbl_postseason(games, now)["series"]
    assert [s["label"] for s in series] == ["季後挑戰賽", "台灣大賽"]
    assert series[0]["results"] and series[0]["next"]["game"] == 2
    fixtures = [{"away": "中信", "home": "味全", "gid": gid, "date": d, "start": "18:35"}
                for gid, d in (("p4", "10/13"), ("t1", "10/17"))]
    out = mr._render_sports_html({"cpbl_post": {"series": series}, "cpbl_fixtures": fixtures}, html)
    assert out.index("季後挑戰賽") < out.index("10/13 18:35") < out.index("中華職棒 台灣大賽") < out.index("10/17 18:35")


def test_parallel_division_series_in_the_same_round_are_all_kept():
    """gpt-6.1-sol C2 P2:分區系列賽每聯盟同時兩組,不可用回合名稱互相覆蓋。"""
    events = [
        mlb_event("ALDS - Game 3", "2026-10-07T20:00Z", "post", away=("1", "NYY", "2", False),
                  home=("2", "HOU", "5", True), wins=(("2", 2), ("1", 1))),
        mlb_event("ALDS - Game 3", "2026-10-08T00:00Z", "post", away=("3", "BOS", "6", True),
                  home=("4", "SEA", "1", False), wins=(("3", 3), ("4", 0)), completed_series=True),
    ]
    post = ss.mlb_postseason(events, dt.datetime(2026, 10, 8, 18, tzinfo=TPE))
    assert sorted(tuple(t for t, _ in s["teams"]) for s in post["series"]) == [("BOS", "SEA"), ("HOU", "NYY")]


def test_cpbl_postseason_gives_up_when_any_day_is_missing():
    """漏一天就會少算勝場:任何一天抓不到,整段放棄,版面退回例行賽樣式。"""
    calls, logs = [], []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def get(url, params=None, headers=None, timeout=None):
        calls.append(params["dates"] if "dates" in params else params["date"])
        if "espn" in url:
            return Response({"events": []})
        if params["date"] == "2026-10-10":
            raise TimeoutError("one bad day")
        return Response({"service": {"scoreboard": {"games": {"g3": yahoo("g3", "Tue, 20 Oct 2026 10:35:00 +0000")},
                                                    "teams": TEAMS, "leagues": LEAGUES}}})
    degraded = []
    out = ss.fetch_posts(get, NOW, logs.append, degraded.append)
    assert "cpbl_post" not in out and any("整段放棄" in line for line in logs)
    # xhigh 第七輪:降級要留下痕跡(::warning:: + 降級標籤),不只是退回例行賽版面
    assert any(line.startswith("::warning::") for line in logs) and degraded == ["sports:cpbl_postseason_failed"]
    assert ss.fetch_posts(get, dt.datetime(2026, 7, 15, 6, tzinfo=TPE), logs.append, degraded.append) == {}, \
        "夏天不查中職季後賽"


def test_tennis_in_progress_uses_end_date_not_espn_state():
    shanghai = {"date": "2026-10-05T04:00Z", "endDate": "2026-10-19T03:59Z",
                "status": {"type": {"state": "post", "completed": True}}}
    assert ss.tennis_state(shanghai, NOW) == "in"
    assert ss.tennis_state({**shanghai, "date": "2026-10-19T04:00Z", "endDate": "2026-10-26T03:59Z"}, NOW) == "pre"
    assert ss.tennis_state({**shanghai, "endDate": "2026-10-12T03:59Z"}, NOW) == "post"
    assert ss.fresh("2026-10-12T22:00Z", NOW) and not ss.fresh("2026-10-06T08:00Z", NOW)
    assert not ss.fresh("", NOW), "時間不明不算新鮮"


def test_fetch_tennis_digest_lists_running_masters_first_and_drops_week_old_champion(monkeypatch):
    def match(cid, day, rnd):
        return {"id": cid, "date": day, "round": {"displayName": rnd},
                "status": {"type": {"completed": True}},
                "competitors": [{"athlete": {"shortName": f"W{cid}"}, "winner": True},
                                {"athlete": {"shortName": f"L{cid}"}, "winner": False}]}

    def event(name, start, end, comps=()):
        return {"shortName": name, "date": start, "endDate": end,
                "status": {"type": {"state": "post", "completed": True}},
                "groupings": [{"grouping": {"slug": "mens-singles"}, "competitions": list(comps)}]}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [
                event("Almaty Open", "2026-10-19T04:00Z", "2026-10-26T03:59Z"),
                event("China Open", "2026-09-27T04:00Z", "2026-10-12T03:59Z",
                      [match("c1", "2026-10-06T08:00Z", "Final")]),
                event("Rolex Shanghai Masters", "2026-10-05T04:00Z", "2026-10-19T03:59Z",
                      [match("s1", "2026-10-13T08:00Z", "Round 4")]),
            ]}
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: Response())
    out = mr.fetch_tennis_digest(NOW)
    assert out["tournaments"][0]["status"] == "進行中" and "Shanghai" in out["tournaments"][0]["name"]
    assert [r["winner"] for r in out["results"]] == ["Ws1"], "一週前的 China Open 冠軍不再列"


def test_award_market_near_certainty_is_just_the_favourite():
    assert ss.award_line([{"name": "Cam Schlittler", "prob": 99}], str) == "Cam Schlittler(大熱門)"
    assert ss.award_line([{"name": "A", "prob": 60}], lambda rows: "LINE") == "LINE"
    sports = {"standings": {"美聯": [{"team": "TB", "record": "98-64", "pct": .605}]},
              "poly": {"mlb_al_mvp": [{"name": "Yordan Alvarez", "prob": 94}],
                       "mlb_al_cy": [{"name": "Cam Schlittler", "prob": 99}, {"name": "X", "prob": 1}]}}
    rendered = mr._render_sports_html(sports, html)
    assert "Cam Schlittler(大熱門)" in rendered and "X 1%" not in rendered
    assert "Yordan Alvarez 94%" in rendered, "未達 95% 照常列機率"


def test_low_information_sports_pages_are_filtered():
    for title in ("台灣大賽門票怎麼買？整理一次看【2026 CPBL】 - Yahoo運動",
                  "Vit Kopriva 0 - 3 诺瓦克·德约科维奇：比賽報告、統計數據、陣容和H2H - 365Scores",
                  "季後賽懶人包"):
        assert not sports_quality.news_allowed({"title": title})
    assert sports_quality.news_allowed({"title": "MLB季後賽》守護者逆襲光芒！搶美聯冠軍戰開門紅 - Yahoo運動"})


def test_focus_lists_next_24_hours_and_previews_the_taiwan_series_opener():
    sports = {"as_of": NOW.isoformat(),
              "mlb_post": {"upcoming": [
                  {"round": "國聯冠軍戰", "game": 3, "away": "MIL", "home": "LAD", "start": "2026-10-15T08:00:00+08:00"},
                  {"round": "美聯冠軍戰", "game": 3, "away": "TB", "home": "CLE", "start": "2026-10-16T06:00:00+08:00"},
                  {"round": "國聯冠軍戰", "game": 4, "away": "MIL", "home": "LAD", "start": "2026-10-16T09:00:00+08:00"}]},
              "cpbl_post": {"series": [{"label": "台灣大賽", "next": {
                  "game": 1, "away": "中信", "home": "味全", "when": "10/17 17:05",
                  "start": "2026-10-17T17:05:00+08:00"}}]}}
    assert ss.focus_items(sports) == ["08:00 國聯冠軍戰 G3 MIL @ LAD", "明日 06:00 美聯冠軍戰 G3 TB @ CLE",
                                      "台灣大賽 10/17 開打(中信 vs 味全)"]
    assert ss.focus_items({**sports, "as_of": ""}) == [], "沒有可靠時間就不列焦點"


def test_postseason_layout_replaces_standings_and_sorts_by_season_importance():
    sports = {
        "as_of": NOW.isoformat(),
        "nba": [{"text": "CLE 52:72 ORL", "date": "10/14 07:00", "note": "季前賽・已完賽"}],
        "nba_fixtures": [{"when": "10/15 07:30", "text": "BKN @ MIA", "phase": 1},
                         {"when": "10/21 08:00", "text": "OKC @ HOU", "phase": 2}],
        "standings": {"美聯": [{"team": "TB", "record": "98-64", "pct": .605}]},
        "mlb_fixtures": [{"when": "10/15 08:00", "text": "MIL @ LAD"}],
        "mlb_post": {"series": [{"round": "美聯冠軍戰", "teams": [("CLE", 1), ("TB", 1)], "best_of": 7,
                                 "completed": False}],
                     "results": [{"round": "美聯冠軍戰", "game": 2, "when": "10/14 08:00", "away": "CLE",
                                  "home": "TB", "away_score": "1", "home_score": "3", "winner": "TB"}],
                     "upcoming": [{"round": "美聯冠軍戰", "game": 3, "when": "10/16 06:00", "away": "TB",
                                   "home": "CLE", "start": "2026-10-16T06:00:00+08:00"}]},
        "cpbl": [{"rank": 1, "team": "中信兄弟", "wdl": "36-0-24", "pct": ".600", "gb": "-"}],
        "cpbl_label": "下半季",
        "cpbl_full_year": [{"rank": 1, "team": "味全龍", "wdl": "67-0-53", "pct": ".558", "gb": "-"}],
        "cpbl_fixtures": [{"away": "中信", "home": "味全", "gid": "g1", "date": "10/17", "start": "17:05",
                           "venue": "大巨蛋"}],
        "cpbl_post": {"series": [{"label": "台灣大賽", "best_of": 7, "numbers": {"g1": 1},
                                  "teams": [("中信", 0), ("味全", 0)], "results": [],
                                  "next": {"game": 1, "away": "中信", "home": "味全", "when": "10/17 17:05",
                                           "start": "2026-10-17T17:05:00+08:00"}}]},
    }
    out = mr._render_sports_html(sports, html)
    assert "MLB 季後賽" in out and "美聯冠軍戰" in out and "MLB 戰績（" not in out
    assert "MLB 未來一週焦點賽程" not in out, "季後賽期間不另列「強隊對戰」週賽程"
    assert "中華職棒 台灣大賽" in out and "<b>G1</b>" in out and "@大巨蛋" in out
    assert "中華職棒戰績（" not in out and "例行賽戰績:下半季 中信兄弟 36-0-24・全年 味全龍 67-0-53" in out
    assert "季前賽進行中" in out and "CLE 52:72" not in out
    assert "布魯克林籃網" not in out and "奧克拉荷馬雷霆 @ 休士頓火箭" in out, "季前賽賽程不列、例行賽開幕照列"
    assert out.index("今日焦點") < out.index("MLB 季後賽") < out.index("中華職棒 台灣大賽") < out.index("季前賽進行中")
    assert "體育快訊（MLB / 中職 / NBA）" in out, "標題順序跟著賽季重要性"


def test_playoff_series_never_reconstructs_a_record_that_misses_the_carried_win():
    """gpt-6.1-sol C2 第二輪:挑戰賽第一名帶一勝,來源比分看不到 → 不列戰績、不據此判定結束。
    台灣大賽排入挑戰賽隊伍才算結束,之後「如有必要」的場次不列。"""
    finals = [playoff(f"p{i}", f"{day}, {9 + i} Oct 2026 10:35:00 +0000", status="status.type.final",
                      away_pts="1", home_pts="5") for i, day in ((1, "Sat"), (2, "Sun"))]
    pending = playoff("p3", "Mon, 12 Oct 2026 10:35:00 +0000")
    now = dt.datetime(2026, 10, 12, 6, tzinfo=TPE)
    [post] = ss.cpbl_postseason(cpbl_games(*finals, pending), now)["series"]
    assert post["teams"] == [] and "一勝優勢" in post["note"] and not post["decided"]
    assert post["next"]["game"] == 3, "主場隊已 2 勝(實際可能已 3 勝),但不靠猜測判定結束"
    out = mr._render_sports_html({"cpbl_post": {"series": [post]}}, html)
    assert "一勝優勢" in out and "統一 <b>2</b>" not in out
    advanced = yahoo("t1", "Sat, 17 Oct 2026 09:05:00 +0000", away="cpbl.t.1", home="cpbl.t.2")
    series = ss.cpbl_postseason(cpbl_games(*finals, pending, advanced), now)["series"]
    done = next(s for s in series if s["label"] == "季後挑戰賽")
    assert done["decided"] and done["next"] is None and "p3" not in done["numbers"]


def test_complete_scores_still_give_the_taiwan_series_record():
    games = cpbl_games(*(yahoo(f"g{i}", f"{day}, {17 + i} Oct 2026 09:05:00 +0000", status="status.type.final",
                               away_pts="3", home_pts=str(h)) for i, day, h in ((1, "Sun", 5), (2, "Mon", 1))),
                       yahoo("g3", "Wed, 21 Oct 2026 10:35:00 +0000"))
    [post] = ss.cpbl_postseason(games, dt.datetime(2026, 10, 20, 6, tzinfo=TPE))["series"]
    assert dict(post["teams"]) == {"味全": 1, "中信": 1} and post["note"] == "" and post["next"]["game"] == 3


def test_counted_wins_are_a_lower_bound_so_reaching_four_still_ends_the_series():
    """比分缺漏只會少算勝場:有比分的 4 勝已達標 → 系列確定結束,如有必要的場次不列;
    但有缺漏時仍不列戰績數字。"""
    days = ((1, "Sun"), (2, "Mon"), (3, "Tue"), (4, "Wed"), (5, "Thu"))
    finals = [yahoo(f"g{i}", f"{day}, {17 + i} Oct 2026 09:05:00 +0000", status="status.type.final",
                    away_pts="1" if i != 3 else "", home_pts="5") for i, day in days]
    games = cpbl_games(*finals, yahoo("g6", "Fri, 23 Oct 2026 10:35:00 +0000"))
    [post] = ss.cpbl_postseason(games, dt.datetime(2026, 10, 23, 6, tzinfo=TPE))["series"]
    assert post["decided"] and post["next"] is None and "g6" not in post["numbers"]
    assert post["teams"] == [] and "比分缺漏" in post["note"]


def test_off_day_is_empty_but_a_broken_scoreboard_is_missing_data():
    """gpt-6.1-sol C2 第三輪:HTTP 200 但結構缺漏 ≠ 休兵日。休兵日 Yahoo 本來就沒有 games 鍵
    (10/13、10/15、10/16 實測),所以判準是「中職聯盟資訊在不在」,不是「games 在不在」。"""
    assert ss.yahoo_cpbl_games({"leagues": LEAGUES, "divisions": {}}) == []
    for broken in (None, {}, {"games": {"g": {}}, "teams": TEAMS}, {"leagues": LEAGUES, "games": {"g": {}}}):
        try:
            ss.yahoo_cpbl_games(broken)
        except ValueError:
            continue
        raise AssertionError(f"缺資料被當成休兵日:{broken}")


def test_fetch_gives_up_on_a_successful_but_empty_response(monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    logs = []

    def get(url, params=None, headers=None, timeout=None):
        if "espn" in url:
            return Response({"events": []})
        if params["date"] == "2026-10-10":
            return Response({"service": {}})                    # 200,但沒有 scoreboard
        if params["date"] == "2026-10-17":
            return Response({"service": {"scoreboard": {"games": {"t1": yahoo("t1", "Sat, 17 Oct 2026 09:05:00 +0000")},
                                                        "teams": TEAMS, "leagues": LEAGUES}}})
        return Response({"service": {"scoreboard": {"leagues": LEAGUES}}})   # 休兵日
    degraded = []
    assert "cpbl_post" not in ss.fetch_posts(get, NOW, logs.append, degraded.append)
    assert degraded == ["sports:cpbl_postseason_failed"]
    get_ok = lambda url, **kw: (Response({"service": {"scoreboard": {"leagues": LEAGUES}}})   # noqa: E731
                                if kw["params"].get("date") == "2026-10-10" else get(url, **kw))
    assert ss.fetch_posts(get_ok, NOW, logs.append, degraded.append)["cpbl_post"]["series"][0]["next"]["game"] == 1
    assert degraded == ["sports:cpbl_postseason_failed"], "成功的那次不再記降級"


def test_provisional_final_is_shown_but_never_counted_as_a_win():
    """gpt-6.1-sol C2 第三輪:標示完賽但只打到第 3 局(minimum 9)= 暫列比分,與「最新賽果」同一判準。"""
    short = yahoo("g4", "Wed, 21 Oct 2026 09:05:00 +0000", status="status.type.final", away_pts="0", home_pts="3")
    short.update(current_period_id=3, minimum_periods=9)
    wins = [yahoo(f"g{i}", f"{day}, {17 + i} Oct 2026 09:05:00 +0000", status="status.type.final",
                  away_pts="1", home_pts="5") for i, day in ((1, "Sun"), (2, "Mon"), (3, "Tue"))]
    games = cpbl_games(*wins, short, yahoo("g5", "Thu, 22 Oct 2026 10:35:00 +0000"))
    [post] = ss.cpbl_postseason(games, dt.datetime(2026, 10, 22, 6, tzinfo=TPE))["series"]
    assert not post["decided"] and post["next"]["game"] == 5, "暫列的第 4 場不可湊成 4 勝"
    assert post["teams"] == [] and "結束方式待確認" in post["note"]
    out = mr._render_sports_html({"cpbl_post": {"series": [post]}}, html)
    assert "結束方式待確認" in out and "<b style='color:#b91c1c;'>味全 3</b>" not in out


def test_postponed_mlb_game_is_neither_a_result_nor_the_next_game():
    """gpt-6.1-sol C2 第四輪:與 NBA 同一份例外狀態清單。"""
    rained = mlb_event("ALCS - Game 2", "2026-10-14T00:00Z", "post")
    rained["status"]["type"]["name"] = "STATUS_POSTPONED"
    later = mlb_event("ALCS - Game 3", "2026-10-15T22:00Z", "pre")
    later["status"]["type"]["name"] = "STATUS_CANCELED"
    post = ss.mlb_postseason([rained, later], NOW)
    assert post["results"] == [] and post["upcoming"] == []
    assert post["series"], "系列戰況仍來自來源的 series 欄位"


def test_postseason_game_without_a_start_time_abandons_the_summary():
    games = cpbl_games(yahoo("g1", "", status="status.type.final", away_pts="1", home_pts="5"),
                       yahoo("g2", "Sun, 18 Oct 2026 09:05:00 +0000"))
    try:
        ss.cpbl_postseason(games, NOW)
    except ValueError:
        return
    raise AssertionError("缺開賽時間的季後賽場次被默默丟掉")


def test_series_reaching_back_to_the_lookback_edge_is_not_published():
    """gpt-6.1-sol C2 第四輪:每天都抓成功 ≠ 整個系列都在窗口內(延賽可能把系列拉長)。"""
    games = cpbl_games(yahoo("g1", "Sat, 17 Oct 2026 09:05:00 +0000", status="status.type.final",
                             away_pts="1", home_pts="5"),
                       yahoo("g2", "Thu, 29 Oct 2026 10:35:00 +0000"))
    now = dt.datetime(2026, 10, 29, 6, tzinfo=TPE)
    assert ss.cpbl_postseason(games, now, dt.date(2026, 10, 14)) == {}, "第一場離窗口起點只有 3 天"
    assert ss.cpbl_postseason(games, now, dt.date(2026, 10, 8))["series"][0]["next"]["game"] == 2


def test_completed_game_without_team_identity_abandons_instead_of_merging_wins():
    """xhigh 審查:teams 空掉時兩隊都變成同一個未知隊,2-2 會被算成某隊 4 勝而誤判結束。"""
    final = yahoo("g1", "Sat, 17 Oct 2026 09:05:00 +0000", status="status.type.final", away_pts="1", home_pts="5")
    try:
        ss.yahoo_cpbl_games({"games": {"g1": final}, "teams": {}, "leagues": LEAGUES})
    except ValueError:
        pass
    else:
        raise AssertionError("完賽場次缺球隊身分卻照算勝場")
    later = yahoo("t1", "Sat, 17 Oct 2026 09:05:00 +0000", away="cpbl.t.1", home="cpbl.t.99")
    [row] = ss.yahoo_cpbl_games({"games": {"t1": later}, "teams": TEAMS, "leagues": LEAGUES})
    assert row["home"] == "待定", "未打的場次對手可能未定,不放棄整段"


def test_known_next_game_is_rendered_even_when_the_fixture_feed_missed_it():
    games = cpbl_games(yahoo("g1", "Sat, 17 Oct 2026 09:05:00 +0000", status="status.type.final",
                             away_pts="1", home_pts="5"),
                       yahoo("g2", "Tue, 20 Oct 2026 10:35:00 +0000"))
    [post] = ss.cpbl_postseason(games, dt.datetime(2026, 10, 18, 6, tzinfo=TPE))["series"]
    out = mr._render_sports_html({"cpbl_post": {"series": [post]}, "cpbl_fixtures": []}, html)
    assert "10/20 18:35" in out and "<b>G2</b>" in out
    dup = [{"away": "中信", "home": "味全", "gid": "g2", "date": "10/20", "start": "18:35", "venue": "大巨蛋"}]
    out = mr._render_sports_html({"cpbl_post": {"series": [post]}, "cpbl_fixtures": dup}, html)
    assert out.count("<b>G2</b>") == 1 and "@大巨蛋" in out, "賽程有這一場就不重複補列"


def test_results_are_labelled_today_or_yesterday_by_the_taipei_report_date():
    early = mlb_event("ALCS - Game 2", "2026-10-14T17:00Z", "post")    # 10/15 01:00 台北
    late = mlb_event("NLCS - Game 3", "2026-10-14T00:00Z", "post", away=("8", "MIL", "2", False),
                     home=("19", "LAD", "5", True), wins=(("8", 2), ("19", 1)))   # 10/14 08:00 台北
    post = ss.mlb_postseason([early, late], NOW)
    assert {r["game"]: r["day"] for r in post["results"]} == {2: "今日", 3: "昨日"}
    out = ss.render_mlb_post(post, html.escape, str)
    assert "今日 10/15" in out and "昨日 10/14" in out


def test_next_game_survives_rest_days_and_only_the_nearest_is_listed():
    """xhigh 第六輪:06:00 的報告、下一場在兩天後 08:00(50 小時)仍要列;同系列只列最近一場。"""
    g3 = mlb_event("ALCS - Game 3", "2026-10-17T00:00Z", "pre", away=("30", "TB", "0", False),
                   home=("5", "CLE", "0", False))
    g4 = mlb_event("ALCS - Game 4", "2026-10-18T00:00Z", "pre", away=("30", "TB", "0", False),
                   home=("5", "CLE", "0", False))
    post = ss.mlb_postseason([g4, g3], NOW)
    assert [g["game"] for g in post["upcoming"]] == [3]
    assert ss.focus_items({"as_of": NOW.isoformat(), "mlb_post": post}) == [], "今日焦點仍只看 24 小時"


def test_postseason_layout_keeps_single_game_odds():
    post = {"series": [], "results": [], "upcoming": [
        {"round": "美聯冠軍戰", "game": 3, "when": "10/16 06:00", "away": "TB", "home": "CLE"}]}
    fixtures = [{"when": "10/16 06:00", "text": "TB @ CLE", "away_abbr": "TB", "home_abbr": "CLE",
                 "odds": "賭盤:CLE 55%・TB 45%(Polymarket)"}]
    out = mr._render_sports_html({"mlb_post": post, "mlb_fixtures": fixtures}, html)
    assert "賭盤" in out and "55%" in out and "MLB 未來一週焦點賽程" not in out


def test_nba_preseason_is_filtered_before_the_ten_game_cap(monkeypatch):
    pre = [{"text": f"P{i} @ Q{i}", "when": f"10/1{i % 9} 07:00", "phase": 1} for i in range(12)]
    opener = {"text": "OKC @ HOU", "when": "10/21 08:00", "phase": 2}
    monkeypatch.setattr(mr, "_espn_week_fixtures", lambda *a, **k: pre + [opener])
    monkeypatch.delenv("NBA_FAVORITE_TEAMS", raising=False)
    assert mr.fetch_nba_week_fixtures(NOW) == [opener]


def test_mlb_postseason_failure_leaves_a_warning_and_a_degraded_step():
    logs, degraded = [], []

    def get(*args, **kwargs):
        raise TimeoutError("espn offline")
    out = ss.fetch_posts(get, dt.datetime(2026, 7, 15, 6, tzinfo=TPE), logs.append, degraded.append)
    assert out == {} and degraded == ["sports:mlb_postseason_failed"]
    assert logs and logs[0].startswith("::warning::")


def test_production_wires_the_degradation_trail():
    """守衛要真的接上去:生產呼叫端傳的是 _DEGRADED_STEPS.append,log 走 stdout(::warning:: 才會被 Actions 讀到)。"""
    import inspect
    src = inspect.getsource(mr.fetch_sports_digest)
    assert "fetch_posts(_http_get, now_tpe, print, _DEGRADED_STEPS.append)" in src


def test_stale_unknown_status_game_is_not_dropped_and_renumbered():
    """xhigh 第九輪:G2 狀態沒更新、G3 已排定 —— 刪掉 G2 再重編號會把 G3 標成 G2、戰績少一場。"""
    g1 = yahoo("g1", "Sat, 17 Oct 2026 09:05:00 +0000", status="status.type.final", away_pts="1", home_pts="5")
    g2 = yahoo("g2", "Sun, 18 Oct 2026 09:05:00 +0000", status="")
    g3 = yahoo("g3", "Tue, 20 Oct 2026 10:35:00 +0000")
    try:
        ss.cpbl_postseason(cpbl_games(g1, g2, g3), dt.datetime(2026, 10, 19, 6, tzinfo=TPE))
    except ValueError:
        pass
    else:
        raise AssertionError("狀態不明的歷史場次被默默刪掉")
    finals = [yahoo(f"w{i}", f"{day}, {17 + i} Oct 2026 09:05:00 +0000", status="status.type.final",
                    away_pts="1", home_pts="5") for i, day in ((1, "Sun"), (2, "Mon"), (3, "Tue"), (4, "Wed"))]
    leftover = yahoo("w5", "Wed, 21 Oct 2026 03:00:00 +0000")      # 已過 6 小時、沒打的如有必要場次
    [post] = ss.cpbl_postseason(cpbl_games(*finals, leftover), dt.datetime(2026, 10, 22, 6, tzinfo=TPE))["series"]
    assert post["decided"] and "w5" not in post["numbers"], "已分勝負時剩下的才是如有必要場次"


def test_overlapping_date_buckets_keep_the_complete_final():
    """xhigh 第九輪:同一場出現在兩個日期桶,較晚那桶比分缺漏 —— 不可蓋掉完整賽果。"""
    full = yahoo("g1", "Sat, 17 Oct 2026 09:05:00 +0000", status="status.type.final", away_pts="1", home_pts="5")
    partial = {**full, "total_away_points": ""}
    later = yahoo("g2", "Tue, 20 Oct 2026 10:35:00 +0000")
    games = cpbl_games(full, later) + cpbl_games(partial)
    [post] = ss.cpbl_postseason(games, dt.datetime(2026, 10, 18, 6, tzinfo=TPE))["series"]
    assert post["teams"] == [("味全", 1), ("中信", 0)] and post["note"] == ""
    clash = cpbl_games({**full, "total_home_points": "6"})
    try:
        ss.cpbl_postseason(cpbl_games(full, later) + clash, dt.datetime(2026, 10, 18, 6, tzinfo=TPE))
    except ValueError:
        return
    raise AssertionError("兩個完整但矛盾的版本被默默擇一")
