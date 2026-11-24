"""11-24 實信:日股休市的舊漲跌沒標日期;類股占比的分母不含 ETF 卻寫「占全市場」。"""
import datetime as dt

import pandas as pd

import market_fresh as mf
import morning_report as mr
import sector_readout as sr


def test_holiday_close_shows_its_date():
    macro = {"N225": {"observed_on": "2026-11-20"}, "KOSPI": {"observed_on": "2026-11-23"}}
    day = dt.date(2026, 11, 24)
    assert mf.asia_label("N225", "日經 225", macro, day) == "日經 225（11/20 收盤）"
    assert mf.asia_label("KOSPI", "韓國 KOSPI", macro, day) == "韓國 KOSPI"
    assert mf.asia_label("SSE", "上證綜指", macro, day) == "上證綜指"
    assert mf.asia_label("10Y", "", {"10Y": {"observed_on": "2026-11-20"}}, day) == ""
    for weekend in (dt.date(2026, 11, 21), dt.date(2026, 11, 22), dt.date(2026, 11, 23)):   # 週末、週一看週五
        assert mf.asia_label("N225", "日經 225", macro, weekend) == "日經 225", weekend


def test_latest_close_records_the_session_it_used():
    class Ticker:
        history_metadata = {"regularMarketPrice": 101.0, "exchangeTimezoneName": "Asia/Seoul",
                            "regularMarketTime": int(pd.Timestamp("2026-11-23 15:00", tz="Asia/Seoul").timestamp())}
    bars = pd.DataFrame({"Close": [99.0, 100.0]}, index=pd.DatetimeIndex(["2026-11-19", "2026-11-20"], tz="Asia/Seoul"))
    mf.latest_close("KOSPI", Ticker(), bars, 100.0, 99.0)
    assert mf.traded_on("KOSPI") == {"observed_on": "2026-11-23"}
    Ticker.history_metadata = {}
    mf.latest_close("KOSPI", Ticker(), bars, 100.0, 99.0)
    assert mf.traded_on("KOSPI") == {"observed_on": "2026-11-20"} and mf.traded_on("VIX") == {}


def test_sector_share_names_its_denominator():
    heat = {"sectors": {"半導體業": {"value_yi": 4200, "value_share_pct": 44.0, "median_pct": 1.0, "leaders": []}},
            "ranked": ["半導體業"], "total_value_yi": 9545}
    block = mr._format_sector_heat_block(heat)
    assert "上市個股成交約 9,545 億" in block and "佔上市個股成交" in block and "全市場成交約" not in block
    assert "佔上市個股成交44%" in sr.readout(heat)
