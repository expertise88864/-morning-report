"""The Sunday/legacy transport must send the configured Flash thinking controls.

Contract: https://api-docs.deepseek.com/guides/thinking_mode/ (2026-09-13).
No provider calls: intercept requests.post, not just the telemetry helper.
"""
from unittest.mock import Mock

import pytest

import morning_report as mr


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
@pytest.mark.parametrize("effort,mode,sent", [
    ("max", "enabled", "max"),
    ("high", "enabled", "high"),
    ("low", "enabled", "low"),
    ("off", "disabled", None),
])
def test_legacy_transport_sends_thinking_controls(monkeypatch, model, effort, mode, sent):
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "offline-fixture")
    monkeypatch.setattr(mr, "DEEPSEEK_MODEL", model)
    monkeypatch.setattr(mr, "DEEPSEEK_REASONING_EFFORT", effort)
    response = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": "fixture"}, "finish_reason": "stop"}],
        "usage": {},
    }
    post = Mock(return_value=response)
    record = Mock()
    monkeypatch.setattr(mr.requests, "post", post)
    monkeypatch.setattr(mr, "_record_llm_call", record)
    assert mr._call_deepseek("offline fixture") == "fixture"
    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == model
    assert payload["thinking"] == {"type": mode}
    assert payload.get("reasoning_effort") == sent
    assert record.call_args.kwargs["applied_effort"] == (sent or "")
