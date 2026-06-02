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
    assert out["method"] == "standardized ridge"
    assert 0.05 <= out["beat_market_probability"] <= 0.95
    assert -12 <= out["expected_return_pct"] <= 12


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
    sessions = [f"2026-06-{day:02d}" for day in range(1, 8)]
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
    assert scored["2330"]["evidence"][0]["score_method"] == "event_study"


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
