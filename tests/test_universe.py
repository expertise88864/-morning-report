"""fetch_tw_top100_universe 測試：正常解析排名 / OpenAPI 失敗時 fallback。"""
import morning_report as mr


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


_BASICS = [
    {"公司代號": "2330", "公司簡稱": "台積電", "產業別": "半導體業",
     "已發行普通股數或TDR原發行股數": "25930380458"},
    {"公司代號": "2317", "公司簡稱": "鴻海", "產業別": "其他電子業",
     "已發行普通股數或TDR原發行股數": "13868736199"},
    {"公司代號": "1234", "公司簡稱": "小型股", "產業別": "食品工業",
     "已發行普通股數或TDR原發行股數": "100000000"},
    {"公司代號": "00878", "公司簡稱": "某 ETF", "產業別": "",
     "已發行普通股數或TDR原發行股數": "5000000000"},  # 5 位數代號，應被過濾
]
_PRICES = [
    {"Code": "2330", "ClosingPrice": "1,085.00"},
    {"Code": "2317", "ClosingPrice": "235.00"},
    {"Code": "1234", "ClosingPrice": "50.00"},
    {"Code": "00878", "ClosingPrice": "22.00"},
]


def test_universe_parses_and_ranks_by_market_cap(monkeypatch):
    def fake_get(url, **kwargs):
        if "t187ap03_L" in url:
            return _FakeResp(_BASICS)
        if "STOCK_DAY_ALL" in url:
            return _FakeResp(_PRICES)
        raise AssertionError(f"未預期的 URL: {url}")

    monkeypatch.setattr(mr.requests, "get", fake_get)
    uni = mr.fetch_tw_top100_universe(top_n=2)

    assert list(uni.keys()) == ["2330", "2317"]      # 依市值由大到小
    assert "00878" not in uni                         # 非 4 位數代號被過濾
    assert "1234" not in uni                          # top_n=2 截斷
    assert uni["2330"]["market_cap"] > uni["2317"]["market_cap"]
    assert uni["2330"]["name"] == "台積電"
    assert uni["2330"]["industry"] == "半導體業"


def test_universe_fallback_when_openapi_fails(monkeypatch):
    def boom(url, **kwargs):
        raise mr.requests.exceptions.ConnectionError("network down")

    monkeypatch.setattr(mr.requests, "get", boom)
    uni = mr.fetch_tw_top100_universe(top_n=100)

    # fallback 應回硬編 0050 清單，且每筆標記 fallback=True
    assert set(uni.keys()) == set(mr.TW0050_CONSTITUENTS.keys())
    assert all(v.get("fallback") for v in uni.values())


def test_snapshot_uses_universe_codes(monkeypatch):
    """fetch_tw0050_snapshot 應依傳入的 universe 決定要抓哪些代號。"""
    captured = {}

    def fake_inst():
        return {}

    def fake_inst_cum(days_back=30, target_codes=None):
        captured["target_codes"] = target_codes
        return {}

    def fake_download(tickers, **kwargs):
        captured["tickers"] = tickers
        import pandas as pd
        return pd.DataFrame()

    monkeypatch.setattr(mr, "fetch_twse_institutional", fake_inst)
    monkeypatch.setattr(mr, "fetch_twse_institutional_cumulative", fake_inst_cum)
    monkeypatch.setattr(mr.yf, "download", fake_download)

    universe = {"2330": {"name": "台積電", "industry": "半導體業", "market_cap": 1e13},
                "2454": {"name": "聯發科", "industry": "半導體業", "market_cap": 2e12}}
    mr.fetch_tw0050_snapshot(universe)
    assert captured["target_codes"] == {"2330", "2454"}
    assert "2330.TW" in captured["tickers"] and "2454.TW" in captured["tickers"]
