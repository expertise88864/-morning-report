# -*- coding: utf-8 -*-
"""單價與成本估算。"""


# ------------------------------------------- DeepSeek 峰谷計價(2026-08-17)

import datetime as _dt

import llm_pricing as lp

_OFFPEAK = _dt.datetime(2026, 8, 17, 22, 0, tzinfo=_dt.timezone.utc)  # 北京 06:00
_PEAK = _dt.datetime(2026, 8, 18, 2, 0, tzinfo=_dt.timezone.utc)      # 北京 10:00
_BEFORE = _dt.datetime(2026, 8, 16, 15, 0, tzinfo=_dt.timezone.utc)   # 生效前一小時


def test_the_peak_window_follows_the_official_beijing_hours():
    """官方:尖峰 = 北京 09:00-12:00 與 14:00-18:00。"""
    def _at(beijing_hour):
        return (_dt.datetime(2026, 8, 20, beijing_hour, 0,
                             tzinfo=_dt.timezone.utc)
                - _dt.timedelta(hours=8))
    for h in (9, 11, 14, 17):
        assert lp.deepseek_window(_at(h)) == "peak", h
    for h in (0, 6, 8, 12, 13, 18, 23):
        assert lp.deepseek_window(_at(h)) == "offpeak", h


def test_rates_switch_only_after_the_effective_moment():
    """生效前用舊的單一費率表 —— 提前套新價會**高估**帳單。"""
    assert lp.price_of("deepseek-v4-pro", _BEFORE)["output"] == 0.87
    assert lp.price_of("deepseek-v4-pro", _OFFPEAK)["output"] == 1.98
    assert lp.price_of("deepseek-v4-pro", _PEAK)["output"] == 3.96


def test_the_offpeak_price_is_higher_than_today_not_lower():
    """**這不是「離峰打折」** —— 離峰價本身就比現價貴,把它讀成
    「調價後有便宜時段」會低估帳單。"""
    old = lp.MODEL_PRICING["deepseek-v4-pro"]
    new_off = lp.price_of("deepseek-v4-pro", _OFFPEAK)
    assert new_off["input"] > old["input"]
    assert new_off["output"] > old["output"]
    assert new_off["cached_input"] > old["cached_input"]


def test_the_estimate_records_which_window_it_used():
    """同一個模型同一天可以有兩種單價 —— 只記總額的話,對不上帳單時
    分不出是「跑在尖峰」還是「漏算呼叫」。"""
    u = {"prompt_tokens": 100_000, "completion_tokens": 10_000}
    off = lp.estimate_cost("deepseek-v4-pro", u, at=_OFFPEAK)
    peak = lp.estimate_cost("deepseek-v4-pro", u, at=_PEAK)
    assert off["pricing_tier"] == "deepseek_offpeak", off
    assert peak["pricing_tier"] == "deepseek_peak", peak
    assert peak["usd"] > off["usd"] * 1.9, (off["usd"], peak["usd"])
    assert "峰谷" in off["basis"]


def test_a_non_deepseek_model_is_untouched_by_the_windows():
    """峰谷只屬於 DeepSeek —— 別讓它污染別家的費率。"""
    for t in (_OFFPEAK, _PEAK):
        assert lp.price_of("gpt-5.6-luna", t) ==             lp.MODEL_PRICING["gpt-5.6-luna"]


def test_the_schema_version_moved_with_the_pricing_model():
    """舊 schema 的成本資料不可與新的相加 —— 版本要跟著動。"""
    assert lp.PRICING_SCHEMA >= 5


def test_a_mixed_window_total_is_not_labelled_as_one_window():
    """**同一角色的兩次呼叫可以跨過峰谷邊界**(外審 r1,P2):其餘欄位
    一律「取最新」,於是尖峰+離峰的合計會整個被標成其中一種,對帳時
    分不出來。混合要明說 mixed,並逐時段留下金額。"""
    import llm_telemetry as lt
    off = {"pricing_tier": "deepseek_offpeak", "estimated_cost_usd": 0.10,
           "calls": 1, "prompt_tokens": 10}
    peak = {"pricing_tier": "deepseek_peak", "estimated_cost_usd": 0.20,
            "prompt_tokens": 20}
    m = lt.merge_same_role(off, peak)
    assert m["pricing_tier"] == "mixed", m
    assert m["cost_by_tier"] == {"deepseek_offpeak": 0.10,
                                 "deepseek_peak": 0.20}, m
    assert m["estimated_cost_usd"] == 0.30
    # 第三次(又是尖峰)累進同一格,而且不會退回單一標籤
    m2 = lt.merge_same_role(m, {"pricing_tier": "deepseek_peak",
                                "estimated_cost_usd": 0.05,
                                "prompt_tokens": 5})
    assert m2["pricing_tier"] == "mixed", m2
    assert m2["cost_by_tier"]["deepseek_peak"] == 0.25, m2


def test_a_single_window_total_keeps_its_own_label():
    """同時段的合併不得被誤標成 mixed —— 那會讓「真的跨時段」失去意義。"""
    import llm_telemetry as lt
    m = lt.merge_same_role(
        {"pricing_tier": "standard", "estimated_cost_usd": 0.1, "calls": 1},
        {"pricing_tier": "standard", "estimated_cost_usd": 0.2})
    assert m["pricing_tier"] == "standard", m
    assert m["cost_by_tier"] == {"standard": 0.3}, m
