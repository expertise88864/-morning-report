"""Offline regression for the market observation date visible to readers."""
from __future__ import annotations

import pandas as pd

import morning_report as mr
import prompt_profiles as profiles
import writing_rules as writing


def test_yahoo_daily_index_keeps_us_market_date_not_taipei_date(monkeypatch):
    bars = pd.DataFrame(
        {"Close": [4.91, 4.968]},
        index=pd.DatetimeIndex([
            "2026-09-21 16:00:00-04:00",
            "2026-09-22 16:00:00-04:00",
        ]),
    )

    class Ticker:
        def history(self, **kwargs):
            assert kwargs == {"period": "1y", "auto_adjust": False}
            return bars

    monkeypatch.setattr(mr.yf, "Ticker", lambda symbol: Ticker())
    ten_year = mr.fetch_macro_indicators()["10Y"]
    assert ten_year["close"] == 4.968
    assert ten_year["source"] == "Yahoo Finance"
    assert ten_year["observed_on"] == "2026-09-22"
    assert "Yahoo Finance" in mr._format_macro_line("10Y", ten_year)
    assert "2026-09-22 資料日" in mr._format_macro_line("10Y", ten_year)


def test_yield_curve_uses_a_shared_date_only_when_all_leg_dates_agree():
    aligned = {
        "13W": {"close": 3.85, "source": "Yahoo Finance", "observed_on": "2026-09-22"},
        "10Y": {"close": 4.968, "source": "Yahoo Finance", "observed_on": "2026-09-22"},
    }
    detail = mr._yield_curve_read(aligned)["detail"]
    assert "Yahoo Finance" in detail
    assert "2026-09-22 資料日" in detail
    assert detail.count("2026-09-22") == 1

    aligned["13W"]["observed_on"] = "2026-09-21"
    mismatch = mr._yield_curve_read(aligned)
    detail = mismatch["detail"]
    assert "短期 2026-09-21" in detail
    assert "10 年 2026-09-22" in detail
    assert mismatch["flag"] == "caution"
    assert "暫不判讀" in detail and "長端利率高於短端" not in detail

    aligned["13W"]["observed_on"] = "2026-09-22"
    aligned["30Y"] = {"close": 4.37, "source": "Yahoo Finance", "observed_on": "2026-09-18"}
    assert "暫不判讀" in mr._yield_curve_read(aligned)["detail"]


def test_prompt_omits_cross_date_yield_spread():
    from test_data_validation import _empty_quotes

    macro = {
        "13W": {"close": 5.0, "source": "Yahoo Finance", "observed_on": "2026-09-18"},
        "10Y": {"close": 4.0, "source": "Yahoo Finance", "observed_on": "2026-09-22"},
    }
    def prompt() -> str:
        return mr._build_prompt(_empty_quotes(MACRO=macro), {"error": "x"},
                                {"error": "x"}, [], [], "")

    assert "10Y−13W 利差" not in prompt()
    assert "暫不判讀" in prompt()
    macro["13W"]["observed_on"] = "2026-09-22"
    assert "10Y−13W 利差" in prompt()


def test_missing_index_date_is_visible_as_unknown_not_fabricated(monkeypatch):
    bars = pd.DataFrame({"Close": [4.91, 4.968]})

    class Ticker:
        def history(self, **kwargs):
            return bars

    monkeypatch.setattr(mr.yf, "Ticker", lambda symbol: Ticker())
    macro = mr.fetch_macro_indicators()
    assert macro["10Y"]["observed_on"] is None
    assert "資料日未確認" in mr._format_macro_line("10Y", macro["10Y"])
    assert "資料日未確認" in mr._yield_curve_read(macro)["detail"]
    assert "暫不判讀" in mr._yield_curve_read(macro)["detail"]


def test_older_macro_fixture_without_provenance_keeps_previous_shape():
    older = {"13W": {"close": 3.85}, "10Y": {"close": 4.968}}
    assert "Yahoo Finance" not in mr._yield_curve_read(older)["detail"]
    assert "資料日" not in mr._format_macro_line("10Y", older["10Y"])


def test_both_analysis_profiles_distinguish_market_date_from_report_date():
    rule = "無資料日不得稱「今日收盤」"
    assert rule in profiles.LUNA_DEVELOPER_INSTRUCTIONS
    assert rule in writing.LEGACY_RULES


def test_source_date_survives_the_final_reader_html_boundary():
    from test_render_smoke import _fixture

    quotes, fair, predictions, analysis = _fixture()
    for tenor, value in (("13W", 3.85), ("10Y", 4.968), ("30Y", 4.37)):
        quotes["MACRO"][tenor] = {
            "close": value,
            "source": "Yahoo Finance",
            "observed_on": "2026-09-22",
        }
    html = mr.render_html(quotes, fair, predictions, analysis, "2026-09-23", "daily")
    assert "Yahoo Finance" in html
    assert "2026-09-22 資料日" in html
