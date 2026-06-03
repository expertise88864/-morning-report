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
