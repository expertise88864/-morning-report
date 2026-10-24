"""2026-10-24 颱風休市(10/23)後的實信:觀察點誤報漏回顧、昨日觀點被整包隔離。"""
import analysis_origin as ao
import analysis_recap as rc
import fallback_recap as fr
import fallback_watch_gap as gap


def _watch(watch_id, created, deadline, last_reviewed):
    return {"watch_id": watch_id, "created": created, "deadline": deadline,
            "last_reviewed": last_reviewed, "status": "open", "trigger": "t"}


def test_deadline_on_a_closed_day_counts_the_previous_session_review():
    """w64 期限 10/24(週六),10/23 那班已回顧;10/24 的目標交易日是 10/26,不能再回顧。"""
    state = {"date": "2026-10-23", "watch": [
        _watch("w64", "2026-10-19", "2026-10-24", "2026-10-23"),
        _watch("w90", "2026-10-19", "2026-10-24", "2026-10-21")]}   # 上一交易日沒回顧到:仍要報
    record = gap.due_cases(state, "2026-10-24", "2026-10-26", "2026-10-22")
    assert [c["watch_id"] for c in record["cases"]] == ["w90"], record
    # 期限當天就是交易日(10/21 走備援那天):這一班本身就是回顧機會,照舊要報
    state = {"date": "2026-10-20", "watch": [_watch("w53", "2026-09-23", "2026-10-21", "2026-10-20")]}
    assert gap.due_cases(state, "2026-10-21", "2026-10-21", "2026-10-20")["count"] == 1


def test_a_recap_from_the_unscheduled_closure_day_is_still_yesterday():
    """10/23 颱風休市那班的觀點比上一個交易日(10/22)新,中間沒有漏交易日。"""
    recap = {"date": "2026-10-23", "items": [{"statement": "外資期貨淨空未回補。"}]}
    assert rc.usable(recap, "2026-10-26", "2026-10-22")
    # 仍擋:早於上一個交易日(中間漏了交易日)、以及同日或未來
    assert rc.usable(dict(recap, date="2026-10-21"), "2026-10-26", "2026-10-22") == []
    assert rc.usable(dict(recap, date="2026-10-26"), "2026-10-26", "2026-10-22") == []
    url = "https://example.com/a"
    record = fr.extract(f"## 八、科技板塊脈動\n外資期貨淨空未回補。[來源]({url})\n", [{"link": url}],
                        "2026-10-23", ao.LEGACY_PRIMARY)
    projected = fr.for_prompt(record, "2026-10-22", "2026-10-26")
    assert projected["items"] and projected["date"] == "2026-10-23"
    assert fr.for_prompt(record, "2026-10-26", "2026-10-27") == {}


def test_official_suspension_replaces_the_forecast_and_merges_counties():
    """10-24 實信:人事總處已公告停班停課,卡片仍印「風雨接近停班停課參考標準」預測,
    人事總處四個縣市各一行、內容一字不差 —— 使用者:「解決今日重複訊息」。"""
    import morning_report as mr
    url = "https://www.dgpa.gov.tw/typh/daily/nds.html"
    locs = [{"name": "彰化市", "t_min": 24, "t_max": 27, "rain_prob": 100, "label": "陣雨",
             "wind": 30, "gust": 74, "rain_sum": 9},
            {"name": "台中北區", "t_min": 23, "t_max": 25, "rain_prob": 100, "label": "陣雨",
             "wind": 30, "gust": 76, "rain_sum": 19}]
    status = "今天已達停止上班及上課標準。"
    susp = [{"title": f"人事總處公告:{c} {status}", "link": url} for c in ("臺中市", "彰化縣", "雲林縣", "南投縣")]
    html = mr._render_weather_html(locs, susp)
    assert "接近停班停課參考標準" not in html, html
    assert html.count("人事總處公告") == 1 and "臺中市、彰化縣、雲林縣、南投縣 " + status in html
    # 沒有正式公告的地點仍要看到預測;不同狀態不併
    other = [{"title": f"人事總處公告:臺中市 {status}", "link": url},
             {"title": "人事總處公告:彰化縣 今天照常上班、照常上課。", "link": url}]
    html = mr._render_weather_html(locs + [dict(locs[0], name="南投市")], other)
    assert "南投市 風雨接近停班停課參考標準" in html and "彰化市 風雨" not in html
    assert html.count("人事總處公告") == 2
