"""Near-limit input shrinks only model-only non-analysis stock details."""
from copy import deepcopy

import payload_budget
import payload_compact


def _packet():
    rows = [
        {"code": str(1000 + i), "name": f"Company{i}",
         "industry": "electronics", "close": 100 + i,
         "ranking_score": 100 - i, "daily_evidence": "x" * 400}
        for i in range(25)
    ]
    return {"tw_universe": rows,
            "news": [{"title": "Chip sector update", "entities": [],
                      "summary": "Company24 announces a result; keep the complete summary."}],
            "market": {"QQQ": 1.2}}


def test_soft_skeleton_preserves_all_codes_and_named_news_stock():
    packet = _packet()
    original = deepcopy(packet)
    out, report = payload_compact.compact(
        packet, limit=1_000_000, soft_skeleton_limit=2_000)
    assert not report["over_budget"]
    assert any(a["tier"] == "universe.non_analyzed_to_skeleton"
               for a in report["applied"])
    assert [r["code"] for r in out["tw_universe"]] == [r["code"] for r in original["tw_universe"]]
    assert "daily_evidence" in out["tw_universe"][0]  # ranked top 20
    assert "daily_evidence" in out["tw_universe"][24]  # named only in summary
    assert "daily_evidence" not in out["tw_universe"][23]
    assert out["news"] == original["news"]
    assert packet == original


def test_soft_limit_does_not_trigger_when_packet_is_small():
    packet = _packet()
    out, report = payload_compact.compact(
        packet, limit=1_000_000, soft_skeleton_limit=1_000_000)
    assert out == packet
    assert report["applied"] == []


def test_fulltext_only_company_keeps_universe_evidence():
    packet = _packet()
    packet["news"][0]["summary"] = "Chip sector update"
    packet["news"][0]["fulltext"] = "Company24 confirms a material result."
    out, report = payload_compact.compact(
        packet, limit=1_000_000, soft_skeleton_limit=2_000)
    assert "daily_evidence" in out["tw_universe"][24]
    assert "daily_evidence" not in out["tw_universe"][23]
    assert out["news"] == packet["news"]
    assert any(a["tier"] == "universe.non_analyzed_to_skeleton"
               for a in report["applied"])


def test_retained_finance_headline_company_keeps_universe_evidence():
    packet = _packet()
    packet["news"][0]["summary"] = "General finance update"
    packet["news"][0]["finance_headlines"] = [
        {"title": "Company24 publishes earnings", "published": "2026-09-24",
         "source": "Independent wire"}]
    out, _ = payload_compact.compact(
        packet, limit=1_000_000, soft_skeleton_limit=2_000)
    assert "daily_evidence" in out["tw_universe"][24]
    assert "daily_evidence" not in out["tw_universe"][23]
    assert out["news"] == packet["news"]


def test_budget_entrypoint_records_soft_compaction_without_hard_failure(monkeypatch):
    monkeypatch.setattr(payload_budget, "SOFT_UNIVERSE_CHARS", 2_000)
    packet = _packet()
    manifest = {}
    out = payload_budget.apply(packet, manifest)
    assert len(out["tw_universe"]) == len(packet["tw_universe"])
    assert "daily_evidence" not in out["tw_universe"][23]
    assert out["news"] == packet["news"]
    assert manifest["llm"]["payload_budget"]["over_budget"] is False
    assert any(a["tier"] == "universe.non_analyzed_to_skeleton"
               for a in manifest["llm"]["payload_compact"]["applied"])
