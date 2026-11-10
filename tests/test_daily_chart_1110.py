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


def test_review_pr51_same_session_once_stale_fx_out_and_pcr_in():
    # 週六與週一都指向週一:同一個交易日只算一筆,今天那筆以本班為準
    rows = _history()[:-1]
    rows.append({"date": "2026-10-10", "target_session_date": "2026-10-12", "vix": 30.0})   # 週六那封
    today = {"date": "2026-10-12", "target_session_date": "2026-10-12", "vix": 31.0}
    points = dc._points(rows, "vix", today)
    assert points[-1] == ("2026-10-12", 31.0) and [d for d, _ in points].count("2026-10-12") == 1
    assert all(v != 30.0 for _, v in points)
    # 補的舊匯率不是當天的觀測:今天與歷史都不算
    fx = _history()[:-1]
    fx[-3]["usdtwd"], fx[-3]["usdtwd_stale"] = 40.0, True
    assert all(v != 40.0 for _, v in dc._points(fx, "usdtwd", {"date": "2026-10-11", "usdtwd": 32.0}))
    assert dc._points(fx, "usdtwd", {"date": "2026-10-11", "usdtwd": 40.0, "usdtwd_stale": True})[-1][0] != "2026-10-11"
    quotes = {"HISTORY": fx, "USDTWD": 40.0, "USDTWD_STALE": {"value": 40.0}, "MACRO": {"VIX": {"close": 18.5}}}
    assert "美元兌新台幣" not in rp.daily_chart(quotes, "2026-10-11")
    # 選擇權 P/C:資料日期等於上一交易日才是今天的值
    pc = [dict(r, txo_pc_oi_ratio=60 + (i % 3)) for i, r in enumerate(_history()[:-1])]
    q = {"HISTORY": pc, "TAIFEX_PCR": {"date": "20261009", "pc_oi_ratio": 140.0}, "LAST_TRADING_SESSION": "2026-10-09"}
    assert "台指選擇權賣權/買權未平倉比 140.00%" in rp.daily_chart(q, "2026-10-11")
    q["TAIFEX_PCR"]["date"] = "20261008"
    assert rp.daily_chart(q, "2026-10-11") == ""
