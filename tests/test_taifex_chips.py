"""TAIFEX 大額交易人 + 選擇權 Put/Call 比 fetcher 測試(借鏡 node-twstock,OpenAPI JSON)。"""
import morning_report as mr


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


_PCR = [
    {"Date": "20260622", "PutVolume": "310654", "CallVolume": "243174",
     "PutCallVolumeRatio%": "127.75", "PutOI": "57608", "CallOI": "39017",
     "PutCallOIRatio%": "147.65"},
    {"Date": "20260618", "PutCallVolumeRatio%": "112.84", "PutCallOIRatio%": "114.67"},
]

_LT = [
    # 別的商品(應被過濾)
    {"Date": "20260622", "Contract": "BRF", "SettlementMonth": "999912",
     "TypeOfTraders": "0", "Top5Buy": "55", "Top5Sell": "61", "Top10Buy": "66",
     "Top10Sell": "70", "OIOfMarket": "37"},
    # TX 近月(非合計,應被過濾)
    {"Date": "20260622", "Contract": "TX", "SettlementMonth": "202607",
     "TypeOfTraders": "0", "Top5Buy": "62250", "Top5Sell": "61038",
     "Top10Buy": "71183", "Top10Sell": "77034", "OIOfMarket": "105197"},
    # TX 所有契約合計 — 全部交易人
    {"Date": "20260622", "Contract": "TX", "SettlementMonth": "999912",
     "TypeOfTraders": "0", "Top5Buy": "62250", "Top5Sell": "61285",
     "Top10Buy": "71623", "Top10Sell": "77835", "OIOfMarket": "109089"},
    # TX 所有契約合計 — 特定法人
    {"Date": "20260622", "Contract": "TX", "SettlementMonth": "999912",
     "TypeOfTraders": "1", "Top5Buy": "62250", "Top5Sell": "61285",
     "Top10Buy": "69594", "Top10Sell": "77835", "OIOfMarket": "109089"},
]


def test_pc_ratio_picks_latest_and_parses(monkeypatch):
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(_PCR))
    out = mr.fetch_taifex_options_pc_ratio()
    assert out["date"] == "20260622"          # 取最新日(非清單順序)
    assert out["pc_oi_ratio"] == 147.65
    assert out["pc_vol_ratio"] == 127.75


def test_pc_ratio_fallback_on_failure(monkeypatch):
    def boom(*a, **k):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    assert mr.fetch_taifex_options_pc_ratio() == {}
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp([]))
    assert mr.fetch_taifex_options_pc_ratio() == {}


def test_large_traders_filters_tx_all_contracts(monkeypatch):
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(_LT))
    out = mr.fetch_taifex_large_traders()
    assert out["date"] == "20260622"
    assert out["top10_buy"] == 71623 and out["top10_sell"] == 77835
    assert out["top10_net"] == 71623 - 77835            # -6212(偏空)
    assert out["oi_market"] == 109089
    assert out["concentration_pct"] == round(77835 / 109089 * 100, 1)   # 71.3
    assert out["spec_top10_net"] == 69594 - 77835       # -8241(特定法人更空)


def test_large_traders_empty_when_no_tx(monkeypatch):
    only_brf = [r for r in _LT if r["Contract"] == "BRF"]
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(only_brf))
    assert mr.fetch_taifex_large_traders() == {}


def test_large_traders_fallback_on_failure(monkeypatch):
    def boom(*a, **k):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    assert mr.fetch_taifex_large_traders() == {}


def test_large_traders_failsafe_on_missing_fields(monkeypatch):
    """缺 Top10Buy/Sell/OI 欄位 → 嚴格 parser 回 None → fail-safe 回 {}(不可用 0 算假部位)。"""
    bad = [{"Date": "20260622", "Contract": "TX", "SettlementMonth": "999912",
            "TypeOfTraders": "0", "Top10Buy": "", "OIOfMarket": "109089"}]  # 缺 Top10Sell、Buy 空
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(bad))
    assert mr.fetch_taifex_large_traders() == {}
    zero_oi = [{"Date": "20260622", "Contract": "TX", "SettlementMonth": "999912",
                "TypeOfTraders": "0", "Top10Buy": "100", "Top10Sell": "90", "OIOfMarket": "0"}]
    monkeypatch.setattr(mr.requests, "get", lambda *a, **k: _Resp(zero_oi))
    assert mr.fetch_taifex_large_traders() == {}      # OI=0 不可當分母
