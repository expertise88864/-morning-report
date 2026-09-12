"""Protect analysis evidence when an oversized packet needs compaction."""
from copy import deepcopy

import payload_compact as pc


def _rows():
    return [{"code": str(1000 + i), "name": f"公司{i}",
             "ranking_score": 100 - i, "eps": 3,
             "detail": "資料" * 100} for i in range(25)]


def test_company_named_without_ticker_retains_full_detail():
    rows = _rows()
    rows[-1]["name"] = "台積電"
    packet = {"tw_universe": rows, "news": [{"title": "台積電公布營收"}]}
    before = deepcopy(packet)
    out, report = pc.compact(packet, limit=1)
    assert out["tw_universe"][-1] == rows[-1]
    assert "eps" not in out["tw_universe"][-2]
    assert report["over_budget"] is True
    assert packet == before


def test_ticker_embedded_in_larger_number_is_not_a_company_mention():
    rows = _rows()
    packet = {"tw_universe": rows, "news": [{"title": "金額 910249 元"}]}
    assert "1024" not in pc._analyzed_codes(packet)
    packet["news"][0]["title"] = "公司（1024.TW）公布營收"
    assert "1024" in pc._analyzed_codes(packet)


def test_entity_name_without_ticker_also_retains_detail():
    rows = _rows()
    rows[-1]["name"] = "台積電"
    packet = {"tw_universe": rows,
              "news": [{"title": "營收公告", "entities": ["台積電"]}]}
    assert "1024" in pc._analyzed_codes(packet)


def test_macro_release_outside_top_and_required_lists_keeps_its_summary():
    summary = "發布值與預期值、發布時間及前值修訂。" * 40
    packet = {
        "news": [{"source_item_id": sid, "summary": summary}
                 for sid in ("macro", "other")],
        "news_clusters": {"required_cluster_ids": [], "clusters": [
            {"cluster_id": "cpi", "member_source_ids": ["macro"]},
            {"cluster_id": "other", "member_source_ids": ["other"]}]},
        "top_events": {"top_cluster_ids": []},
        "event_graph": {"macro_release_cluster_ids": ["cpi"]},
    }
    before = deepcopy(packet)
    out, report = pc.compact(packet, limit=1)
    assert out["news"][0]["summary"] == summary
    assert len(out["news"][1]["summary"]) == pc.COMPACT_SUMMARY_CHARS
    assert [n["source_item_id"] for n in out["news"]] == ["macro", "other"]
    assert report["over_budget"] is True
    assert packet == before
