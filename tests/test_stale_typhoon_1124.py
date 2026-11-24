"""11-24 實信:10 月的薔琵停班課公告被重新收錄,寫成 11/23 停班停課。"""
import datetime as dt

import news_display_quality as q
import typhoon_staleness as t

TODAY = dt.date(2026, 11, 24)
OLD = "薔琵颱風來襲新北23日停班停課新北捷運調整班距 - 新北大眾捷運"


def _row(title, day):
    return {"key": title, "headline": title, "first_seen": day, "timeline": [{"d": day, "t": title}]}


LEDGER = [_row("強烈颱風「薔琵」逼近", "2026-10-20"), _row("薔琵颱風動態一次看", "2026-10-21"),
          _row("「薔琵」颱風最新路徑", "2026-10-21"), _row("薔琵颱風停班停課", "2026-10-22"),
          _row("沙德爾颱風「復活」路徑曝", "2026-09-01"), _row("颱風「這天」最近", "2026-09-01")]


def test_reindexed_live_news_is_dropped_everywhere():
    quotes = {"STORY_LEDGER": LEDGER + [_row(OLD, "2026-11-24")], "EVENT_TIMELINE": [{"latest_title": OLD}, {"latest_title": "x", "zh_title": OLD}],
              "STRUCTURED_NEWS_EVENTS": [{"title": OLD}], "NEWS_RESEARCH_SOURCES": [{"title": OLD}]}
    news, out = q.current_inputs([{"title": OLD}, {"title": "台積電法說"}], quotes, TODAY)
    assert [n["title"] for n in news] == ["台積電法說"]
    assert out["EVENT_TIMELINE"] == out["STRUCTURED_NEWS_EVENTS"] == out["NEWS_RESEARCH_SOURCES"] == []
    assert OLD not in [r["headline"] for r in out["STORY_LEDGER"]]
    assert len(quotes["STORY_LEDGER"]) == 7, "存檔用的帳本不被改寫"


def test_aftermath_and_new_typhoons_stay():
    names = t.old_names(LEDGER, TODAY)
    assert names == {"薔琵"}
    for title in ("薔琵颱風農損救助受理", "薔琵颱風重創吉安米 收成減半", "颱風「新名」逼近 停班停課一覽",
                  "新颱風逼近台灣，路徑與薔琵颱風相似", "薔琵颱風上月擦過台灣，海葵今逼近東部", "薔琵颱風上月登陸，海葵今逼近東部", "薔琵颱風10月登陸，海葵今逼近", "薔琵颱風剛走 海葵逼近 停班停課",
                  "薔琵颱風停班停課 海葵颱風逼近", "海葵逼近 路徑恐比薔琵颱風更近", "新颱風復活 專家:這天最近 最新路徑", "薔琵餐廳動態"):
        assert not t.stale(title, names), title


def test_a_current_typhoon_is_never_old():
    assert t.old_names(LEDGER[:4], dt.date(2026, 10, 24)) == set()


def test_broken_ledger_filters_nothing():
    for ledger in (None, "x", [None, 3, {"headline": "a"}, {"first_seen": "bad"}]):
        news, _ = q.current_inputs([{"title": OLD}], {"STORY_LEDGER": ledger}, dt.date(2026, 11, 23))
        assert news == [{"title": OLD}]


def test_review_pr65_old_story_with_a_new_headline_is_not_old_evidence():
    """線索 first_seen 很舊、headline 換成今天的新颱風:不能把新颱風當成 21 天前就有。"""
    rows = [dict(_row(h, "2026-10-01"), timeline=[{"d": "2026-10-01", "t": "防颱整備"}, {"d": "2026-11-23", "t": h}])
            for h in ("「海葵」颱風逼近", "海葵颱風路徑", "海葵颱風停班停課")]
    assert t.old_names(rows, TODAY) == set()
