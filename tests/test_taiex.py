"""calc_taiex_prediction 測試：三訊號加權 / 缺訊號 reweight / 全缺 error。"""
import numpy as np


def _hist(mkdf):
    return mkdf(np.linspace(22000, 23000, 30))


def test_three_signals(fake_yf, mkdf):
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.2, tsm_pct=0.8, night_pct=0.5)
    assert res["signal_count"] == 3
    assert len(res["signals"]) == 3
    assert res["pred_open"] > 0
    assert res["ci_lower"] <= res["pred_open"] <= res["ci_upper"]


def test_reweight_when_night_missing(fake_yf, mkdf):
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.0, tsm_pct=1.0, night_pct=None)
    # 夜盤缺 → 只剩兩個訊號，權重自動重新分配
    assert res["signal_count"] == 2
    names = {s["name"] for s in res["signals"]}
    assert "Night_TXF" not in names
    assert res["pred_open"] > 0


def test_all_signals_missing_returns_error(fake_yf, mkdf):
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=None, tsm_pct=None, night_pct=None)
    assert res.get("error")


def test_missing_history_returns_error():
    import morning_report as mr
    assert mr.calc_taiex_prediction(None, 1.0, 1.0, 1.0).get("error")


def test_consensus_all_bullish(fake_yf, mkdf):
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.5, tsm_pct=1.0, night_pct=0.8)
    assert "偏多" in res["consensus"]
    assert res["signal_std"] is not None


def test_us_signal_rescaled_by_backtest_beta(fake_yf, mkdf):
    """482 日全合成回測(含夜盤)定案:有效 beta=0.31(美股訊號縮放,夜盤不縮)。"""
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.2, tsm_pct=0.8, night_pct=0.5)
    assert res["us_rescale_k"] == mr.TAIEX_US_BETA_PRIOR == 0.31
    # us_combo=(1.2*1.05*0.4+0.8*0.3)/0.7≈1.063 → 0.7*0.31*1.063 + 0.3*0.5 ≈ 0.38
    assert 0.2 < res["raw_weighted_pct"] < 0.55   # 遠小於舊公式的 ~0.85
    # signals 帶有效權重,加總 <1(縮放的體現)
    assert sum(s["weight"] for s in res["signals"]) < 1.0


def test_us_beta_pinned_to_prior_dynamic_disabled(fake_yf, mkdf):
    """動態 live OLS 已停用(誤設:學成 US-only ~0.19,餵進 0.70/0.30 blend 會雙重低估)。
    在改成殘差式規格 + 回測前,不論 live 樣本多少都固定回傳回測先驗 0.31,避免 ≥30 後漂移劣化。"""
    import morning_report as mr
    # 即使 ≥30 筆樣本(舊版會動態估出 0.5),現在仍釘在 0.31
    ctx = {"us_beta_samples": [(1.0, 0.5)] * 40}
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.0, tsm_pct=1.0,
                                   night_pct=None, context=ctx)
    assert res["us_rescale_k"] == mr.TAIEX_US_BETA_PRIOR == 0.31
    assert "OLS" not in res["us_beta_source"]   # 不再走動態路徑
    # 無樣本同樣是先驗
    res2 = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=1.0, tsm_pct=1.0, night_pct=None)
    assert res2["us_rescale_k"] == mr.TAIEX_US_BETA_PRIOR


def test_night_leg_not_rescaled(fake_yf, mkdf):
    """夜盤台指期直接定價開盤(beta≈1),只有美股腿縮放;只剩夜盤時不縮。"""
    import morning_report as mr
    res = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=None, tsm_pct=None, night_pct=1.0)
    assert res["raw_weighted_pct"] == 1.0   # 純夜盤,不被 0.31 縮掉


# --- 回歸測試：fetch_taifex_foreign_futures 曾誤抓「契約金額」欄當「口數」 ---
class _FakeTaifexResp:
    def __init__(self, text):
        self.status_code = 200
        self._text = text
        self.content = text.encode("big5")

    @property
    def text(self):
        return self._text


_TAIFEX_CSV = "\n".join([
    "日期,商品名稱,身份別,多方交易口數,多方契約金額,空方交易口數,空方契約金額,"
    "多空淨額交易口數,多空淨額交易契約金額,多方未平倉口數,多方未平倉契約金額,"
    "空方未平倉口數,空方未平倉契約金額,多空淨額未平倉口數,多空淨額未平倉契約金額",
    "2026/05/14,臺股期貨,外資,100,200,90,180,10,20,50000,99999,12000,612000443,38000,888888",
    "2026/05/14,臺股期貨,投信,1,1,1,1,1,1,8000,1,2000,45341604,6000,1",
    "2026/05/14,臺股期貨,自營商,1,1,1,1,1,1,5000,1,3000,48345585,2000,1",
    "# padding line to keep response body length over the 200-char guard " * 3,
])


def test_taifex_foreign_futures_reads_lots_not_value(monkeypatch):
    import morning_report as mr
    monkeypatch.setattr(mr.requests, "post",
                        lambda url, **kw: _FakeTaifexResp(_TAIFEX_CSV))
    res = mr.fetch_taifex_foreign_futures()
    # 必須抓「多空淨額未平倉口數」(38000)，不是隔壁的「契約金額」(6.12 億)
    assert res["foreign_oi_net"] == 38000
    assert res["invest_oi_net"] == 6000
    assert res["dealer_oi_net"] == 2000


def test_taifex_foreign_futures_accepts_current_header_order(monkeypatch):
    """TAIFEX 現行欄名為「多空未平倉口數淨額」，詞序不同仍應解析。"""
    import morning_report as mr
    csv = _TAIFEX_CSV.replace("多空淨額未平倉口數", "多空未平倉口數淨額")
    monkeypatch.setattr(mr.requests, "post",
                        lambda url, **kw: _FakeTaifexResp(csv))
    res = mr.fetch_taifex_foreign_futures()
    assert res["foreign_oi_net"] == 38000


# 夜盤台指期：「交易時段」欄不在最後一欄，硬編 row[-1] 會抓不到夜盤
_TAIFEX_NIGHT_CSV = "\n".join([
    "交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,"
    "成交量,結算價,未沖銷契約數,交易時段,備註欄",
    "2026/05/14,TX,202605,41300,41500,41200,41374,+74,+0.18,120000,41380,95000,一般,-",
    "2026/05/14,TX,202605,41374,41900,41350,41850,+476,+1.15,80000,41850,95000,盤後,-",
    "2026/05/14,TX,202605W3,41300,41400,41280,41360,+60,+0.15,5000,41360,3000,一般,-",
    "# padding line to keep the response body length over the 200-char guard " * 3,
])


# ===== calc_0050_prediction =====

def test_0050_prediction_weighted_2330_and_taiex():
    import morning_report as mr
    preds = {"mid": 2200.0, "last_2330": 2200.0}    # 2330 pct = 0%
    taiex = {"weighted_pct": 2.0}                    # 加權 +2%
    res = mr.calc_0050_prediction(last_0050=100.0, predictions_2330=preds, taiex_pred=taiex)
    # 加權指數本身已含約 30% 台積電，先扣除後再估其餘 0050 成分。
    assert res["pred_open"] == 101.43
    assert res["pred_pct"] == 1.429
    assert res["pct_taiex_ex_2330"] == round(2.0 / 0.7, 3)


def test_0050_prediction_applies_ex_dividend_once():
    import morning_report as mr
    res = mr.calc_0050_prediction(
        last_0050=100.0,
        predictions_2330={"mid": 2200.0, "last_2330": 2200.0},
        taiex_pred={"weighted_pct": 0.0},
        ex_div_amt=1.2,
    )
    assert res["pred_open"] == 98.8
    assert res["pred_pct"] == -1.2
    assert res["ex_div_amt"] == 1.2


def test_0050_prediction_falls_back_to_taiex_when_2330_missing():
    import morning_report as mr
    res = mr.calc_0050_prediction(last_0050=100.0,
                                   predictions_2330={"error": "x"},
                                   taiex_pred={"weighted_pct": 1.5})
    assert res["pred_pct"] == 1.5
    assert "加權指數" in res["method"]


def test_0050_prediction_error_when_both_upstream_missing():
    import morning_report as mr
    res = mr.calc_0050_prediction(last_0050=100.0,
                                   predictions_2330={"error": "x"},
                                   taiex_pred={"error": "x"})
    assert res.get("error")


def test_0050_prediction_error_when_no_last():
    import morning_report as mr
    assert mr.calc_0050_prediction(None, {"mid": 2200, "last_2330": 2200},
                                    {"weighted_pct": 1.0}).get("error")


def test_taifex_night_session_detects_session_column(monkeypatch):
    import morning_report as mr
    monkeypatch.setattr(mr.requests, "post",
                        lambda url, **kw: _FakeTaifexResp(_TAIFEX_NIGHT_CSV))
    res = mr.fetch_taifex_night_session()
    assert res["day_close"] == 41374
    assert res["night_close"] == 41850
    # 夜盤漲跌 = (41850 - 41374) / 41374 * 100
    assert res["night_pct"] == round((41850 - 41374) / 41374 * 100, 2)


def test_taiex_prediction_shrinks_bullish_forecast_on_conflicts(fake_yf, mkdf):
    import morning_report as mr
    base = mr.calc_taiex_prediction(_hist(mkdf), sox_pct=4.0, tsm_pct=2.0, night_pct=1.0)
    conflicted = mr.calc_taiex_prediction(
        _hist(mkdf), sox_pct=4.0, tsm_pct=2.0, night_pct=1.0,
        context={
            "TAIFEX_OI": {"foreign_oi_net": -60000},
            "MACRO": {
                "SOX": {"change_pct": 4.0},
                "WTI": {"change_pct": 3.5},
                "VIX": {"close": 15.0},
                "VIX9D": {"close": 15.6},
            },
        },
    )
    assert conflicted["raw_weighted_pct"] == base["weighted_pct"]
    assert conflicted["weighted_pct"] < base["weighted_pct"]
    assert conflicted["conflict_shrink_factor"] < 1
    assert "foreign_oi_short" in conflicted["conflict_reasons"]


import morning_report as mr  # noqa: E402 — 延後 import 沿用本檔慣例

# ===== PR-2 雙軌(2026-07-17):11 維立場分 Python 化 =====

def _stance_quotes(**over):
    q = {
        "QQQ": {"change_pct": -1.64}, "TSM": {"change_pct": -2.32},
        "MACRO": {
            "SOX": {"change_pct": -4.29},
            "VIX": {"close": 16.73, "pct_rank_252d": 45},
            "10Y": {"close": 4.57, "prev_close": 4.54},
            "NQ": {"change_pct": -1.8},
            "VIX_TERM": {"ratio": 0.84},
            "WTI": {"change_pct": 1.2},
        },
        "FOREIGN_TOP10_TOTAL": -12000.0,
        "TAIFEX_OI": {"foreign_oi_net": -84453},
        "BREADTH": {"advance_ratio": 34.2},
        "US_HOLIDAY": None,
    }
    q.update(over)
    return q

def test_stance_py_matches_prompt_rules_bearish_day():
    """以 2026-07-17 實際盤面驗證:各維依 §C 規則給分,總分/標籤正確。"""
    out = mr._compute_stance_score(_stance_quotes())
    c = out["components"]
    assert c == {"qqq": -1,            # -1.64 < -0.5
                 "sox": -1,            # -4.29 < -1
                 "vix": 1,             # 16.73 < 18(rank 45 中性,不衝突)
                 "tsm_adr": -1,        # < 0
                 "foreign_top10": -1,  # 賣超
                 "taifex_foreign_oi": -1,   # -84453 < -5000
                 "10y": -1,            # +3 bps > +2
                 "nq": -1,             # -1.8 < -0.5
                 "vix_term": 0,        # contango
                 "wti": 0,             # |1.2| < 3
                 "breadth": -1}        # 34.2 <= 40
    assert out["total"] == -7 and out["label"] == "偏空"
    # 註:當日 LLM 自算 -8(把 VIX 16.73<18 誤判為負分)——雙軌要抓的正是這種偏差
    assert out["missing"] == [] and out["flags"] == []

def test_stance_py_vix_conflict_and_thresholds():
    # VIX 絕對值看多(<18)但百分位看空(>70)→ 衝突記 0
    out = mr._compute_stance_score(_stance_quotes(
        MACRO={**_stance_quotes()["MACRO"],
               "VIX": {"close": 17.0, "pct_rank_252d": 75}}))
    assert out["components"]["vix"] == 0 and "vix_conflict" in out["flags"]
    # 標籤門檻:+4 中性、+5 偏多
    base = _stance_quotes(
        QQQ={"change_pct": 1.0}, TSM={"change_pct": 1.0},
        FOREIGN_TOP10_TOTAL=5000.0, TAIFEX_OI={"foreign_oi_net": 9000},
        BREADTH={"advance_ratio": 70},
        MACRO={"SOX": {"change_pct": 2.0}, "VIX": {"close": 25, "pct_rank_252d": 80},
               "10Y": {"close": 4.50, "prev_close": 4.54},   # -4bps → +1
               "NQ": {"change_pct": 1.0}, "VIX_TERM": {"ratio": 0.9},
               "WTI": {"change_pct": 0.0}})
    out2 = mr._compute_stance_score(base)   # 8個+1、vix -1 → +7 偏多
    assert out2["total"] == 7 and out2["label"] == "偏多"

def test_stance_py_us_holiday_and_missing():
    # R13:美股休市 → 美股八維全 0,只剩台方三維
    out = mr._compute_stance_score(_stance_quotes(US_HOLIDAY={"name": "獨立日"}))
    c = out["components"]
    for k in ("qqq", "sox", "vix", "tsm_adr", "10y", "nq", "vix_term", "wti"):
        assert c[k] == 0, k
    assert out["stale_us"] is True
    assert out["total"] == -3      # 外資前十/台指期/廣度三維皆 -1
    # 缺資料 → 0 + missing 名單,不炸
    out2 = mr._compute_stance_score({})
    assert out2["total"] == 0 and out2["label"] == "中性"
    assert len(out2["missing"]) >= 9
