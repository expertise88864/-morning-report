import pytest

import run_quality


@pytest.mark.parametrize("label", ["polymarket_pulse", "weekend_polymarket"])
def test_pulse_failure_is_explicit_not_silenced_or_unknown(label):
    findings = run_quality.assess({"degraded_steps": [label]})
    assert label in run_quality.KNOWN_DEGRADED
    assert not any(row["code"] == "unknown_degradation" for row in findings)
    finding = next(row for row in findings if row["code"] == label)
    assert finding["severity"] == "degraded"
    assert finding["domain"] == run_quality.DOMAIN_CONTENT
    assert "Polymarket" in finding["detail"] and "缺席" in finding["detail"]
