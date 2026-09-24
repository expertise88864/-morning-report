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


def test_weekends_and_chinese_public_holidays_are_offpeak_all_day():
    # Beijing 10:00 falls inside the usual weekday peak window.
    def _at(month, day):
        return (_dt.datetime(2026, month, day, 10, 0,
                             tzinfo=_dt.timezone.utc)
                - _dt.timedelta(hours=8))

    assert lp.deepseek_window(_at(9, 20)) == "offpeak"  # Sunday make-up workday
    assert lp.deepseek_window(_at(9, 25)) == "offpeak"  # Mid-Autumn Friday
    assert lp.deepseek_window(_at(10, 5)) == "offpeak"  # National Day Monday
    assert lp.deepseek_window(_at(9, 24)) == "peak"     # Ordinary Thursday
    assert lp.price_of("deepseek-flash", _at(9, 25))["output"] == 0.60


def test_unverified_future_holidays_do_not_silently_use_peak_or_old_rates():
    at = _dt.datetime(2027, 1, 1, 2, 0, tzinfo=_dt.timezone.utc)
    assert lp.price_of("deepseek-flash", at) is None
    assert lp.estimate_cost("deepseek-flash", {
        "prompt_tokens": 1000, "completion_tokens": 1000}, at=at)["usd"] is None
    assert lp.price_of("deepseek-flash", at + _dt.timedelta(days=1)) == {
        "input": 0.15, "cached_input": 0.003, "output": 0.60}
    assert lp.price_of("deepseek-flash", at - _dt.timedelta(hours=4)) == {
        "input": 0.15, "cached_input": 0.003, "output": 0.60}


def test_recorded_historical_cost_does_not_depend_on_todays_calendar(monkeypatch):
    import llm_telemetry as lt

    monkeypatch.setattr(lt, "price_of", lambda *_args: (_ for _ in ()).throw(
        AssertionError("a recorded cost must not be repriced")))
    rec = {"model": "deepseek-flash", "prompt_tokens": 1000,
           "completion_tokens": 500, "estimated_cost_usd": 0.001}
    summary = lt.run_cost_summary({"primary": rec})
    assert summary["total_usd"] == 0.001
    assert "incomplete" not in summary
    unpriced = lt.run_cost_summary({"primary": dict(rec, estimated_cost_usd=None)})
    assert "有用量但成本未估" in unpriced["incomplete"]


def test_rates_switch_only_after_the_effective_moment():
    """生效前用舊的單一費率表 —— 提前套新價會**高估**帳單。"""
    assert lp.price_of("deepseek-v4-pro", _BEFORE)["output"] == 0.87
    assert lp.price_of("deepseek-v4-pro", _OFFPEAK)["output"] == 1.98
    assert lp.price_of("deepseek-v4-pro", _PEAK)["output"] == 3.96


def test_v41_flash_rates_begin_at_the_documented_september_10_instant():
    before = _dt.datetime(2026, 9, 10, 3, 59, tzinfo=_dt.timezone.utc)
    after = _dt.datetime(2026, 9, 10, 4, 0, tzinfo=_dt.timezone.utc)
    assert lp.price_of("deepseek-v4-flash", before) == {
        "input": 0.44, "cached_input": 0.014, "output": 1.32}
    assert lp.price_of("deepseek-v4-flash", after) == {
        "input": 0.15, "cached_input": 0.003, "output": 0.60}
    assert lp.price_of("deepseek-flash", after) == lp.price_of(
        "deepseek-v4-flash", after)
    assert lp.price_of("deepseek-v4-pro", after) == {
        "input": 0.66, "cached_input": 0.022, "output": 1.98}


def test_retired_flash_alias_uses_v41_cost_for_a_normal_morning():
    at = _dt.datetime(2026, 9, 23, 23, 54, tzinfo=_dt.timezone.utc)
    usage = {"prompt_tokens": 100_000, "completion_tokens": 20_000,
             "prompt_tokens_details": {"cached_tokens": 10_000}}
    result = lp.estimate_cost("deepseek-v4-flash", usage, at=at)
    assert result["usd"] == 0.02553
    assert result["effective_input_rate"] == 0.15
    assert result["effective_cached_rate"] == 0.003
    assert result["effective_output_rate"] == 0.60
    assert result["pricing_schema"] == 6


def test_current_flash_alias_is_not_falsely_unpriced_in_run_summary(monkeypatch):
    import llm_telemetry as lt

    at = _dt.datetime(2026, 9, 23, 23, 54, tzinfo=_dt.timezone.utc)
    real_estimate = lt.estimate_cost
    monkeypatch.setattr(lt, "estimate_cost",
                        lambda model, usage: real_estimate(model, usage, at=at))
    rec = lt.build_record(
        "deepseek", "deepseek-flash",
        usage={"prompt_tokens": 100_000, "completion_tokens": 20_000})
    summary = lt.run_cost_summary({"primary": rec})
    assert rec["estimated_cost_usd"] > 0
    assert summary["total_usd"] == rec["estimated_cost_usd"]
    assert "incomplete" not in summary


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
