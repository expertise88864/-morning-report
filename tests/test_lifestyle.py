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
    assert "分批買入參考" in h and "分批調節參考" in h
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
    monkeypatch.setattr(mr.feedparser, "parse", lambda *a, **k: Feed())
    out = mr.fetch_tw_daily_intelligence(
        dt.datetime(2026, 6, 3, 6, tzinfo=mr.TPE), per_kind_limit=8)
    titles = [item["title"] for item in out["medical"]]
    assert sum(1 for t in titles if "中榮" in t) <= 1
