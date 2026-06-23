"""分析師評等動能 fetch_analyst_rating_momentum(借鏡 yfinance upgrades_downgrades)測試。"""
import pandas as pd

import morning_report as mr


class _FakeTicker:
    def __init__(self, df):
        self._df = df

    @property
    def upgrades_downgrades(self):
        return self._df


def _ud(rows):
    """rows: (Timestamp, Firm, Action, priceTargetAction) → 仿 yfinance 的 DataFrame(index=GradeDate)。"""
    idx = pd.DatetimeIndex([r[0] for r in rows], name="GradeDate")
    return pd.DataFrame({"Firm": [r[1] for r in rows], "Action": [r[2] for r in rows],
                         "priceTargetAction": [r[3] for r in rows]}, index=idx)


def _recent(days_ago):
    return pd.Timestamp.now().normalize() - pd.Timedelta(days=days_ago)


def test_analyst_momentum_counts_net(monkeypatch):
    df = _ud([
        (_recent(2), "Susquehanna", "main", "Raises"),   # 調高目標
        (_recent(5), "Barclays", "up", "Raises"),        # 升評 + 調高
        (_recent(8), "UBS", "down", "Lowers"),           # 降評 + 調低
    ])
    monkeypatch.setattr(mr.yf, "Ticker", lambda tk: _FakeTicker(df))
    out = mr.fetch_analyst_rating_momentum(tickers=("TSM",), days=30)
    v = out["TSM"]
    assert v["up"] == 1 and v["down"] == 1
    assert v["tgt_raise"] == 2 and v["tgt_cut"] == 1
    assert v["net"] == (1 + 2) - (1 + 1)                  # = 1
    assert v["n"] == 3
    assert "Susquehanna" in v["latest"]                  # 最新一筆


def test_analyst_momentum_filters_by_window(monkeypatch):
    df = _ud([(_recent(3), "A", "up", "Raises"), (_recent(400), "B", "down", "Lowers")])
    monkeypatch.setattr(mr.yf, "Ticker", lambda tk: _FakeTicker(df))
    out = mr.fetch_analyst_rating_momentum(tickers=("T",), days=30)
    assert out["T"]["n"] == 1                             # 400 天前那筆被窗格排除
    assert out["T"]["net"] == 2                           # 只剩 up + raise


def test_analyst_momentum_empty_and_failsafe(monkeypatch):
    monkeypatch.setattr(mr.yf, "Ticker", lambda tk: _FakeTicker(None))
    assert mr.fetch_analyst_rating_momentum(tickers=("X",)) == {}

    monkeypatch.setattr(mr.yf, "Ticker", lambda tk: _FakeTicker(_ud([])))
    assert mr.fetch_analyst_rating_momentum(tickers=("X",)) == {}

    def boom(tk):
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(mr.yf, "Ticker", boom)
    assert mr.fetch_analyst_rating_momentum(tickers=("X",)) == {}    # fail-safe
