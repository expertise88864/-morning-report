"""backtest_runner Section D 純函式單測(不需 Yahoo;鎖定 reserve 引擎的公平性與『階梯確實觸發』)。"""
import backtest_runner as br


def _months(n):
    """造 n 個『每月首交易日』日期字串(每月一筆,確保 d[:7] 唯一)。"""
    out = []
    for k in range(n):
        y, m = 2020 + k // 12, k % 12 + 1
        out.append(f"{y}-{m:02d}-01")
    return out


def test_ma_warmup_and_value():
    assert br._ma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert br._ma([5], 3) == [None]


def test_rolling_high():
    assert br._rolling_high([3, 1, 4, 1, 5], 2) == [3, 3, 4, 4, 5]
    assert br._rolling_high([2, 2, 2], 5) == [2, 2, 2]   # 窗大於長度仍可


def test_rsi_wilder_warmup_and_extremes():
    rsi = br._rsi_wilder([10, 11, 12, 13, 14], 2)
    assert rsi[0] is None and rsi[1] is None      # 前 win 日無值
    assert rsi[2] == 100.0                          # 全漲 → RSI=100
    down = br._rsi_wilder([14, 13, 12, 11, 10], 2)
    assert down[2] == 0.0                            # 全跌 → RSI=0


def test_buy_min_fee_floor():
    # 小額:手續費被 20 元地板吃掉(比例費 <20)
    assert abs(br._buy(10000.0, 100.0) - (10000 - 20) / 100) < 1e-9
    # 大額:比例費 > 20,用比例
    fee = 1_000_000 * br.FEE_RATE
    assert fee > br.MIN_FEE
    assert abs(br._buy(1_000_000.0, 100.0) - (1_000_000 - fee) / 100) < 1e-6
    assert br._buy(0.0, 100.0) == 0.0


def test_deploy_reserve_flat_total_in():
    days = _months(12)
    px = [100.0] * len(days)
    tin, sh, force, mo = br._deploy_reserve(days, px, lambda i: 1.0, cap_months=0)
    assert mo == 12
    assert abs(tin - 12 * br.CONTRIB) < 1e-9         # total_in = 月數×供款
    assert force == 0                                 # cap=0、每月投滿 → 無溢出
    assert sh > 0


def test_deploy_reserve_fairness_equal_total_in():
    """公平性核心:加碼策略與 flat 的 total_in 必須完全相等(差異只在時點)。"""
    days = _months(15)
    px = [100.0] * 5 + [50.0, 50.0] + [100.0] * 8

    def dip(i):                                       # 平靜 0.7x 存彈藥,前一日低於 60 → 3x
        return 3.0 if (i > 0 and px[i - 1] <= 60) else 0.7

    tin_flat, sh_flat, _, _ = br._deploy_reserve(days, px, lambda i: 1.0, cap_months=0)
    tin_dip, sh_dip, _, _ = br._deploy_reserve(days, px, dip, cap_months=6)
    assert abs(tin_flat - tin_dip) < 1e-6            # 同額,可直接比 money-weighted


def test_deploy_reserve_ladder_actually_fires():
    """回歸:平靜月 <1x 存彈藥、逢跌月 >1x 放出 → 階梯確實觸發(非死碼),
    在有持續回檔的序列下買到更多便宜股、均價低於 flat。"""
    days = _months(15)
    px = [100.0] * 5 + [50.0, 50.0] + [100.0] * 8     # 第 5–6 月持續腰斬

    def dip(i):
        return 3.0 if (i > 0 and px[i - 1] <= 60) else 0.7

    _, sh_flat, _, _ = br._deploy_reserve(days, px, lambda i: 1.0, cap_months=0)
    _, sh_dip, force, _ = br._deploy_reserve(days, px, dip, cap_months=6)
    assert sh_dip > sh_flat                           # 逢低加碼買到更多股 → 均價更低
    # 若這裡 sh_dip == sh_flat,代表 reserve 從未累積(舊版死碼 bug 重現)


def test_months_between():
    assert br._months_between("2020-01-15", "2020-01-20") == 1   # 同月至少 1
    assert br._months_between("2020-01-01", "2020-04-01") == 3
    assert br._months_between("2020-12-01", "2021-02-01") == 2
