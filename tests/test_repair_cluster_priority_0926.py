"""Offline regression for stateless repairs of production cluster failures."""

import json

import evidence_packet as ep
import llm_postprocess as lp
import morning_report as mr
import payload_budget as pb
import repair_cluster_members as rc


def _packet():
    news = [
        {"source_item_id": f"n{i:03d}", "title": f"新聞{i}",
         "summary": f"事件內容{i}", "published": "2026-09-25T21:00:00Z",
         "source_name": "來源"}
        for i in range(100)
    ]
    return {
        "news": news,
        "news_clusters": {"clusters": [
            {"cluster_id": "cluster:n90", "representative_source_id": "n091",
             "member_source_ids": ["n090", "n091"]},
            {"cluster_id": "cluster:n900", "member_source_ids": ["n092"]},
        ]},
    }


def test_only_exact_named_cluster_members_are_prioritized():
    packet = _packet()
    legal = {n["source_item_id"] for n in packet["news"]}
    problems = ["深入主題 cluster:n90 漏寫 top_news_analysis"]
    assert rc.problem_cluster_members(packet, problems, legal) == ["n091", "n090"]
    assert rc.problem_cluster_members(packet, ["cluster:n9"], legal) == []
    assert rc.problem_cluster_members({}, problems, legal) == []
    assert rc.problem_cluster_members({"news_clusters": []}, problems, legal) == []
    packet["news_clusters"]["clusters"][0]["member_source_ids"] = "n090"
    assert rc.problem_cluster_members(packet, problems, legal) == []


def test_tuple_members_follow_the_same_packet_backed_selection():
    packet = _packet()
    packet["news_clusters"]["clusters"][0]["member_source_ids"] = ("n090", "n091")
    legal = {n["source_item_id"] for n in packet["news"]}
    assert rc.problem_cluster_members(packet, ["cluster:n90 漏寫"], legal) == [
        "n091", "n090"]


def test_slim_repair_sees_named_cluster_content_before_prior_citations(monkeypatch):
    packet = _packet()
    legal = {n["source_item_id"] for n in packet["news"]}
    monkeypatch.setattr(mr._ep, "evidence_ids", lambda _: legal)
    previous = json.dumps({"cited": [f"n{i:03d}" for i in range(90)]})
    problems = ["深入主題 cluster:n90 漏寫 top_news_analysis"]
    tail = lp.repair_instruction(problems, [], previous_json=previous)
    output, record = mr._repair_request_payload(
        {"model": "m"}, "x" * pb.MAX_REQUEST_CHARS, tail, packet,
        problems=problems, hints=[])
    assert record["mode"] == "evidence_slice"
    assert record["evidence_items"] <= mr._REPAIR_SLICE_MAX
    assert {"n090", "n091"} <= record["visible_ids"]
    assert "事件內容90" in output["input"]
    assert "事件內容91" in output["input"]
    assert "2026-09-25T21:00:00Z" in output["input"]
    assert pb.request_gate(output) <= pb.MAX_REQUEST_CHARS


def test_news_snippet_carries_published_time_without_inventing_one():
    packet = _packet()
    snippet = ep.evidence_snippets(packet, ["n090"], budget_chars=10000)["n090"]
    assert snippet["published"] == "2026-09-25T21:00:00Z"
    packet["news"][90].pop("published")
    snippet = ep.evidence_snippets(packet, ["n090"], budget_chars=10000)["n090"]
    assert snippet["published"] == ""


def test_unseen_warning_only_names_sources_actually_cited_by_prior_output(monkeypatch):
    packet = _packet()
    legal = {n["source_item_id"] for n in packet["news"]}
    monkeypatch.setattr(mr._ep, "evidence_ids", lambda _: legal)
    monkeypatch.setattr(mr, "_REPAIR_SLICE_MAX", 1)
    problems = ["深入主題 cluster:n90 漏寫 top_news_analysis"]
    tail = lp.repair_instruction(problems, [], previous_json=json.dumps({"cited": ["n000"]}))
    output, record = mr._repair_request_payload(
        {"model": "offline"}, "x" * pb.MAX_REQUEST_CHARS, tail, packet,
        problems=problems)
    assert record["visible_ids"] == {"n091"}
    assert record["unseen_cited_ids"] == 1
    warning = output["input"].split("【本輪看不到內容的證據 ID(前一版引用過)】", 1)[1].split(" —— ", 1)[0]
    assert "n000" in warning and "n090" not in warning


def test_large_named_clusters_keep_representatives_without_evicting_prior_evidence(monkeypatch):
    packet = _packet()
    first = packet["news_clusters"]["clusters"][0]
    second = packet["news_clusters"]["clusters"][1]
    first["member_source_ids"] = [f"n{i:03d}" for i in range(80, 95)]
    first["representative_source_id"] = "n094"
    packet["news"][91]["official"] = True
    second["member_source_ids"] = [f"n{i:03d}" for i in range(95, 100)]
    second["representative_source_id"] = "n099"
    packet["news"][98]["official"] = True
    legal = {n["source_item_id"] for n in packet["news"]}
    problems = ["cluster:n90 未交代", "cluster:n900 未交代"]
    assert rc.problem_cluster_members(packet, problems, legal) == [
        "n094", "n080", "n091", "n099", "n095", "n098"]
    monkeypatch.setattr(mr._ep, "evidence_ids", lambda _: legal)
    previous = json.dumps({"cited": [f"n{i:03d}" for i in range(80)]})
    tail = lp.repair_instruction(problems, [], previous_json=previous)
    _, record = mr._repair_request_payload(
        {"model": "offline"}, "x" * pb.MAX_REQUEST_CHARS, tail, packet,
        problems=problems, hints=[])
    assert record["mode"] == "evidence_slice"
    assert record["evidence_items"] <= mr._REPAIR_SLICE_MAX
    assert {"n094", "n080", "n091", "n099", "n095", "n098", "n070"} <= record["visible_ids"]


def test_many_reported_clusters_each_keep_a_first_evidence_item(monkeypatch):
    news = [{"source_item_id": f"n{i:03d}", "title": f"事件{i}",
             "summary": f"可核對的內容{i}"} for i in range(120)]
    for i in range(40):
        news[3*i+1]["official"] = True
    clusters = [{"cluster_id": f"cluster:c{i:02d}",
                 "representative_source_id": f"n{3*i+2:03d}",
                 "member_source_ids": [f"n{3*i+j:03d}" for j in range(3)]}
                for i in range(40)]
    packet = {"news": news, "news_clusters": {"clusters": clusters}}
    legal = {row["source_item_id"] for row in news}
    monkeypatch.setattr(mr._ep, "evidence_ids", lambda _: legal)
    problems = [f"cluster:c{i:02d} 未交代" for i in range(40)]
    tail = lp.repair_instruction(problems, [])
    _, record = mr._repair_request_payload(
        {"model": "offline"}, "x" * pb.MAX_REQUEST_CHARS, tail, packet,
        problems=problems)
    assert record["mode"] == "evidence_slice"
    assert record["evidence_items"] <= mr._REPAIR_SLICE_MAX
    assert {f"n{3*i+2:03d}" for i in range(40)} <= record["visible_ids"]
