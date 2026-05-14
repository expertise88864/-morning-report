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

    # data_map 由 fixture 注入到 class attribute
    data_map: dict = {}

    def history(self, **kwargs):
        df = FakeTicker.data_map.get(self.symbol)
        if df is None:
            return pd.DataFrame({"Close": []})
        return df.copy()


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
