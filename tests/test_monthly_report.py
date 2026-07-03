"""V2-D1:monthly_report.d1_readiness 就緒度判定(純字串解析,無網路)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backtest_data"))

import monthly_report as mrpt  # noqa: E402


def test_d1_readiness_ready_when_samples_enough():
    ic = (
        "=== 前瞻 20 交易日報酬(close→close)IC ===\n"
        "  因子              平均IC    t值   IC>0比率  (n_days)\n"
        "  rev_yoy_pct       +0.0500   +2.5     70%    (35)\n"
        "  op_margin         +0.0300   +2.0     60%    (32)\n"
        "判讀:\n"
    )
    r = mrpt.d1_readiness(ic)
    assert "樣本已足" in r and "rev_yoy_pct=35日" in r and "D1 因子 IC 驗收" in r


def test_d1_readiness_not_ready_when_insufficient():
    ic = (
        "=== 前瞻 20 交易日報酬(close→close)IC ===\n"
        "  rev_yoy_pct       樣本不足\n"
        "  op_margin         樣本不足\n"
        "判讀:\n"
    )
    assert "尚未就緒" in mrpt.d1_readiness(ic)


def test_d1_readiness_ignores_1day_section_only_uses_20day():
    # 前瞻 1 日有足夠樣本,但 20 日不足 → 仍判「尚未就緒」(只看 20 日 horizon)
    ic = (
        "=== 前瞻 1 交易日報酬(close→close)IC ===\n"
        "  rev_yoy_pct       +0.01   +1.2   60%    (40)\n"
        "=== 前瞻 20 交易日報酬(close→close)IC ===\n"
        "  rev_yoy_pct       樣本不足\n"
        "判讀:\n"
    )
    assert "尚未就緒" in mrpt.d1_readiness(ic)


def test_d1_readiness_no_20day_section_is_not_ready():
    # 只有 1 日段(且樣本足)但完全沒有 20 日段 → 絕不可誤判為就緒
    ic = (
        "=== 前瞻 1 交易日報酬(close→close)IC ===\n"
        "  rev_yoy_pct       +0.01   +1.2   60%    (40)\n"
        "判讀:\n"
    )
    r = mrpt.d1_readiness(ic)
    assert "尚未就緒" in r and "樣本已足" not in r
