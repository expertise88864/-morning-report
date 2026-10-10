"""Offline regression for the October 10 truncated structured report."""
import pytest

import llm_telemetry as lt


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "max"])
def test_structured_budget_reserves_room_for_complete_json(effort):
    cap = lt.structured_output_cap(effort, 7000, model="deepseek-v4-flash")
    assert cap >= 55_345 + 32_768
    assert cap <= lt.max_output_for("deepseek-v4-flash")[0]


def test_larger_configuration_is_preserved():
    assert lt.structured_output_cap("high", 12000, model="deepseek-v4-flash") == 120000


def test_unknown_provider_ceiling_is_not_bypassed():
    assert lt.structured_output_cap("high", 7000, model="unknown") == lt.UNKNOWN_MODEL_MAX_OUTPUT


def test_legacy_prose_budget_is_unchanged():
    assert lt.output_cap("high", 7000, model="deepseek-v4-flash") == 70000
