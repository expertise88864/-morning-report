import datetime as dt
import json

import pandas as pd
import pytest

import morning_report as mr


def _stock(close, **extra):
    return {
        "code": "2330",
        "name": "台積電",
        "industry": "半導體",
        "close": close,
        "daily_vol_pct": 2.0,
        "pct_5d": 1.0,
        **extra,
    }


def test_parse_twse_date_supports_roc_and_gregorian():
    assert mr._parse_twse_date("115/06/01") == "2026-06-01"
    assert mr._parse_twse_date("2026-06-02") == "2026-06-02"


def test_fetch_trading_sessions_merges_twse_and_long_history(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"Date": "115/06/01"}]

    class Ticker:
        def history(self, **kwargs):
            return pd.DataFrame(
                {"Close": [1, 2]},
                index=pd.to_datetime(["2026-05-29", "2026-06-01"]),
            )

    monkeypatch.setattr(mr.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(mr.yf, "Ticker", lambda *args, **kwargs: Ticker())
    assert mr.fetch_tw_trading_sessions() == ["2026-05-29", "2026-06-01"]


def test_training_rows_require_real_horizon_not_next_saved_snapshot():
    sessions = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]
    history = [
        {"session_date": "2026-06-01", "taiex_close": 100,
         "stocks": {"2330": _stock(100)}},
        {"session_date": "2026-06-03", "taiex_close": 102,
         "stocks": {"2330": _stock(103)}},
    ]
    assert mr.build_model_training_rows(history, sessions, horizon=1) == []
    rows = mr.build_model_training_rows(history, sessions, horizon=2)
    assert len(rows) == 1
    assert rows[0]["future_excess_pct"] == pytest.approx(1.0)


def test_dual_ridge_model_predicts_probability_and_return():
    sessions = [f"2026-01-{day:02d}" for day in range(1, 8)]
    history = []
    for index, session in enumerate(sessions):
        stocks = {}
        for code_index in range(30):
            close = 100 + index + code_index
            stocks[str(1000 + code_index)] = {
                **_stock(close, pct_5d=float(code_index % 5)),
                "code": str(1000 + code_index),
            }
        history.append({"session_date": session, "taiex_close": 100 + index, "stocks": stocks})
    snapshot = [{**_stock(110), "code": "2330"}]
    out = mr._model_predictions(history, sessions, snapshot, horizon=1)["2330"]
    assert out["method"] == "time-decayed ridge + regime blend + Platt + quantile"
    assert 0.05 <= out["beat_market_probability"] <= 0.95
    assert -12 <= out["expected_return_pct"] <= 12
    assert out["market_regime"] == "neutral"
    assert out["regime_training_rows"] == out["training_rows"]


def test_time_decay_weights_prioritize_recent_sessions():
    rows = [{"session_date": f"2026-06-{day:02d}"} for day in range(1, 6)]
    weights = mr._time_decay_weights(rows, half_life_sessions=2)
    assert weights[-1] == pytest.approx(1.0)
    assert weights[0] < weights[-1]


def test_training_rows_preserve_market_regime():
    sessions = ["2026-06-01", "2026-06-02"]
    history = [
        {"session_date": sessions[0], "taiex_close": 100, "market_regime": "risk_off",
         "stocks": {"2330": _stock(100)}},
        {"session_date": sessions[1], "taiex_close": 101, "market_regime": "risk_on",
         "stocks": {"2330": _stock(102, open=101)}},
    ]
    row = mr.build_model_training_rows(history, sessions, 1)[0]
    assert row["market_regime"] == "risk_off"


def test_industry_neutral_scores_are_relative_within_industry():
    scores = mr._industry_neutral_scores([
        {"code": "1", "industry": "A", "attention_score": 10},
        {"code": "2", "industry": "A", "attention_score": 20},
        {"code": "3", "industry": "B", "attention_score": 99},
    ])
    assert scores["1"] < 0 < scores["2"]
    assert scores["3"] == 0


def test_market_regime_detects_risk_off():
    quotes = {
        "MACRO": {"VIX": {"close": 30}, "SOX": {"change_pct": -1}},
        "BREADTH": {"advance_ratio": 55},
    }
    assert mr._market_regime(quotes) == "risk_off"


def test_event_clustering_prefers_official_source_and_decays_old_news():
    now = dt.datetime(2026, 6, 2, 0, tzinfo=dt.timezone.utc)
    events = mr.extract_structured_events(
        [{
            "source": "CNBC",
            "company_label": "2330",
            "title": "2330 raises guidance",
            "published": "2026-06-01T22:00:00Z",
        }, {
            "source": "Blog",
            "company_label": "2454",
            "title": "2454 raises guidance",
            "published": "2026-05-29T00:00:00Z",
        }],
        [{
            "source": "MOPS",
            "code": "2330",
            "title": "2330 raises guidance",
            "published": "2026-06-01T21:00:00Z",
        }],
        now=now,
    )
    tsmc = next(event for event in events if event["entity"] == "2330")
    mediatek = next(event for event in events if event["entity"] == "2454")
    assert tsmc["source"] == "MOPS"
    assert tsmc["source_grade"] == "A"
    assert tsmc["corroboration_count"] == 2
    assert mediatek["freshness_weight"] == 0.2


def test_event_study_replaces_fallback_after_five_labels():
    sessions = [f"2026-06-{day:02d}" for day in range(1, 10)]
    history = []
    for index, session in enumerate(sessions):
        evidence = ([{"event_type": "orders", "direction": 1}] if index < 5 else [])
        history.append({
            "session_date": session,
            "taiex_close": 100,
            "stocks": {"2330": _stock(100 + index * 2, news_catalysts=evidence)},
        })
    study = mr.build_event_study(history, sessions, horizon=1)
    assert study[("orders", 1)]["samples"] == 5
    event = mr.extract_structured_events(
        [{"source": "MOPS", "company_label": "2330", "title": "2330 new orders"}],
        [],
    )
    scored = mr._stock_news_catalysts(
        [_stock(110)], [], [], events=event, event_study=study)
    assert scored["2330"]["evidence"][0]["score_method"] == "hierarchical_event_study:global"


def test_snapshot_compacts_news_evidence():
    snap = mr._snapshot_for_model([_stock(
        100,
        news_catalysts=[{
            "event_id": "x",
            "event_type": "orders",
            "direction": 1,
            "relation": "direct",
            "score_delta": 1.2,
            "source_grade": "A",
            "title": "large title should not be persisted",
        }],
    )])
    assert "title" not in snap["2330"]["news_catalysts"][0]


def test_save_model_history_caps_file_size(monkeypatch, tmp_path):
    path = tmp_path / "model_history.json"
    monkeypatch.setattr(mr, "MODEL_HISTORY_FILE", path)
    monkeypatch.setattr(mr, "MODEL_HISTORY_MAX_BYTES", 300)
    for day in range(1, 8):
        mr.save_model_history({
            "session_date": f"2026-06-{day:02d}",
            "stocks": {"2330": {"close": 100, "padding": "x" * 120}},
        })
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(path.read_bytes()) <= 300
    assert saved[-1]["session_date"] == "2026-06-07"


def test_parse_llm_event_json_recovers_fenced_array():
    parsed = mr._parse_llm_event_json(
        '```json\n[{"entity":"2330","event_type":"orders","direction":1}]\n```')
    assert parsed[0]["entity"] == "2330"


def test_training_rows_include_next_open_and_close_targets():
    sessions = ["2026-06-01", "2026-06-02"]
    history = [
        {"session_date": sessions[0], "taiex_close": 100,
         "stocks": {"2330": _stock(100)}},
        {"session_date": sessions[1], "taiex_close": 101,
         "stocks": {"2330": _stock(103, open=102)}},
    ]
    row = mr.build_model_training_rows(history, sessions, 1)[0]
    assert row["future_open_return_pct"] == pytest.approx(2)
    assert row["future_close_return_pct"] == pytest.approx(3)


def test_platt_fit_returns_calibrated_probability():
    scores = [index / 100 for index in range(-40, 40)]
    labels = [float(score > 0) for score in scores]
    params = mr._platt_fit(scores, labels)
    assert params is not None
    probability, calibrated = mr._calibrated_beat_probability(0.8, params)
    assert calibrated is True
    assert probability > 0.5


def test_quantile_model_orders_interval_bounds():
    rows = []
    for index in range(140):
        rows.append({
            **_stock(100, pct_5d=float(index % 7)),
            "future_close_return_pct": float(index % 11) - 5,
        })
    lower = mr._quantile_ridge_fit_predict(rows, _stock(100), "future_close_return_pct", 0.1)
    upper = mr._quantile_ridge_fit_predict(rows, _stock(100), "future_close_return_pct", 0.9)
    assert lower is not None and upper is not None
    assert lower < upper


def test_expected_news_has_lower_surprise_than_unexpected_news():
    assert mr._event_surprise_score({
        "event_type": "revenue_growth", "title": "Revenue in line with market expectations",
    }) < mr._event_surprise_score({
        "event_type": "revenue_growth", "title": "Revenue unexpectedly beats estimates",
    })


def test_tw_intelligence_monday_window_includes_weekend():
    start, end, label = mr._tw_intelligence_window(
        dt.datetime(2026, 6, 8, 6, tzinfo=mr.TPE))
    assert start.strftime("%Y-%m-%d") == "2026-06-06"
    assert end.strftime("%Y-%m-%d") == "2026-06-08"
    assert "2026-06-06" in label and "2026-06-07" in label


def test_fetch_tw_intelligence_is_bounded_and_prioritizes_official(monkeypatch):
    class Feed:
        entries = [{
            "title": "行政院公告育兒津貼新制",
            "link": "https://www.ey.gov.tw/policy",
            "published": "Mon, 01 Jun 2026 08:00:00 GMT",
        }, {
            "title": "媒體整理育兒津貼方向",
            "link": "https://example.com/news",
            "published": "Mon, 01 Jun 2026 09:00:00 GMT",
        }]

    monkeypatch.setattr(mr.feedparser, "parse", lambda *args, **kwargs: Feed())
    out = mr.fetch_tw_daily_intelligence(
        dt.datetime(2026, 6, 2, 6, tzinfo=mr.TPE), per_kind_limit=1)
    assert len(out["policy"]) == 1
    assert out["policy"][0]["official"] is True


def test_tw_intelligence_html_marks_awareness_only():
    html = mr._render_tw_intelligence_html({
        "window": "2026-06-01 至 2026-06-01",
        "policy": [{"title": "行政院公告新制", "link": "https://gov.tw", "official": True,
                    "source_grade": "官方", "status": "已公告", "topic": "育兒社福",
                    "published": "2026-06-01 09:00"}],
        "medical": [],
    }, __import__("html"))
    assert "台灣政策昨日走向" in html
    assert "台灣醫界昨日走向" in html
    assert "不納入股價模型" in html


def test_model_forecast_exposes_version_quality_and_four_targets():
    predictions = {
        key: {"expected_return_pct": 1, "training_rows": 200,
              "model_version": mr.MODEL_VERSION, "fallback_enabled": False,
              "quantile_lower_pct": -1, "quantile_upper_pct": 2}
        for key in mr.MODEL_TARGETS
    }
    out = mr.calc_stock_price_forecast(
        _stock(100, attention_score=60), model_predictions=predictions)
    assert set(mr.MODEL_TARGETS) <= set(out)
    assert out["3d"]["quality"]["model_version"] == mr.MODEL_VERSION
    assert out["3d"]["quality"]["interval_method"] == "quantile regression"


def test_purge_recent_rows_drops_boundary_labels():
    sessions = [f"2026-06-{day:02d}" for day in range(1, 7)]
    rows = [{"future_session_date": day} for day in sessions]
    kept = mr._purge_recent_rows(rows, sessions, gap=2)
    assert [row["future_session_date"] for row in kept] == sessions[:4]


def test_walk_forward_groups_metrics_by_model_version():
    sessions = ["2026-06-01", "2026-06-02"]
    history = [{
        "session_date": sessions[0], "model_version": "v1", "taiex_close": 100,
        "stocks": {"2330": _stock(100, price_forecast={
            "1d_close": {"expected_return_pct": 1, "lower": 99, "upper": 103},
        })},
    }, {
        "session_date": sessions[1], "model_version": "v2", "taiex_close": 101,
        "stocks": {"2330": _stock(101, open=100.5)},
    }]
    out = mr.evaluate_model_walk_forward(history, sessions)
    assert out["versions"]["v1"]["1d_close"]["samples"] == 1


def test_hierarchical_event_study_shrinks_sparse_company_signal():
    study = {
        ("company", "2330", "orders", 1): {"samples": 2, "avg_excess_pct": 3},
        ("industry", "半導體", "orders", 1): {"samples": 20, "avg_excess_pct": 1},
        ("global", "", "orders", 1): {"samples": 50, "avg_excess_pct": 0.5},
    }
    impact, samples, method = mr._shrunk_event_impact(
        study, "2330", "半導體", "", "orders", 1)
    assert 0.5 < impact < 3
    assert samples == 72
    assert method == "hierarchical_event_study:company+industry+global"


def test_event_study_counts_same_event_id_once_per_stock():
    sessions = [f"2026-06-{day:02d}" for day in range(1, 8)]
    history = []
    for index, session in enumerate(sessions):
        history.append({
            "session_date": session,
            "taiex_close": 100,
            "stocks": {"2330": _stock(
                100 + index,
                news_catalysts=[{"event_id": "same", "event_type": "orders", "direction": 1}],
            )},
        })
    study = mr.build_event_study(history, sessions, horizon=1)
    assert study[("orders", 1)]["samples"] == 1


def test_probability_metrics_expose_brier_and_ece():
    out = mr._probability_calibration_metrics([(0.8, 1), (0.2, 0)])
    assert out == {
        "probability_samples": 2,
        "brier_score": 0.04,
        "ece_pct": 20.0,
    }


def test_event_timeline_only_scores_incremental_transitions():
    history = [{
        "session_date": "2026-06-01",
        "structured_events": [{
            "entity": "2330", "event_type": "orders", "lifecycle": "rumor",
        }],
    }]
    events = [{
        "entity": "2330", "event_type": "orders", "title": "台積電公告新訂單",
        "source_grade": "A",
    }, {
        "entity": "2330", "event_type": "orders", "title": "台積電公告新訂單",
        "source_grade": "A",
    }]
    out = mr.apply_event_timeline(history, events)
    assert out[0]["lifecycle"] == "confirmed"
    assert out[0]["lifecycle_weight"] == 0.65
    assert out[1]["is_incremental"] is False
    assert out[1]["lifecycle_weight"] == 0.0


def test_revenue_expectation_prefers_external_consensus_then_proxy():
    actual = {"rev": 110, "yoy_pct": 15.0, "cum_yoy_pct": 10.0}
    consensus = mr._revenue_expectation_feature(actual, {"expected_rev": 100, "source": "vendor"})
    assert consensus["rev_surprise_pct"] == pytest.approx(10)
    assert consensus["rev_expectation_method"] == "external_consensus"
    proxy = mr._revenue_expectation_feature(actual)
    assert proxy["rev_surprise_pct"] == 5.0
    assert proxy["rev_expectation_method"] == "cumulative_yoy_baseline"


def test_feature_drift_report_and_source_health_penalize_degraded_data():
    history = [{
        "session_date": f"2026-05-{day:02d}",
        "stocks": {str(code): {"pct_5d": 1.0} for code in range(100)},
    } for day in range(1, 3)]
    snapshot = [{"code": str(code), "pct_5d": 20.0} for code in range(100)]
    drift = mr.build_feature_drift_report(history, snapshot, min_history_rows=100)
    assert drift["penalty"] > 0
    assert drift["alerts"][0]["feature"] == "pct_5d"
    source = mr.build_source_health_report(snapshot, [], [])
    assert source["status"] in ("fallback", "error")
    assert source["ranking_penalty"] > 0


def test_slippage_estimate_rewards_liquid_stocks():
    assert mr._estimate_slippage_bps(5_000_000_000, 2) < mr._estimate_slippage_bps(10_000_000, 2)


def test_model_monitoring_penalizes_unreliable_probability():
    out = mr.build_model_monitoring_report({"3d": {
        "probability_samples": 100,
        "brier_score": 0.31,
        "ece_pct": 20.0,
        "interval_coverage_pct": 50.0,
    }})
    assert out["status"] == "error"
    assert out["ranking_penalty"] == 3.0


def test_walk_forward_does_not_fake_top5_for_unranked_backfill():
    sessions = ["2026-06-01", "2026-06-02"]
    history = [{
        "session_date": sessions[0], "taiex_close": 100,
        "stocks": {"2330": _stock(100, liquidity_eligible=True, slippage_bps=5)},
    }, {
        "session_date": sessions[1], "taiex_close": 101,
        "stocks": {"2330": _stock(103, liquidity_eligible=True, slippage_bps=5)},
    }]
    out = mr.evaluate_model_walk_forward(history, sessions)
    assert out["1d_close"]["top5_avg_return_pct"] is None
    assert out["1d_close"]["top5_avg_net_return_pct"] is None
