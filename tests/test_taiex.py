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
        # 平日的真實 runtime 形狀:dict 且 detected=False(Codex review:
        # truthiness 判斷曾天天誤判休市)
        "US_HOLIDAY": {"detected": False},
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
    out = mr._compute_stance_score(_stance_quotes(
        US_HOLIDAY={"detected": True, "weekday": "Fri"}))
    c = out["components"]
    for k in ("qqq", "sox", "vix", "tsm_adr", "10y", "nq", "vix_term", "wti"):
        assert c[k] == 0, k
    assert out["stale_us"] is True
    assert out["total"] == -3      # 外資前十/台指期/廣度三維皆 -1
    # 四審 P0-4:休市 → taiwan_only regime,門檻 ±2(3 維最高 ±3,沿用 ±5
    # 等於休市日永遠中性);台方三維全 -1 → 必須判偏空
    assert out["mode"] == "taiwan_only" and out["label"] == "偏空"
    assert out["coverage"] == 1.0 and out["abstain"] is False
    # 休市 + 台灣三維也全缺 → 適用維度 0/3,必須 abstain(舊 coverage 以 11 維
    # 計會得 8/11=72.7% 而不 abstain——「沒有資料≠中性」)
    out3 = mr._compute_stance_score({"US_HOLIDAY": {"detected": True}})
    assert out3["mode"] == "taiwan_only"
    assert out3["abstain"] is True and out3["label"] == "資料不足"
    # 缺資料 → 0 + missing 名單,不炸;coverage<70% 時「沒有資料≠市場中性」,
    # 標 abstain 並以「資料不足」取代方向標籤(三審 P1-5)
    out2 = mr._compute_stance_score({})
    assert out2["total"] == 0 and out2["label"] == "資料不足"
    assert out2["abstain"] is True and out2["coverage"] < 0.7
    assert len(out2["missing"]) >= 9


def test_stance_py_block_and_attribution_formatting():
    """PR-2 第二階段:系統計分區塊格式+Decision Attribution。"""
    sp = {"total": -8, "label": "偏空",
          "components": {"qqq": -1, "sox": -1, "vix": -1, "tsm_adr": -1,
                         "foreign_top10": -1, "taifex_foreign_oi": -1,
                         "10y": 0, "nq": -1, "vix_term": 0, "wti": -1,
                         "breadth": -1},
          "missing": ["10y"], "flags": [], "stale_us": False}
    hist = [{"date": "2026-07-17", "stance_score_py": -7,
             "stance_components_py": {**sp["components"], "vix": 0}}]
    at = mr._stance_attribution(sp, hist)
    assert at["prev_total"] == -7 and at["curr_total"] == -8
    assert at["changes"] == [("vix", 0, -1)]
    block = mr._format_stance_py_block(sp, at)
    assert "淨分 -8" in block and "偏空" in block
    assert "QQQ [-1]" in block and "10Y [0]" in block
    assert "缺資料(記0):10Y" in block
    assert "立場變化歸因:2026-07-17 -7 → 今日 -8" in block
    assert "VIX +0→-1" in block or "VIX 0→-1" in block.replace("+0", "0")
    # 空 → 空字串(prompt 降級)
    assert mr._format_stance_py_block({}, {}) == ""
    # 無可比基準 → 空 dict
    assert mr._stance_attribution(sp, []) == {}


def test_prompt_uses_python_stance_authority():
    """PR-2 第二階段:prompt 含【系統立場計分】權威區塊與抄錄指令;
    Python 計分缺席時降級為 LLM 自算+標註。"""
    quotes = _stance_quotes()
    quotes["STANCE_PY"] = mr._compute_stance_score(quotes)
    quotes.update({"SEC_FILINGS": [], "TAIFEX_OI": {}, "MARGIN": {},
                   "WEEKLY": {}, "EARNINGS_PROXIMITY": {}, "HISTORY": [],
                   "NIGHT_TXF": {}, "TAIEX_PRED": {}, "BACKTEST": "",
                   "ALERTS": [], "DATA_QUALITY": [], "USDTWD_prev": 31.1})
    p = mr._build_prompt(quotes, {"error": "x"}, {"error": "x"}, [], [], "")
    assert "【系統立場計分" in p and "原樣採用" in p
    assert "淨分" in p
    quotes["STANCE_PY"] = {}
    p2 = mr._build_prompt(quotes, {"error": "x"}, {"error": "x"}, [], [], "")
    assert "系統計分缺席" in p2


def test_render_stance_display_prefers_python(monkeypatch):
    """PR-2 第二階段:KPI 立場以 Python 分數為權威——LLM 文字寫不同立場也不採。"""
    quotes = {
        "QQQ": {"ticker": "QQQ", "close": 720, "prev_close": 718, "change_pct": 0.3,
                "high": 721, "low": 717, "volume": 1, "date": "2026-07-18"},
        "TSM": {"ticker": "TSM", "close": 420, "prev_close": 410, "change_pct": 2.4,
                "high": 422, "low": 415, "volume": 1, "date": "2026-07-18"},
        "SPY": {"ticker": "SPY", "close": 750, "prev_close": 749, "change_pct": 0.1,
                "high": 751, "low": 748, "volume": 1, "date": "2026-07-18"},
        "MACRO": {}, "USDTWD": 31.4, "USDTWD_prev": 31.4,
        "SEC_FILINGS": [], "TW_MOPS": [], "TAIFEX_OI": {}, "MARGIN": {},
        "WEEKLY": {}, "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
        "TAIEX_PRED": {}, "TW0050_PRED": {}, "BREADTH": {}, "MIDTERM": {},
        "BACKTEST": "", "ALERTS": [], "DATA_QUALITY": [],
        "TW_UNIVERSE_SNAPSHOT": [], "US_HOLIDAY": {},
    }
    quotes["STANCE_PY"] = {"total": -8, "label": "偏空",
                           "components": {"qqq": -1}}
    quotes["STANCE_ATTRIB"] = {"prev_date": "2026-07-17", "prev_total": -6,
                               "curr_total": -8,
                               "changes": [("qqq", 0, -1), ("vix", 0, -1)]}
    analysis = ("## 十二、我的明確立場\n> **立場：偏多**(淨分 +6)\n"
                "## 十三、一句話總結\n偏多操作 00662 逢低加碼")
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, analysis,
                          "2026-07-18 (Sat)", "每日報")
    assert "偏空" in html and "-8" in html
    # Codex r1 P1 合規防線:LLM 相反立場的結論/方向性建議不得殘留
    assert "偏多操作 00662 逢低加碼" not in html
    assert "依系統計分" in html
    # Codex r1 P2:歸因卡包 <tr><td>(裸 div 是 table 非法子元素)
    assert "立場變化歸因" in html and "-6 → -8" in html.replace("+", "")
    import re as _re
    assert _re.search(r"<tr><td[^>]*>\s*<div[^>]*'>\s*<b>立場變化歸因", html), \
        "歸因卡必須包在 <tr><td> 內"


def test_stance_attribution_skips_same_day_entries():
    """Codex r1 P2:同日重跑存下的今日 entry 不得當基準(今天比今天)。"""
    sp = {"total": -8, "label": "偏空", "components": {"qqq": -1, "vix": -1}}
    hist = [
        {"date": "2026-07-17", "stance_score_py": -6,
         "stance_components_py": {"qqq": 0, "vix": -1}},
        {"date": "2026-07-18", "stance_score_py": -7,
         "stance_components_py": {"qqq": -1, "vix": 0}},   # 今日較早版本
    ]
    at = mr._stance_attribution(sp, hist, today="2026-07-18")
    assert at["prev_date"] == "2026-07-17" and at["prev_total"] == -6
    assert ("qqq", 0, -1) in at["changes"]


def test_prompt_degraded_mode_instructions_consistent():
    """Codex r1 P2:降級模式(Python 計分缺席)不得殘留「原樣抄錄/禁止自算」
    互斥指令;權威模式反之不得出現「強制自算標註」。"""
    quotes = _stance_quotes()
    quotes.update({"SEC_FILINGS": [], "TAIFEX_OI": {}, "MARGIN": {},
                   "WEEKLY": {}, "EARNINGS_PROXIMITY": {}, "HISTORY": [],
                   "NIGHT_TXF": {}, "TAIEX_PRED": {}, "BACKTEST": "",
                   "ALERTS": [], "DATA_QUALITY": [], "USDTWD_prev": 31.1})
    quotes["STANCE_PY"] = mr._compute_stance_score(quotes)
    p_auth = mr._build_prompt(quotes, {"error": "x"}, {"error": "x"}, [], [], "")
    assert "原樣抄錄" in p_auth and "禁止自行計算" in p_auth
    assert "系統計分缺席,本行為 LLM 自算" not in p_auth
    quotes["STANCE_PY"] = {}
    p_deg = mr._build_prompt(quotes, {"error": "x"}, {"error": "x"}, [], [], "")
    assert "系統計分缺席" in p_deg and "自行計算" in p_deg
    assert "原樣抄錄" not in p_deg and "禁止自行計算" not in p_deg


def test_summary_stance_word_also_enforced():
    """Codex r2:十二段抄對但一句話總結寫別的立場詞 → 仍觸發確定性摘要;
    「資料不足」為合法立場詞(十三段詞表已補)。"""
    quotes = {
        "QQQ": {"ticker": "QQQ", "close": 720, "prev_close": 718, "change_pct": 0.3,
                "high": 721, "low": 717, "volume": 1, "date": "2026-07-18"},
        "TSM": {"ticker": "TSM", "close": 420, "prev_close": 410, "change_pct": 2.4,
                "high": 422, "low": 415, "volume": 1, "date": "2026-07-18"},
        "SPY": {"ticker": "SPY", "close": 750, "prev_close": 749, "change_pct": 0.1,
                "high": 751, "low": 748, "volume": 1, "date": "2026-07-18"},
        "MACRO": {}, "USDTWD": 31.4, "USDTWD_prev": 31.4,
        "SEC_FILINGS": [], "TW_MOPS": [], "TAIFEX_OI": {}, "MARGIN": {},
        "WEEKLY": {}, "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
        "TAIEX_PRED": {}, "TW0050_PRED": {}, "BREADTH": {}, "MIDTERM": {},
        "BACKTEST": "", "ALERTS": [], "DATA_QUALITY": [],
        "TW_UNIVERSE_SNAPSHOT": [], "US_HOLIDAY": {},
        "STANCE_PY": {"total": 0, "label": "資料不足", "components": {"qqq": 0}},
    }
    analysis = ("## 十二、我的明確立場\n> **立場：資料不足**(淨分 0)\n"
                "## 十三、一句話總結\n中性觀望 等待更多資料再進場")
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, analysis,
                          "2026-07-18 (Sat)", "每日報")
    assert "中性觀望 等待更多資料再進場" not in html   # 總結立場詞不一致 → 移除
    assert "依系統計分" in html and "資料不足" in html


def test_summary_stance_word_uses_first_position():
    """Codex r4:多立場詞句取「字串位置最前」者——「偏空風險升高,偏多仍可
    加碼」在權威=偏多時必須觸發替換(位置最前的詞是偏空)。"""
    quotes = {
        "QQQ": {"ticker": "QQQ", "close": 720, "prev_close": 718, "change_pct": 0.3,
                "high": 721, "low": 717, "volume": 1, "date": "2026-07-18"},
        "TSM": {"ticker": "TSM", "close": 420, "prev_close": 410, "change_pct": 2.4,
                "high": 422, "low": 415, "volume": 1, "date": "2026-07-18"},
        "SPY": {"ticker": "SPY", "close": 750, "prev_close": 749, "change_pct": 0.1,
                "high": 751, "low": 748, "volume": 1, "date": "2026-07-18"},
        "MACRO": {}, "USDTWD": 31.4, "USDTWD_prev": 31.4,
        "SEC_FILINGS": [], "TW_MOPS": [], "TAIFEX_OI": {}, "MARGIN": {},
        "WEEKLY": {}, "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
        "TAIEX_PRED": {}, "TW0050_PRED": {}, "BREADTH": {}, "MIDTERM": {},
        "BACKTEST": "", "ALERTS": [], "DATA_QUALITY": [],
        "TW_UNIVERSE_SNAPSHOT": [], "US_HOLIDAY": {},
        "STANCE_PY": {"total": 6, "label": "偏多", "components": {"qqq": 1}},
    }
    analysis = ("## 十二、我的明確立場\n> **立場：偏多**(淨分 +6)\n"
                "## 十三、一句話總結\n偏空風險升高,偏多仍可加碼 00662")
    html = mr.render_html(quotes, {"error": "x"}, {"error": "x"}, analysis,
                          "2026-07-18 (Sat)", "每日報")
    assert "偏空風險升高,偏多仍可加碼 00662" not in html
    assert "依系統計分" in html
