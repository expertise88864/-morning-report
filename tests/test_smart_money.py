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
    assert out["raw_score"] <= -20
    assert out["tag"] == "籌碼鬆動警示"


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


# ---------- calc_breakout_score（短線爆發力複合分數）----------

def test_breakout_score_strong_multi_factor():
    """籌碼+動能+營收+EPS 全強 → 高分。"""
    e = {"smart_money": {"score": 80}, "pct_5d": 16.0, "ma20_dist_pct": 8.0,
         "high20_break": True, "rev_yoy_pct": 112.0, "rev_mom_pct": 5.0, "eps": 3.5}
    out = mr.calc_breakout_score(e)
    assert out["score"] >= 65
    c = out["components"]
    assert c["chips"] == 28.0          # 80 × 0.35
    assert c["momentum"] > 0 and c["revenue"] > 0 and c["eps"] > 0


def test_breakout_score_momentum_priority_no_overheat_penalty():
    """動能優先:5日漲幅越大分數越高(不懲罰過熱)。"""
    base = {"smart_money": {"score": 50}, "ma20_dist_pct": 5.0, "high20_break": True,
            "rev_yoy_pct": 20.0, "rev_mom_pct": 3.0, "eps": 2.0}
    low = dict(base, pct_5d=3.0)
    hot = dict(base, pct_5d=16.0)      # 已大漲
    assert mr.calc_breakout_score(hot)["score"] >= mr.calc_breakout_score(low)["score"]


def test_breakout_score_weak_stock_low():
    e = {"smart_money": {"score": 15}, "pct_5d": 0.5, "ma20_dist_pct": 0.0,
         "high20_break": False, "rev_yoy_pct": 5.0, "rev_mom_pct": 1.0, "eps": None}
    assert mr.calc_breakout_score(e)["score"] < 20


def test_breakout_score_empty():
    assert mr.calc_breakout_score({})["score"] == 0


def test_breakout_score_handles_missing_fundamentals():
    """缺營收/EPS → 該因子 0,不崩。"""
    e = {"smart_money": {"score": 60}, "pct_5d": 8.0, "ma20_dist_pct": 4.0,
         "high20_break": True, "rev_yoy_pct": None, "rev_mom_pct": None, "eps": None}
    out = mr.calc_breakout_score(e)
    assert out["components"]["revenue"] == 0.0 and out["components"]["eps"] == 0.0
    assert out["score"] > 0   # 籌碼+動能仍給分


def test_breakout_score_prefers_cross_sectional_eps_percentile():
    base = {"smart_money": {"score": 0}, "eps": 20.0}
    assert mr.calc_breakout_score(dict(base, eps_percentile=10))["components"]["eps"] == 1.0
    assert mr.calc_breakout_score(dict(base, eps_percentile=90))["components"]["eps"] == 9.0


def test_breakout_tracking_reports_forward_snapshot_returns():
    history = [{
        "date": "2026-05-29",
        "target_session_date": "2026-05-29",
        "breakout_candidates": [{"code": "2330", "name": "台積電", "score": 90, "close": 1000.0}],
    }]
    out = mr.build_breakout_tracking(
        history,
        [{"code": "2330", "name": "台積電", "close": 1050.0}],
        "2026-06-03",
    )
    assert "3 日候選" in out
    assert "平均 +5.00%" in out


def test_foreign_top10_total_requires_market_cap():
    valid = [
        {"code": str(i), "market_cap": 100 - i, "foreign_lot": i}
        for i in range(10)
    ]
    assert mr._foreign_top10_total(valid) == 45
    assert mr._foreign_top10_total([dict(valid[0], market_cap=None)] + valid[1:]) is None


def test_stock_news_catalyst_direct_outweighs_supply_chain():
    snapshot = [
        {"code": "2330", "name": "台積電"},
        {"code": "2382", "name": "廣達"},
    ]
    news = [{
        "source": "CNBC Tech",
        "source_grade": "B",
        "company_label": "NVDA",
        "title": "NVIDIA raises outlook as AI orders hit record",
        "summary": "",
    }, {
        "source": "中央社財經",
        "source_grade": "B",
        "company_label": "2330",
        "title": "台積電上修展望 訂單增加",
        "summary": "",
    }]
    out = mr._stock_news_catalysts(snapshot, news, [])
    assert out["2330"]["score"] > out["2382"]["score"] > 0


def test_stock_news_catalyst_does_not_double_count_direct_supply_chain():
    out = mr._stock_news_catalysts(
        [{"code": "2330", "name": "台積電"}],
        [{
            "source": "CNBC Tech", "source_grade": "B", "company_label": "NVDA",
            "title": "NVIDIA orders increase at 台積電", "summary": "",
        }],
        [],
    )
    assert out["2330"]["score"] == 1.25
    assert len(out["2330"]["evidence"]) == 1


def test_stock_news_catalyst_negative_mops_reduces_score():
    out = mr._stock_news_catalysts(
        [{"code": "2330", "name": "台積電"}],
        [],
        [{"code": "2330", "title": "台積電公告下修展望"}],
    )
    assert out["2330"]["score"] < 0
    assert out["2330"]["evidence"][0]["relation"] == "direct"


def test_stock_price_forecast_uses_learned_bias():
    entry = {
        "close": 100.0, "daily_vol_pct": 2.0, "pct_5d": 0.0,
        "attention_score": 50, "news_catalyst_score": 0,
    }
    raw = mr.calc_stock_price_forecast(entry)
    calibrated = mr.calc_stock_price_forecast(
        entry, {3: {"forecast_samples": 5, "forecast_bias_pct": 1.0}})
    assert calibrated["3d"]["expected_price"] > raw["3d"]["expected_price"]
    assert calibrated["3d"]["lower"] < calibrated["3d"]["upper"]


def test_breakout_tracking_reports_forecast_mae():
    history = [{
        "date": "2026-05-29",
        "target_session_date": "2026-05-29",
        "breakout_candidates": [{
            "code": "2330", "close": 100.0,
            "price_forecast": {"3d": {"expected_price": 104.0}},
        }],
    }]
    out = mr.build_breakout_tracking(
        history, [{"code": "2330", "close": 105.0}], "2026-06-03")
    assert "預測 MAE 1.00%" in out
    assert "方向命中 100%" in out


def test_rank_attention_candidates_filters_weak_revenue_without_catalyst():
    ranked = mr._rank_attention_candidates([
        {"code": "A", "attention_score": 80, "rev_yoy_pct": -20, "news_catalyst_score": 0},
        {"code": "B", "attention_score": 70, "rev_yoy_pct": -20, "news_catalyst_score": 2},
        {"code": "C", "attention_score": 60, "rev_yoy_pct": 5, "news_catalyst_score": 0},
    ])
    assert [item["code"] for item in ranked] == ["B", "C"]


# ---------- fetch_tw_eps（best-effort）----------

def test_fetch_tw_eps_parses(monkeypatch):
    class _R:
        def __init__(self, payload): self._p = payload
        def raise_for_status(self): pass
        def json(self): return self._p
    payload = [
        {"公司代號": "2330", "基本每股盈餘（元）": "12.5", "年度": "115", "季別": "1"},
        {"公司代號": "2317", "基本每股盈餘（元）": "3.2", "年度": "115", "季別": "1"},
        {"公司代號": "00878", "基本每股盈餘（元）": "0.0"},  # 5 位代號略過
    ]
    # 第一個端點回資料,其餘回空
    calls = {"n": 0}
    def fake_get(url, **kw):
        calls["n"] += 1
        return _R(payload if calls["n"] == 1 else [])
    monkeypatch.setattr(mr.requests, "get", fake_get)
    out = mr.fetch_tw_eps()
    assert out["2330"]["eps"] == 12.5
    assert out["2317"]["eps"] == 3.2
    assert "00878" not in out


def test_fetch_tw_eps_all_fail(monkeypatch):
    def boom(url, **kw):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    assert mr.fetch_tw_eps() == {}
