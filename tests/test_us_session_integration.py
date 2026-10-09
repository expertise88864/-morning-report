"""A data delay must not become an exchange holiday anywhere downstream."""
import datetime as dt
from types import SimpleNamespace

import pandas as pd

import evidence_packet as ep
import evidence_registry as er
import morning_report as mr
import signal_tensions as st


def quotes():
    return {"QQQ": {"date": "2026-10-07", "close": 700, "prev_close": 680, "change_pct": 2.9},
            "TSM": {"date": "2026-10-08", "close": 450, "prev_close": 440, "change_pct": 2.3},
            "MACRO": {"SOX": {"change_pct": 2}, "VIX": {"close": 16}},
            "TAIFEX_OI": {"foreign_oi_net": -83000},
            "FOREIGN_TOP10_TOTAL": -76000, "BREADTH": {"advance_ratio": 39}}


def test_october9_data_delay_remains_unusable_without_claiming_holiday():
    q = quotes()
    q["US_HOLIDAY"] = mr.detect_us_holiday(q, dt.date(2026, 10, 9))
    assert q["US_HOLIDAY"]["detected"] is False
    assert q["US_HOLIDAY"]["stale"] is True
    stance = mr._compute_stance_score(q)
    assert stance["stale_us"] and stance["mode"] == "taiwan_only"
    assert stance["components"]["qqq"] == 0
    alerts = mr.detect_market_alerts(q, {}, {}, {})
    assert any(a["title"] == "美股行情資料未更新" for a in alerts)
    assert not any("美股昨日休市" in a["title"] + a["detail"] for a in alerts)
    tensions = st.detect(q)
    us = next(row for row in tensions["items"] if row["tension_id"] == "t_us_vs_taifex")
    assert not us["usable_for_inference"] and "美股昨日休市" not in us["caveat"]
    packet = ep.build(q, {}, {}, [], [], {}, as_of="2026-10-09T06:00",
                      target_session_date="2026-10-09", sanitize=str)
    evidence = er.registry(packet)["market:QQQ.change_pct"]
    assert not evidence["usable_for_inference"] and "美股昨日休市" not in evidence["why_unusable"]


def test_refreshed_quote_restores_regular_stance_and_no_holiday_warning():
    q = quotes()
    q["QQQ"]["date"] = "2026-10-08"
    q["US_HOLIDAY"] = mr.detect_us_holiday(q, dt.date(2026, 10, 9))
    stance = mr._compute_stance_score(q)
    assert not stance["stale_us"] and stance["mode"] == "global_full"
    assert stance["components"]["qqq"] == 1
    assert not any("休市" in a["title"] for a in mr.detect_market_alerts(q, {}, {}, {}))


def test_stance_prompt_distinguishes_official_holiday_from_delayed_data():
    q = quotes()
    q["QQQ"]["date"] = "2026-11-25"
    q["US_HOLIDAY"] = mr.detect_us_holiday(q, dt.date(2026, 11, 27))
    assert q["US_HOLIDAY"]["detected"] is True
    block = mr._format_stance_py_block(mr._compute_stance_score(q))
    assert "休市" in block and "不代表休市" not in block and "不是休市" not in block
    q = quotes()
    q["US_HOLIDAY"] = mr.detect_us_holiday(q, dt.date(2026, 10, 9))
    block = mr._format_stance_py_block(mr._compute_stance_score(q))
    assert "不是休市" in block
    stance = mr._compute_stance_score(q)
    stance.pop("us_status")
    assert "休市" not in mr._format_stance_py_block(stance)


def test_individually_stale_tsm_cannot_feed_price_estimation_or_stance():
    q = quotes()
    q["QQQ"]["date"] = "2026-10-08"
    q["TSM"].update(stale=True, date="2026-10-07")
    q["US_HOLIDAY"] = mr.detect_us_holiday(q, dt.date(2026, 10, 9))
    assert mr.require_quote(q, "TSM") is None
    assert mr.require_quote(q, "QQQ") is not None
    stance = mr._compute_stance_score(q)
    assert stance["components"]["tsm_adr"] == 0 and "tsm_adr" in stance["missing"]
    evidence = er.registry({"market": q})["market:TSM.change_pct"]
    assert not evidence["usable_for_inference"] and "休市" not in evidence["why_unusable"]


def test_stale_tsm_is_excluded_from_real_taiex_and_0050_phase(monkeypatch):
    q = quotes()
    q["TSM"].update(stale=True, change_pct=10)
    q["MACRO"] = {"SOX": {"change_pct": 0}}
    for name in ("fetch_taifex_foreign_futures", "fetch_taifex_large_traders",
                 "fetch_taifex_options_pc_ratio", "fetch_twse_margin", "fetch_weekly_momentum",
                 "fetch_taifex_basis", "fetch_twse_market_breadth"):
        monkeypatch.setattr(mr, name, lambda: {})
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr(mr, "check_tsmc_earnings_proximity", lambda: {"note": "fixture"})
    monkeypatch.setattr(mr, "load_history_state", lambda **k: [])
    monkeypatch.setattr(mr, "fetch_taifex_night_session", lambda: {"night_pct": 0})
    hist = pd.DataFrame({"Close": [20000.]}, index=pd.to_datetime(["2026-10-08"]))
    monkeypatch.setattr(mr, "fetch_taiex_history", lambda: hist.copy())
    monkeypatch.setattr(mr, "fetch_twse_taiex_close", lambda: 20000.)
    monkeypatch.setattr(mr, "fetch_twse_close", lambda code: 100.)
    monkeypatch.setattr(mr, "calibrate_predictions", lambda f, p, t, h: (f, p, t))
    monkeypatch.setattr(mr, "calibrate_0050_bias", lambda p, h: p)
    ctx = SimpleNamespace(ex_div={}, fair={}, predictions={"error": "TSM stale"},
                          quotes=q, mark_phase=lambda *a: None)
    mr._phase_taifex_and_chips(ctx)
    assert ctx.taiex_pred["pred_open"] == 20000.
    assert ctx.tw0050_pred["pred_open"] == 100.


def test_pending_history_does_not_relabel_stale_returns_as_today(monkeypatch):
    q = quotes()
    q["QQQ"]["stale"] = q["TSM"]["stale"] = True
    q["SPY"] = {"change_pct": 1.2, "stale": False}
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(mr, "render_html", lambda *a: "<html>offline</html>")
    monkeypatch.setattr(mr, "_structured_stance", lambda: {})
    monkeypatch.setattr(mr, "_latest_completed_session", lambda *a: None)
    monkeypatch.setattr(mr, "save_model_history", lambda *a: (_ for _ in ()).throw(AssertionError("state write")))
    ctx = SimpleNamespace(
        analysis="", earnings_proximity={}, ex_div={}, fair={}, mode="daily", model_history=[],
        news=[], night_txf={}, now_tpe=dt.datetime(2026, 10, 9, 6), predictions={}, quotes=q,
        report_date="2026-10-09", taiex_pred={}, taifex_large={}, taifex_oi={}, taifex_pcr={},
        target_session_date="2026-10-09", tdcc_snapshot_for_state={}, trading_sessions=[],
        tw0050=[], tw0050_pred={}, twse_taiex_close=None, mark_phase=lambda *a: None)
    mr._phase_render(ctx)
    assert ctx.pending_state_entry["qqq_pct"] is None
    assert ctx.pending_state_entry["tsm_pct"] is None
    assert ctx.pending_state_entry["spy_pct"] == 1.2


def test_individual_qqq_stale_reaches_legacy_tension_metadata():
    q = quotes()
    q["QQQ"]["stale"] = True
    q["US_HOLIDAY"] = {"detected": False}
    us = next(row for row in st.detect(q)["items"] if row["tension_id"] == "t_us_vs_taifex")
    assert not us["usable_for_inference"]
    assert "未更新" in us["caveat"] and "符合預期" not in us["caveat"]


def test_stale_quotes_are_not_presented_as_today_in_either_email_table(monkeypatch):
    q = quotes()
    q["SPY"] = {"date": "2026-10-08", "close": 600., "change_pct": .1}
    for symbol in ("QQQ", "TSM", "SPY"):
        q[symbol].update(ticker=symbol, stale=True, change_pct=19.876)
    normal = mr.render_html(q, {"error": "stale"}, {"error": "stale"}, "", "2026-10-09", "每日報")
    minimal = mr._render_minimal_html(q, {}, {}, "", "2026-10-09", "每日報")
    for output in (normal, minimal):
        assert "行情未更新" in output and "2026-10-07" in output
        assert "19.876" not in output


def test_email_subject_qualifies_stale_quote_without_sending(monkeypatch):
    q = quotes()
    q["QQQ"].update(stale=True, change_pct=19.876)
    seen = []
    monkeypatch.setattr(mr, "_write_run_manifest", lambda *a, **k: None)
    monkeypatch.setattr(mr, "deliver_report", lambda html, subject, *a, **k: seen.append(subject))
    monkeypatch.setattr(mr, "_MAIL_UNRESOLVED", [])
    ctx = SimpleNamespace(html="offline", now_tpe=dt.datetime(2026, 10, 9),
                          pending_state_entry={}, quotes=q, report_date="2026-10-09",
                          analysis="", news=[], mark_phase=lambda *a: None)
    assert mr._phase_deliver(ctx) == 0
    assert "行情未更新" in seen[0] and "19.876" not in seen[0]
