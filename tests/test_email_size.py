"""信件超過約 100KB 才精簡體育快訊(2026-11-17 使用者選 b)。"""
import email_size
import morning_report as mr


def _sports():
    games = [{"when": f"11/{18 + i} 08:00", "text": f"GAME{i}"} for i in range(8)]
    games.insert(1, {"when": "11/18", "text": "HIDDEN", "phase": 1})
    news = {"NBA": [{"title": f"NBANEWS{i}", "link": f"https://x/{i}"} for i in range(3)],
            "MLB": [{"title": f"MLBNEWS{i}", "link": f"https://y/{i}"} for i in range(3)]}
    return {"nba_fixtures": games, "news": news}


def _quotes():
    def base(t):
        return {"ticker": t, "date": "2026-11-16", "close": 100.0, "prev_close": 99.0, "change_pct": 1.01}
    return {"QQQ": base("QQQ"), "TSM": base("TSM"), "SPY": base("SPY"),
            "USDTWD": 31.0, "USDTWD_prev": 31.1, "MACRO": {}, "SEC_FILINGS": [], "TAIFEX_OI": {},
            "MARGIN": {}, "WEEKLY": {}, "EARNINGS_PROXIMITY": {}, "HISTORY": [], "NIGHT_TXF": {},
            "TAIEX_PRED": {}, "BACKTEST": "", "ALERTS": [], "DATA_QUALITY": [], "SPORTS": _sports()}


def test_compact_sports_keeps_four_games_and_two_news_without_touching_the_original():
    original = _sports()
    out = email_size.compact_sports(original)
    shown = [g["text"] for g in out["nba_fixtures"] if g.get("phase") != 1]
    assert shown == ["GAME0", "GAME1", "GAME2", "GAME3"]
    assert all(len(v) == 2 for v in out["news"].values())
    assert len(original["nba_fixtures"]) == 9 and len(original["news"]["NBA"]) == 3


def test_compact_sports_tolerates_odd_shapes():
    assert email_size.compact_sports(None) is None
    assert email_size.compact_sports({"news": {"NBA": "x"}, "nba_fixtures": None}) == {
        "news": {"NBA": "x"}, "nba_fixtures": None}


def _render():
    return mr.render_html(_quotes(), {"error": "x"}, {"error": "x"}, "x", "2026-11-17", "每日報")


def test_under_the_limit_the_sports_card_is_untouched(monkeypatch):
    monkeypatch.setattr(email_size, "LIMIT_KB", 10_000.0)
    html = _render()
    assert "GAME7" in html and "NBANEWS2" in html and "MLBNEWS2" in html


def test_over_the_limit_only_sports_is_compacted(monkeypatch):
    monkeypatch.setattr(email_size, "LIMIT_KB", 1.0)
    html = _render()
    assert "GAME3" in html and "GAME4" not in html
    assert "NBANEWS1" in html and "NBANEWS2" not in html and "MLBNEWS2" not in html
    assert "體育快訊" in html   # 區塊不移除
