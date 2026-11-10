"""今日一圖(使用者 2026-11-10):挑偏離最多的指標,畫近 20 個交易日。"""
import datetime as dt
import html

import daily_chart as dc
import report_polish as rp


def _history(n=40, **last):
    start = dt.date(2026, 9, 1)
    rows = [{"date": (start + dt.timedelta(days=i)).isoformat(), "vix": 18 + (i % 3) * 0.5,
             "taifex_foreign_oi": -30000 - (i % 5) * 500, "usdtwd": 32.0 + (i % 4) * 0.01} for i in range(n)]
    rows[-1].update(last)
    return rows


def test_picks_the_most_unusual_series_and_ends_on_today():
    rows = _history(taifex_foreign_oi=-99000)
    chart = dc.pick(rows, {"date": rows[-1]["date"]})
    assert chart["key"] == "taifex_foreign_oi" and len(chart["points"]) == dc.BARS
    assert chart["points"][-1] == (rows[-1]["date"], -99000.0)
    page = dc.render(chart, html.escape)
    assert "今日一圖" in page and page.count("<td") == dc.BARS and page.count("#0369a1") == 1
    assert "外資台指期淨部位 -99,000 口，近 20 個交易日最低（區間 -99,000～-30,000）。" in page
    assert "height:8px" in page and "height:56px" in page   # 最低 8、最高 56


def test_today_value_comes_from_this_run_and_missing_today_is_not_drawn():
    rows = _history()[:-1]                                   # 歷史只到昨天
    today = {"date": "2026-10-11", "vix": 45.0}
    chart = dc.pick(rows, today)
    assert chart["key"] == "vix" and chart["points"][-1] == ("2026-10-11", 45.0)
    assert "近 20 個交易日最高" in dc.render(chart, html.escape)
    assert dc.pick(rows, {"date": "2026-10-11"}) == {}       # 今天沒有任何值:不拿昨天的當今天
    assert dc.pick(rows, {"date": "2026-10-11", "vix": None, "usdtwd": float("nan")}) == {}


def test_too_little_history_bad_values_and_flat_series_do_not_break():
    assert dc.pick(_history(10), {"date": "2026-09-10"}) == {} and dc.render({}, html.escape) == ""
    rows = _history()
    rows[5]["vix"], rows[6]["vix"], rows[7] = "壞值", True, "不是 dict"
    assert dc.pick(rows, {"date": rows[-1]["date"]})["key"]
    flat = [{"date": f"2026-09-{i + 1:02d}", "vix": 20.0} for i in range(30)]
    chart = dc.pick(flat, {"date": "2026-09-30"})
    assert chart["score"] == 0.0 and "height:8px" in dc.render(chart, html.escape)
    middle = dc.pick(_history(vix=18.4), {"date": _history()[-1]["date"]})
    assert "高於近 20 個交易日中的" in dc._caption(middle) or "最" in dc._caption(middle)


def test_wiring_reads_quotes_and_never_raises():
    rows = _history()[:-1]
    quotes = {"HISTORY": rows, "TAIFEX_OI": {"foreign_oi_net": -99000}, "MACRO": {"VIX": {"close": 18.5}},
              "USDTWD": 32.01, "TAIEX_PRED": {"last_close": 41661.7}}
    page = rp.daily_chart(quotes, "2026-10-11 (Sun)")
    assert "外資台指期淨部位 -99,000 口" in page
    assert rp.daily_chart({}, "2026-10-11") == "" and rp.daily_chart(None) == ""
    assert rp.daily_chart({"HISTORY": "壞資料"}, "2026-10-11") == ""
