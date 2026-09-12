"""Offline regression: numerical observations must not invent flows or motives."""
import pytest

import render_utils
import top5_readout


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True])
def test_nonfinite_inputs_do_not_crash_or_become_observations(bad):
    assert top5_readout.readout({
        "foreign_streak": bad, "invest_streak": bad, "tdcc_wow_pct": bad,
        "vol_ratio_20d": bad, "per": bad, "dividend_yield": bad,
    }) == ""


def test_zero_revenue_growth_is_not_decline():
    out = top5_readout.readout({"per": 8, "rev_yoy_pct": 0})
    assert "去年同期持平" in out
    assert "年減" not in out


@pytest.mark.parametrize("change", [-3, 0, 3, None])
def test_volume_alone_cannot_establish_cost_or_intent(change):
    for ratio in (0.5, 2):
        out = top5_readout.readout({"vol_ratio_20d": ratio, "day_pct": change})
        assert "追價成本" not in out
        assert "都在觀望" not in out


def test_unqualified_valuation_and_price_support_are_not_invented():
    out = top5_readout.readout({"per": 8, "dividend_yield": 6})
    assert "本益比 8.0 倍" in out and "殖利率 6.0%" in out
    assert "偏低" not in out and "有撐" not in out


def test_rotation_footnote_distinguishes_returns_from_flows():
    from test_prod_0904_missing_sector_news import _heat, _snap
    import morning_report

    rot = morning_report._sector_rotation(_snap())
    html = render_utils._render_sector_rotation_table(rot, _heat())
    assert "股價相對表現較強，不代表資金淨流入" in html
    assert "資金相對流入" not in html
