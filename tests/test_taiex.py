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
