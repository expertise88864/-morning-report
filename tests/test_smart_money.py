"""籌碼悄悄站隊 (smart money) 偵測測試。

涵蓋：
  - _calc_inst_streaks 連續同向天數
  - calc_tdcc_wow_delta 從 history 計算 WoW Δ%
  - calc_smart_money_score 各分數來源與標籤
  - 量縮收紅 / 暴量收紅 + 法人賣 兩種典型情境
"""
import datetime as dt

import morning_report as mr


# ---------- _calc_inst_streaks ----------

def test_streak_empty():
    out = mr._calc_inst_streaks([])
    assert out == {"foreign_streak": 0, "invest_streak": 0}


def test_streak_consecutive_buys():
    """三天都正 → foreign_streak = +3。"""
    daily = [
        {"date": "20260520", "foreign": 1000, "invest": -200, "dealer": 0},
        {"date": "20260521", "foreign": 800,  "invest": 100, "dealer": 0},
        {"date": "20260522", "foreign": 500,  "invest": 200, "dealer": 0},
    ]
    out = mr._calc_inst_streaks(daily)
    assert out["foreign_streak"] == 3
    # 投信只有最後兩天 + ,但最新天 (200) 為正,從最新往回 1, 2 都正 ⇒ +2(因為第一天 -200,中斷)
    assert out["invest_streak"] == 2


def test_streak_consecutive_sells():
    daily = [
        {"date": "20260520", "foreign": -300, "invest": 0, "dealer": 0},
        {"date": "20260521", "foreign": -500, "invest": 0, "dealer": 0},
        {"date": "20260522", "foreign": -800, "invest": 0, "dealer": 0},
    ]
    out = mr._calc_inst_streaks(daily)
    assert out["foreign_streak"] == -3


def test_streak_latest_zero_returns_zero():
    """最新一天為 0(可能停牌或無資料)→ streak = 0 不算方向。"""
    daily = [
        {"date": "20260520", "foreign": 1000, "invest": 0, "dealer": 0},
        {"date": "20260521", "foreign": 500,  "invest": 0, "dealer": 0},
        {"date": "20260522", "foreign": 0,    "invest": 0, "dealer": 0},
    ]
    assert mr._calc_inst_streaks(daily)["foreign_streak"] == 0


def test_streak_breaks_on_reversal():
    """最新買、前一天賣 → streak 只到最新那天 = +1。"""
    daily = [
        {"date": "20260520", "foreign": -500, "invest": 0, "dealer": 0},
        {"date": "20260521", "foreign": -500, "invest": 0, "dealer": 0},
        {"date": "20260522", "foreign": +800, "invest": 0, "dealer": 0},
    ]
    assert mr._calc_inst_streaks(daily)["foreign_streak"] == 1


# ---------- calc_tdcc_wow_delta ----------

def test_tdcc_wow_no_history():
    out = mr.calc_tdcc_wow_delta({"2330": {"major_holder_pct": 74.5}}, [])
    assert out == {}


def test_tdcc_wow_history_too_recent():
    """歷史 < min_gap_days → 不取(避免拿到同一週)。"""
    today = dt.datetime.now(mr.TPE).date()
    history = [{
        "date": (today - dt.timedelta(days=2)).strftime("%Y-%m-%d"),
        "tdcc_snapshot": {"2330": 73.5},
    }]
    out = mr.calc_tdcc_wow_delta({"2330": {"major_holder_pct": 74.0}},
                                    history, min_gap_days=5)
    assert out == {}


def test_tdcc_wow_computes_delta():
    today = dt.datetime.now(mr.TPE).date()
    history = [{
        "date": (today - dt.timedelta(days=7)).strftime("%Y-%m-%d"),
        "tdcc_snapshot": {"2330": 73.50, "2317": 60.00},
    }]
    cur = {"2330": {"major_holder_pct": 74.20},
           "2317": {"major_holder_pct": 59.80}}
    out = mr.calc_tdcc_wow_delta(cur, history, min_gap_days=5)
    assert out["2330"] == 0.70
    assert out["2317"] == -0.20


def test_tdcc_wow_picks_earliest_eligible():
    """有多筆歷史時,優先用最舊但 >= min_gap_days 的那筆(在 reversed 中第一個)。"""
    today = dt.datetime.now(mr.TPE).date()
    history = [
        {"date": (today - dt.timedelta(days=14)).strftime("%Y-%m-%d"),
         "tdcc_snapshot": {"2330": 72.0}},
        {"date": (today - dt.timedelta(days=7)).strftime("%Y-%m-%d"),
         "tdcc_snapshot": {"2330": 73.5}},
    ]
    out = mr.calc_tdcc_wow_delta({"2330": {"major_holder_pct": 74.0}},
                                    history, min_gap_days=5)
    # reversed 從最新到最舊,找到第一個距今 ≥ 5 天的 = 7 天前
    assert out["2330"] == 0.5


# ---------- calc_smart_money_score ----------

def test_score_quietly_accumulating():
    """理想偷買案例:外資連 3 天買 + 投信跟風 + 大戶 + 量縮 + 5日漲幅在偷買區。"""
    entry = {
        "foreign_streak": 3,
        "invest_streak": 2,
        "tdcc_wow_pct": 0.5,
        "vol_ratio_20d": 0.7,
        "day_pct": 0.4,
        "pct_5d": 1.5,
        "foreign_lot": 2000,
        "invest_lot": 500,
        "high20_break": False,
        "low20_break": False,
        "margin_change_lot": -300,
    }
    out = mr.calc_smart_money_score(entry)
    # 30(連3) + 10(投信跟) + 15(大戶0.5) + 20(量縮收紅) + 10(偷買區) + 5(融資減)= 90
    assert out["score"] >= 80
    assert out["tag"] == "強力偷買訊號"
    assert any("外資連3買" in t for t in out["tags"])
    assert any("投信連2買" in t for t in out["tags"])
    assert "量縮收紅" in out["tags"]


def test_score_breakout_case():
    """突破 20 日高 + 放量 + 法人買:給「突破」標籤,分數中等。"""
    entry = {
        "foreign_streak": 2,
        "invest_streak": 1,
        "tdcc_wow_pct": None,
        "vol_ratio_20d": 2.0,    # 放量
        "day_pct": 3.5,
        "pct_5d": 4.0,
        "foreign_lot": 1500,
        "invest_lot": 200,
        "high20_break": True,
        "low20_break": False,
        "margin_change_lot": None,
    }
    out = mr.calc_smart_money_score(entry)
    assert any("突破" in t for t in out["tags"])
    # 連 2 = 18 + 量放突破 = 8 + 偷買區 6 ≈ 32,落在「輕微正向」或「中性」
    assert 25 <= out["score"] <= 55


def test_score_retail_buying_high_warning():
    """暴量 + 收紅 + 法人賣 = 散戶接刀警示,分數扣分。"""
    entry = {
        "foreign_streak": -2,
        "invest_streak": 0,
        "tdcc_wow_pct": None,
        "vol_ratio_20d": 2.5,
        "day_pct": 2.0,
        "pct_5d": 12.0,
        "foreign_lot": -1000,
        "invest_lot": 0,
        "high20_break": False,
        "low20_break": False,
        "margin_change_lot": None,
    }
    out = mr.calc_smart_money_score(entry)
    # 連賣 2 不到 -25 門檻;量暴 + 法人賣 = -15;5日 12% = -8 → 負分被夾到 0
    assert out["score"] == 0


def test_score_foreign_selling_streak_warns():
    entry = {
        "foreign_streak": -4,
        "invest_streak": 0,
        "tdcc_wow_pct": -0.5,
        "vol_ratio_20d": 1.0,
        "day_pct": -2.0,
        "pct_5d": -3.0,
        "foreign_lot": -5000,
        "invest_lot": 0,
        "high20_break": False,
        "low20_break": False,
        "margin_change_lot": None,
    }
    out = mr.calc_smart_money_score(entry)
    # f_streak <= -3 → -25;tdcc -0.5 → -10 → 全負被夾 0
    assert out["score"] == 0
    assert out["tag"] == "籌碼鬆動警示"


def test_score_handles_empty_entry():
    assert mr.calc_smart_money_score({})["score"] == 0
    assert mr.calc_smart_money_score(None)["score"] == 0


def test_score_components_breakdown():
    """應回傳 components 細項供除錯。"""
    entry = {
        "foreign_streak": 3,
        "invest_streak": 0,
        "tdcc_wow_pct": 1.0,
        "vol_ratio_20d": 0.6,
        "day_pct": 0.5,
        "pct_5d": 2.0,
        "foreign_lot": 1000,
        "invest_lot": 0,
    }
    out = mr.calc_smart_money_score(entry)
    c = out["components"]
    assert c["foreign_streak_score"] == 30.0
    assert c["tdcc_wow_score"] == 30.0     # 1.0% → 30 分
    assert c["volume_score"] == 20.0        # 量縮收紅
    assert c["quiet_score"] == 10.0         # 5日 2% 在偷買區
