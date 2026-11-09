"""11-09(週一)實信:「昨日台股」「半導體端昨夜」其實是上週五;東北季風被當成停班停課;傳聞整件標未證實。"""
import pathlib

import morning_report as mr
import report_polish as rp
import session_wording as sw

MON = {"TARGET_SESSION": "2026-11-09", "LAST_TRADING_SESSION": "2026-11-06"}
TUE = {"TARGET_SESSION": "2026-11-10", "LAST_TRADING_SESSION": "2026-11-09"}


def test_monday_yesterday_market_wording_becomes_last_friday():
    text = ("昨日台股大幅波動。半導體端昨夜很強,昨夜美股收高,昨日外資賣超。\n"
            "## 三、昨夜三大重點\n川普昨夜表示會談有進展。")
    out = sw.rewrite(text, MON, today="2026-11-09")
    assert "上週五台股大幅波動" in out and "半導體端上週五很強" in out
    assert "上週五美股收高" in out and "上週五外資賣超" in out
    assert "## 三、昨夜三大重點" in out and "川普昨夜表示" in out   # 段名與週末新聞不動
    assert sw.rewrite(text, TUE, today="2026-11-10") == text      # 一般交易日不動


def test_monday_prompt_and_program_written_line():
    note = sw.prompt_note(MON, today="2026-11-09")
    assert "上一交易日是上週五(11/06)" in note and "不可寫「昨日台股」" in note
    assert sw.prompt_note(TUE, today="2026-11-10") == ""
    assert sw.yesterday(MON, today="2026-11-09") == "上週五" and sw.yesterday(TUE, today="2026-11-10") == "昨日"
    assert sw.yesterday({"TARGET_SESSION": "2026-10-13", "LAST_TRADING_SESSION": "2026-10-09"},
                        today="2026-10-13") == "上週五"   # 週一國慶連假後的週二
    assert sw.yesterday({"TARGET_SESSION": "2026-02-23", "LAST_TRADING_SESSION": "2026-02-11"},
                        today="2026-02-23") == "2/11"     # 春節長假
    assert sw.yesterday({}, today="2026-11-09") == "昨日"
    # 週六仍走「下一交易日」的提示,不改成上週五
    assert "11/7（週六）台股休市" in sw.prompt_note(MON, today="2026-11-07")
    breadth = {"advance_ratio": 9.6}
    assert rp.big_move_line(-4.51, breadth, "上週五").startswith("上週五台股大幅波動:加權 -4.51%")
    assert "上週五台股大幅波動" in rp.big_move_rule(-4.51, breadth, "上週五")
    assert rp.big_move_line(-4.51, breadth).startswith("昨日台股大幅波動")


def test_reader_claims_apply_monday_wording(monkeypatch):
    import legacy_actor_guard as g
    monkeypatch.setattr(sw, "_days", lambda quotes, today=None: None)
    out = g.correct_reader_claims("## 一、結論\n半導體端昨夜很強,昨日台股重挫。\n", [],
                                  origin="luna_primary", manifest={}, quotes=MON)
    assert "半導體端上週五很強" in out and "上週五台股重挫" in out


def test_monsoon_gust_is_not_a_typhoon_signal():
    locs = [{"name": "台中北區", "t_min": 17, "t_max": 26, "rain_prob": 0, "label": "多雲時晴",
             "wind": 30, "gust": 76, "rain_sum": 0}]
    assert "停班停課" not in mr._render_weather_html(locs, [], [])
    typhoon = [{"title": "海上陸上颱風警報", "link": "https://www.cwa.gov.tw/", "typhoon": True, "counties": []}]
    assert "台中北區 風雨接近停班停課參考標準" in mr._render_weather_html(locs, [], typhoon)
    other = [{"title": "強風特報", "link": "https://www.cwa.gov.tw/", "typhoon": False, "counties": ["臺中市"]}]
    assert "接近停班停課" not in mr._render_weather_html(locs, [], other)


def test_rumor_rule_names_the_unconfirmed_part():
    src = pathlib.Path(mr.__file__).read_text(encoding="utf-8")
    assert "lifecycle=rumor 必標「未證實」並指明是哪一部分(當事人已承認、官方已公告的部分不算)" in src
