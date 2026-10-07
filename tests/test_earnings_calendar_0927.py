"""Offline regression for earnings source-date and time-zone uncertainty."""

import datetime as dt

import morning_report as mr
from earnings_calendar import earnings_row
from earnings_timing_guard import correct_uncertain_earnings_impact
from render_utils import _render_event_calendar_html


def test_date_only_earnings_never_invents_eastern_after_hours():
    today = dt.date(2026, 9, 26)
    row = earnings_row("MU", {"Earnings Date": [dt.date(2026, 10, 1)]},
                       today, today + dt.timedelta(days=7))
    assert row is not None
    assert row["date"] == dt.date(2026, 10, 1)
    assert row["time"] == "時間待確認"
    assert row["date_zone_uncertain"] is True
    assert "時區" in row["note"] and "盤後" not in str(row)
    html = _render_event_calendar_html([row])
    assert "10/01" in html and "時區與公布時刻未核實" in html
    assert "時間均為台北時間" not in html and "盤後" not in html


def test_datetime_and_scalar_values_remain_date_only():
    today = dt.date(2026, 9, 26)
    source = dt.datetime(2026, 9, 30, 20, 30, tzinfo=dt.timezone.utc)
    row = earnings_row("MU", {"Earnings Date": source}, today,
                       today + dt.timedelta(days=7))
    assert row is not None and row["date"] == source.date()
    assert row["time"] == "時間待確認"
    assert earnings_row("MU", {"Earnings Date": ["bad"]}, today,
                        today + dt.timedelta(days=7)) is None


def test_fetch_calendar_uses_uncertain_earnings_row_offline(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    class Ticker:
        calendar = {"Earnings Date": [dt.date(2026, 10, 1)]}

    monkeypatch.setattr(mr, "_http_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(mr.yf, "Ticker", lambda ticker: Ticker() if ticker == "MU"
                        else type("Empty", (), {"calendar": {}})())
    now = dt.datetime(2026, 9, 26, 7, tzinfo=mr.TPE)
    rows = mr.fetch_event_calendar(now, horizon_days=7)
    earnings = [row for row in rows if row["title"] == "MU 財報"]
    assert len(earnings) == 1 and earnings[0]["time"] == "時間待確認"
    prompt = mr._format_event_scenarios(earnings,
        dt.datetime(2026, 9, 29, 7, tzinfo=mr.TPE))
    assert "日期已知、時間待確認" in prompt
    assert "MU 財報（時間待確認）" in prompt
    assert "原始時間註記：時間待確認" not in prompt


def test_unverified_mu_time_cannot_become_taiwan_same_day_impact():
    row = {"title": "MU 財報", "date_zone_uncertain": True}
    source = "[原始新聞標題：對台股週三（9/30）盤中才有實質影響](https://example.test)\n"
    prose = "美光（MU）財報是美東盤後公布，對台股週三（9/30）盤中才有實質影響。\n"
    manifest = {}
    corrected = correct_uncertain_earnings_impact(source + prose, [row], manifest)
    assert corrected.startswith(source)
    assert "財報公布時刻未核實，不能推定" in corrected
    assert "才有實質影響。" not in corrected[len(source):]
    assert manifest["llm"]["uncertain_earnings_timing_claims_neutralized"] == 1
    assert correct_uncertain_earnings_impact(prose, [], {}) == prose


def test_emergency_source_list_mu_title_is_preserved_while_own_prose_is_corrected(monkeypatch):
    row = {"title": "MU 財報", "date_zone_uncertain": True}
    title = "- [fixture-source] 美光（MU）財報對台股週三（9/30）盤中才有實質影響。\n"
    own = "本報解讀：美光（MU）財報對台股週三（9/30）盤中才有實質影響。\n"
    manifest = {}
    monkeypatch.setattr(mr, "_RUN_MANIFEST", manifest)
    mr._fallback_analysis_text([{"source": "fixture-source", "title": title.split("] ", 1)[1].rstrip("\n")}],
                              RuntimeError("offline fixture"))
    corrected = correct_uncertain_earnings_impact(title + own, [row], manifest)
    assert corrected.startswith(title)
    assert "財報公布時刻未核實，不能推定" in corrected[len(title):]
    assert manifest["llm"]["uncertain_earnings_timing_claims_neutralized"] == 1
