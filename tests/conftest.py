"""
pytest 共用設定與 fixtures。

重點：
- 在 import morning_report 之前先塞假環境變數（雖然程式已改成 import 不會炸，
  但設好可讓測試更穩定、不依賴本機環境）。
- 提供 fake_yf fixture：用 monkeypatch 把 morning_report.yf.Ticker 換成假物件，
  測試完全不連 Yahoo Finance。
"""
import os

os.environ.setdefault("GMAIL_USER", "test@example.com")
os.environ.setdefault("GMAIL_APP_PASSWORD", "dummy")
os.environ.setdefault("LLM_PROVIDER", "gemini")

import pandas as pd
import pytest

import morning_report as mr


@pytest.fixture(autouse=True)
def _reset_twse_stock_day_all_cache(monkeypatch, tmp_path_factory):
    """STOCK_DAY_ALL 共用快取在測試間必須清空,否則 mock 資料會跨測試污染;
    重試退避在測試中歸零,避免失敗路徑測試慢 10 倍。"""
    mr._TWSE_STOCK_DAY_ALL_CACHE["data"] = None
    mr._TWSE_STOCK_DAY_ALL_CACHE.pop("failed", None)
    mr._RSS_CONTENT_CACHE.clear()   # N5:RSS 內容快取也必須測試間清空,避免跨測試污染
    mr._FEED_STATS.clear()          # V2-N1:per-host feed 統計同理
    mr._HTTP_HOST_STATS.clear()     # 批#32:_http_get per-host 熔斷計數同理
    mr._DEGRADED_STEPS.clear()      # 批#32:模組級可變 list,不重置會跨測試污染資料品質區
    monkeypatch.setattr(mr, "_TWSE_RETRY_SLEEP_BASE", 0.0)
    # §B:信件存檔目錄導到 tmp,避免經 deliver_report 的測試把 *.html.gz 寫進真實 state/emails/
    monkeypatch.setattr(mr, "EMAIL_ARCHIVE_DIR", tmp_path_factory.mktemp("emails"))
    # 政策「已顯示」記錄同理導到 tmp(deliver_report 內會呼叫 mark_intel_shown)
    monkeypatch.setattr(mr, "INTEL_SHOWN_FILE",
                        tmp_path_factory.mktemp("intel") / "intel_shown.json")
    # Polymarket 護欄(斷路器/時間預算)測試間重置,避免跨測試污染
    mr._POLY_GUARD.update({"spent": 0.0, "consecutive_failures": 0, "tripped": False})
    # Polymarket delta 快照導到 tmp(地基批#4)
    monkeypatch.setattr(mr, "POLY_HISTORY_FILE",
                        tmp_path_factory.mktemp("poly") / "poly_history.json")
    # 類股排名快照導到 tmp(地基批#5)
    monkeypatch.setattr(mr, "SECTOR_RANK_FILE",
                        tmp_path_factory.mktemp("sector") / "sector_rank_history.json")
    # Forecast Ledger 導到 tmp(2026-07-18)
    monkeypatch.setattr(mr, "FORECAST_LEDGER_FILE",
                        tmp_path_factory.mktemp("ledger") / "forecast_ledger.json")
    # model_history 分區目錄與 legacy 檔導到 tmp:防測試讀寫真實 state/(地基批#1)
    _mh = tmp_path_factory.mktemp("mh")
    monkeypatch.setattr(mr, "MODEL_HISTORY_FILE", _mh / "model_history.json")
    monkeypatch.setattr(mr, "MODEL_HISTORY_DIR", _mh / "model_history")
    yield
    mr._TWSE_STOCK_DAY_ALL_CACHE["data"] = None
    mr._TWSE_STOCK_DAY_ALL_CACHE.pop("failed", None)
    mr._RSS_CONTENT_CACHE.clear()
    mr._FEED_STATS.clear()


def _bdays(n: int, start: str = "2026-01-05"):
    return pd.date_range(start, periods=n, freq="B")


def make_close_df(values, index=None) -> pd.DataFrame:
    """產生只有 Close 欄位的歷史 DataFrame（模擬 yfinance .history() 回傳）。"""
    idx = index if index is not None else _bdays(len(values))
    return pd.DataFrame({"Close": list(values)}, index=idx)


class FakeTicker:
    """依 symbol 回傳預先準備好的 DataFrame；查無資料回傳空 DataFrame。"""

    def __init__(self, symbol):
        self.symbol = symbol

    # data_map / div_map 由 fixture 注入到 class attribute
    data_map: dict = {}
    div_map: dict = {}   # {symbol: pd.Series 配息(index 為除息日 Timestamp)}

    def history(self, **kwargs):
        df = FakeTicker.data_map.get(self.symbol)
        if df is None:
            return pd.DataFrame({"Close": []})
        return df.copy()

    @property
    def dividends(self):
        return FakeTicker.div_map.get(self.symbol, pd.Series([], dtype=float))


@pytest.fixture
def mkdf():
    """測試用：快速產生只含 Close 欄位的歷史 DataFrame。"""
    return make_close_df


@pytest.fixture
def bdays():
    """測試用：產生 n 個營業日 DatetimeIndex。"""
    return _bdays


@pytest.fixture
def fake_yf(monkeypatch):
    """
    回傳一個 setter：測試呼叫 set_data({symbol: DataFrame}) 後，
    morning_report 內所有 yf.Ticker(...) 都會走假資料。
    """
    def set_data(data_map: dict):
        FakeTicker.data_map = dict(data_map)
        monkeypatch.setattr(mr.yf, "Ticker", FakeTicker)
        return FakeTicker

    yield set_data
    FakeTicker.data_map = {}
    FakeTicker.div_map = {}
