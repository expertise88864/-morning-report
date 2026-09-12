import datetime as dt
from copy import deepcopy

import sports_news_selection as sn

NOW = dt.datetime(2026, 9, 12, 7, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def test_duplicate_slots_are_refilled_after_selection():
    rows = [{"title": t, "link": f"https://example.com/{i}"} for i, t in enumerate([
        "球員復出 - 媒體甲", "球員復出 - 媒體乙", "戰績更新", "總教練宣布退休"])]
    before = deepcopy(rows)
    out, report = sn.select(rows, NOW)
    assert [r["title"] for r in out] == ["球員復出 - 媒體甲", "戰績更新", "總教練宣布退休"]
    assert report["duplicates"] == 1 and report["selected"] == 3
    assert rows == before


def test_old_scorecard_and_tip_post_are_excluded():
    out, report = sn.select([
        {"title": "中信兄弟 3 : 9 味全龍 (04/19) | 中職2026年 - LINE TODAY"},
        {"title": "[推薦] 網球 WTA美國公開賽 - CMoney"},
        {"title": "美網女單決賽今日開打"}], NOW)
    assert [r["title"] for r in out] == ["美網女單決賽今日開打"]
    assert report["excluded"] == 2


def test_recent_scorecard_and_historical_comparison_remain():
    assert not sn.stale_scorecard("中信 3:9 味全 (09/11) | 中職", NOW.date())
    assert not sn.stale_scorecard("回顧04/19的3:9敗仗，今日戰術如何調整", NOW.date())
    assert not sn.stale_scorecard("比分3:9 (02/30) | 日期未知", NOW.date())
    assert not sn.stale_scorecard("3:9 (12/31) | 年末戰績", dt.date(2027, 1, 1))


def test_feed_age_uses_report_clock_not_wall_clock():
    rows = [{"title": title, "published_parsed": day} for title, day in [
        ("新稿", (2026, 9, 11, 22, 0, 0)),
        ("舊稿", (2026, 9, 10, 10, 0, 0)),
        ("未來稿", (2026, 9, 13, 0, 0, 0))]]
    out, report = sn.select(rows, NOW)
    assert [r["title"] for r in out] == ["新稿"]
    assert report["excluded"] == 2


def test_full_titles_are_compared_before_any_display_truncation():
    prefix = "甲" * 100
    out, _ = sn.select([{"title": prefix + "勝"}, {"title": prefix + "敗"}], NOW)
    assert len(out) == 2 and all(len(r["title"]) > 90 for r in out)
