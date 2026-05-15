"""fetch_tw_top100_universe 測試：正常解析排名 / OpenAPI 失敗時 fallback。"""
import morning_report as mr


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeCsvResp:
    def __init__(self, text):
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        pass


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


_REVENUE = [
    {"公司代號": "2330", "資料年月": "11504",
     "營業收入-當月營收": "300,000,000",
     "營業收入-上月比較增減(%)": "5.2",
     "營業收入-去年同月增減(%)": "38.6",
     "累計營業收入-前期比較增減(%)": "41.0"},
    {"公司代號": "2317", "資料年月": "11504",
     "營業收入-當月營收": "550,000,000",
     "營業收入-上月比較增減(%)": "-2.1",
     "營業收入-去年同月增減(%)": "12.4",
     "累計營業收入-前期比較增減(%)": "10.0"},
    {"公司代號": "00878", "資料年月": "11504",   # 5 位代號應被略過
     "營業收入-當月營收": "0",
     "營業收入-上月比較增減(%)": "0",
     "營業收入-去年同月增減(%)": "0",
     "累計營業收入-前期比較增減(%)": "0"},
]


def test_monthly_revenue_parses(monkeypatch):
    monkeypatch.setattr(mr.requests, "get",
                        lambda url, **kw: _FakeResp(_REVENUE))
    rev = mr.fetch_tw_monthly_revenue()
    assert "00878" not in rev
    assert rev["2330"]["yoy_pct"] == 38.6
    assert rev["2330"]["mom_pct"] == 5.2
    assert rev["2330"]["rev"] == 300_000_000
    assert rev["2317"]["yoy_pct"] == 12.4


def test_monthly_revenue_fallback_on_failure(monkeypatch):
    def boom(url, **kw):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    assert mr.fetch_tw_monthly_revenue() == {}


_TDCC_CSV = (
    "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n"
    "20260509,2330,1,10000,5000000,2.00\n"          # 散戶分級，應排除
    "20260509,2330,12,500,400000000,1.93\n"
    "20260509,2330,13,300,300000000,1.50\n"
    "20260509,2330,14,200,250000000,2.10\n"
    "20260509,2330,15,150,9000000000,68.50\n"
    "20260509,2330,17,11150,9955000000,100.00\n"    # 合計，應排除
    "20260509,2317,15,5000,4000000000,40.00\n"
)


def test_tdcc_major_holders_parses(monkeypatch):
    monkeypatch.setattr(mr.requests, "get", lambda url, **kw: _FakeCsvResp(_TDCC_CSV))
    out = mr.fetch_tdcc_major_holders({"2330", "2317"})
    # 2330：分級 12-15 加總 = 1.93+1.50+2.10+68.50
    assert round(out["2330"]["major_holder_pct"], 2) == 74.03
    assert out["2317"]["major_holder_pct"] == 40.0
    assert out["2330"]["date"] == "20260509"


def test_tdcc_respects_target_codes(monkeypatch):
    monkeypatch.setattr(mr.requests, "get", lambda url, **kw: _FakeCsvResp(_TDCC_CSV))
    out = mr.fetch_tdcc_major_holders({"2330"})
    assert "2317" not in out


def test_tdcc_fallback_on_failure(monkeypatch):
    def boom(url, **kw):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    assert mr.fetch_tdcc_major_holders() == {}


_STOCK_DAY_ALL = [
    {"Code": "2330", "Name": "台積電", "ClosingPrice": "1,085.00"},
    {"Code": "00662", "Name": "富邦NASDAQ", "ClosingPrice": "119.45"},
]


def test_twse_close_finds_etf(monkeypatch):
    monkeypatch.setattr(mr.requests, "get", lambda url, **kw: _FakeResp(_STOCK_DAY_ALL))
    # 00662 是 ETF（5 位代號），Yahoo 常落後 → 改用 TWSE 官方收盤
    assert mr.fetch_twse_close("00662") == 119.45
    assert mr.fetch_twse_close("2330") == 1085.0


def test_twse_close_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(mr.requests, "get", lambda url, **kw: _FakeResp(_STOCK_DAY_ALL))
    assert mr.fetch_twse_close("9999") is None


def test_twse_close_fallback_on_failure(monkeypatch):
    def boom(url, **kw):
        raise mr.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(mr.requests, "get", boom)
    assert mr.fetch_twse_close("00662") is None


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
    monkeypatch.setattr(mr, "fetch_tw_monthly_revenue", lambda: {})
    monkeypatch.setattr(mr, "fetch_tdcc_major_holders", lambda tc=None: {})
    monkeypatch.setattr(mr.yf, "download", fake_download)

    universe = {"2330": {"name": "台積電", "industry": "半導體業", "market_cap": 1e13},
                "2454": {"name": "聯發科", "industry": "半導體業", "market_cap": 2e12}}
    mr.fetch_tw0050_snapshot(universe)
    assert captured["target_codes"] == {"2330", "2454"}
    assert "2330.TW" in captured["tickers"] and "2454.TW" in captured["tickers"]
