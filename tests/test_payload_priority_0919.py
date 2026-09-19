"""Repetitive model metadata must not evict usable historical evidence."""
from copy import deepcopy

import payload_budget as pb
import payload_compact as pc


def test_plumbing_is_removed_before_history():
    packet = {
        "market": {"HISTORY": [{"date": "2026-09-18", "text": "昨日證據" * 100}]},
        "tw_universe": [{"code": "2330", "name": "台積電", "price_forecast": {
            "training_rows": "x" * pb.MAX_PAYLOAD_CHARS,
            "one_day": {"expected_price": 100, "lower": 90, "upper": 110}}}],
        "news": [{"source_item_id": "n1", "summary": "完整新聞" * 100}],
    }
    original = deepcopy(packet)
    manifest = {}
    out = pb.apply(packet, manifest)
    assert out["market"]["HISTORY"] == original["market"]["HISTORY"]
    assert out["news"] == original["news"]
    assert out["tw_universe"][0]["price_forecast"]["one_day"]["expected_price"] == 100
    assert packet == original
    assert manifest["llm"]["payload_budget"]["trimmed"] == []
    assert manifest["llm"]["payload_compact"]["applied"]
    assert manifest["llm"]["payload_budget"]["chars_after"] == pb._size(out)


def test_first_pass_does_not_shorten_news_or_remove_stock_evidence():
    packet = {"news": [{"source_item_id": "n1", "summary": "新聞" * 500}],
              "tw_universe": [{"code": str(n), "ranking_score": n, "detail": "證據"}
                              for n in range(30)]}
    out, report = pc.compact(packet, limit=1, plumbing_only=True)
    assert out == packet
    assert report["over_budget"] is True


def test_forecast_outputs_and_errors_survive_pretrim():
    forecast = {"expected_price": 100, "beat_market_probability": .6,
                "interval_pct": 4.5, "conformal_adj_pct": .7, "lower": 90,
                "upper": 110, "fallback_enabled": True}
    packet = {"market": {"HISTORY": "x" * pb.MAX_PAYLOAD_CHARS},
              "tw_universe": [{"code": "2330", "price_forecast": {"3d": forecast}},
                              {"code": "2317", "price_forecast": {
                                  "error": "資料不足", "3d": {"error": "缺價格"}}}]}
    out = pb.apply(packet, {})
    assert out["tw_universe"] == packet["tw_universe"]
