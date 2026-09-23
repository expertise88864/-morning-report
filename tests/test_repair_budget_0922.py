"""Offline regression for the Sept22 transport-valid oversized repair."""
import morning_report as mr
import payload_budget as pb
import prompt_profiles as pp
import pytest
from reader_evidence_writing import WRITING


def test_large_but_transport_valid_repair_uses_visible_evidence():
    packet = {"news": [{"source_item_id": "n1", "title": "測試新聞",
                        "summary": "可核對原文"}], "market": {}}
    original = "x" * 610_000
    payload, rec = mr._repair_request_payload(
        {"model": "test"}, original, "\nREPAIR n1", packet,
        problems=["n1 缺少說明"])
    assert rec is not None
    assert rec["full_chars"] < pb.MAX_REQUEST_CHARS
    assert rec["slim_chars"] < rec["full_chars"]
    assert rec["slim_chars"] <= 600_000
    assert "n1" in rec["visible_ids"]
    assert "可核對原文" in payload["input"]
    assert "<UNTRUSTED_SOURCE_DATA>" in payload["input"]
    assert packet["news"][0]["summary"] == "可核對原文"


def test_small_repair_still_keeps_complete_input():
    payload, rec = mr._repair_request_payload({}, "small", "tail", {})
    assert rec is None
    assert payload["input"] == "smalltail"


def test_soft_limit_falls_back_to_format_only_when_slice_is_oversized(monkeypatch):
    monkeypatch.setattr(mr._ep, "evidence_ids", lambda p: {"n1"})
    monkeypatch.setattr(mr._ep, "evidence_snippets",
                        lambda *a, **kw: {"n1": "x" * 800_000})
    original = "x" * 610_000
    payload, rec = mr._repair_request_payload({}, original, "tail", {})
    assert rec["mode"] == "format_only"
    assert rec["slim_chars"] <= 600_000
    assert payload["input"] != original + "tail"


def test_complete_output_and_inference_instructions():
    assert "一個完整JSON物件" in pp.LUNA_DEVELOPER_INSTRUCTIONS
    assert "不刪反證" in pp.LUNA_DEVELOPER_INSTRUCTIONS
    assert "逢回加碼價" in WRITING
    assert "是否為同一資金轉移無法確認" in WRITING


@pytest.mark.parametrize("index_failure", [False, True])
def test_empty_slice_uses_format_only_below_soft_limit(monkeypatch, index_failure):
    def ids(packet):
        if index_failure:
            raise ValueError("unavailable index")
        return []
    monkeypatch.setattr(mr._ep, "evidence_ids", ids)
    monkeypatch.setattr(mr._ep, "evidence_snippets", lambda *a, **kw: {})
    original = "x" * 610_000
    payload, rec = mr._repair_request_payload({}, original, "tail", {})
    assert rec["mode"] == "format_only"
    assert rec["slim_chars"] <= 600_000
    assert rec["visible_ids"] == set()
    assert payload["input"] != original + "tail"


def test_empty_slice_over_hard_limit_still_uses_format_only(monkeypatch):
    monkeypatch.setattr(mr._ep, "evidence_snippets", lambda *a, **kw: {})
    payload, rec = mr._repair_request_payload({}, "x" * pb.MAX_REQUEST_CHARS, "tail", {})
    assert rec["mode"] == "format_only"
    assert rec["visible_ids"] == set()
    assert pb.measure_request(payload) < pb.MAX_REQUEST_CHARS
