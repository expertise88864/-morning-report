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
        "scores": {
            "MLB": [{"text": "<b>MIA</b> 2:0 ARI", "final": True, "note": ""}],
            "NBA": [],
        },
        "news": {"中華職棒": ["兄弟逆轉勝 悍將吞三連敗"], "網球": []},
    }
    h = mr._render_sports_html(sports, htmllib)
    assert "體育快訊" in h
    assert "MLB 昨日比分" in h and "2:0" in h
    assert "NBA" in h and "昨日無賽事" in h
    assert "中華職棒 消息" in h and "兄弟逆轉勝" in h
    assert mr._render_sports_html({}, htmllib) == ""


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
