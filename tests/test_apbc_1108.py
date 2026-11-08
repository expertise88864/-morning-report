"""亞冠賽中華隊賽程與比分(使用者 2026-11-08:中職資料源沒有國家隊賽事)。"""
import datetime as dt
import html

import apbc
import morning_report as mr

TPE = apbc.TPE


def _ls(date, time, road, home, rr="-", hr="-"):
    return (f"{{{{linescore\n|Date= {date}\n|Time= {time} ([[日本標準時間|JST]])\n"
            f"|Road= {{{{bb|{road}}}}}\n|RoadAbr= {road}\n|R1=-|RR={rr}|RH=-|RE=-\n"
            f"|Home= {{{{bb|{home}}}}}\n|HomeAbr= {home}\n|H1=-|HR={hr}|HH=-|HE=-\n}}}}\n")


def _page(jpn_tpe=("-", "-"), finals=(("", ""), ("", ""))):
    return ("== 預賽 ==\n" + _ls("2026年11月12日", "12:00", "AUS", "KOR", "3", "5")
            + _ls("2026年11月12日", "19:00", "JPN", "TPE", *jpn_tpe)
            + _ls("2026年11月13日", "12:00", "TPE", "AUS")
            + "== 決賽 ==\n=== 季軍賽 ===\n" + _ls("2026年11月15日", "11:00", *finals[0])
            + "=== 冠軍賽 ===\n" + _ls("2026年11月15日", "19:00", *finals[1]) + "== 獎項 ==\n")


def _at(text):
    return dt.datetime.strptime("2026/" + text, "%Y/%m/%d %H:%M").replace(tzinfo=TPE)


def test_parse_converts_jst_to_taipei_and_tags_stages():
    games = apbc.parse(_page())
    assert [(g["stage"], g["away"], g["home"], g["start"].strftime("%m/%d %H:%M")) for g in games] == [
        ("預賽", "AUS", "KOR", "11/12 11:00"), ("預賽", "JPN", "TPE", "11/12 18:00"),
        ("預賽", "TPE", "AUS", "11/13 11:00"), ("季軍賽", "", "", "11/15 10:00"),
        ("冠軍賽", "", "", "11/15 18:00")]
    assert (games[0]["away_score"], games[0]["home_score"]) == (3, 5)
    assert games[1]["away_score"] is None


def test_only_shown_from_day_before_until_day_after():
    games = apbc.parse(_page())
    assert apbc.select(games, _at("11/10 06:00")) == {}
    assert apbc.select(games, _at("11/11 06:00"))
    assert apbc.select(games, _at("11/16 06:00"))
    assert apbc.select(games, _at("11/17 06:00")) == {}


def test_only_taiwan_games_and_tbd_finals_with_scores_and_record():
    out = apbc.select(apbc.parse(_page(jpn_tpe=("2", "4"))), _at("11/13 06:00"))
    assert [(r["stage"], r["away"], r["home"], r["score"]) for r in out["rows"]] == [
        ("預賽", "日本", "中華隊", (2, 4)), ("預賽", "中華隊", "澳洲", None),
        ("季軍賽", "待定", "待定", None), ("冠軍賽", "待定", "待定", None)]
    assert out["record"] == [1, 0, 0]
    page = apbc.render(out, html.escape)
    assert "預賽 1 勝 0 敗" in page and "<b style='color:#b91c1c;'>中華隊 4</b>" in page


def test_score_typed_in_during_the_game_is_not_a_result():
    out = apbc.select(apbc.parse(_page(jpn_tpe=("1", "0"))), _at("11/12 19:00"))
    assert out["rows"][0]["score"] is None and out["record"] == [0, 0, 0]


def test_final_with_only_one_team_known_is_still_pending():
    out = apbc.select(apbc.parse(_page(finals=(("KOR", ""), ("JPN", "")))), _at("11/15 06:00"))
    assert [(r["stage"], r["away"], r["home"]) for r in out["rows"]][-2:] == [
        ("季軍賽", "韓國", "待定"), ("冠軍賽", "日本", "待定")]


def test_once_taiwan_is_placed_only_its_final_is_listed():
    out = apbc.select(apbc.parse(_page(finals=(("KOR", "AUS"), ("JPN", "TPE")))), _at("11/15 06:00"))
    assert [(r["stage"], r["away"], r["home"]) for r in out["rows"]][-1:] == [("冠軍賽", "日本", "中華隊")]
    assert all(r["stage"] != "季軍賽" for r in out["rows"])


class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_fetch_missing_page_is_silent_and_bad_page_degrades():
    now, logs, degraded = _at("11/13 06:00"), [], []
    missing = lambda *a, **k: _Resp({"error": {"code": "missingtitle"}})   # noqa: E731
    assert apbc.fetch(missing, now, logs.append, degraded.append) == {} and not logs and not degraded
    api_err = lambda *a, **k: _Resp({"error": {"code": "internal_api_error_DBQueryError"}})   # noqa: E731
    assert apbc.fetch(api_err, now, logs.append, degraded.append) == {}
    assert degraded == ["sports:apbc_failed"]
    degraded.clear()
    bad = lambda *a, **k: _Resp({"parse": {"wikitext": "{{linescore\n|Date= 待定\n}}"}})   # noqa: E731
    assert apbc.fetch(bad, now, logs.append, degraded.append) == {}
    assert logs[0].startswith("::warning::") and degraded == ["sports:apbc_failed"]
    assert apbc.fetch(bad, _at("11/13 06:00").replace(month=10), logs.append, degraded.append) == {}
    assert len(degraded) == 1, "11 月以外不查"


def test_sports_card_lists_apbc_first_and_in_title():
    sports = {"apbc": apbc.select(apbc.parse(_page()), _at("11/12 06:00")),
              "mlb_tw": [{"date": "11/11", "name": "某球員", "role": "投手", "summary": "1 局無失分"}]}
    out = mr._render_sports_html(sports, html)
    assert "體育快訊（亞冠賽 / MLB）" in out and out.index("亞冠賽 中華隊") < out.index("某球員")
    assert "體育快訊（亞冠賽" in out and "亞冠賽 中華隊" in out
    assert "11/12 18:00</span>　預賽　日本 vs 中華隊" in out
