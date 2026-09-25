"""CR-02 independent-review finding: structured and legacy paths share unknown semantics."""
import pytest

import analysis_render as ar
import analysis_schema as schema
import evidence_packet as ep
import fixtures_analysis as fx
import json_contract as jc
import morning_report as mr
import prompt_profiles as pp
from stance_score_alignment import align_model_score


def _packet(sp):
    return ep.build({"QQQ": {"change_pct": 2.1}, "STANCE_PY": sp}, {}, {},
                    fx.news(), [], {}, sanitize=mr._external_text)


def test_real_structured_bundle_carries_unknown_authority_contract():
    packet = _packet({})
    bundle = pp.build_luna_bundle(packet)
    assert packet["market"]["STANCE_PY"] == {}
    assert '"STANCE_PY":{}' in bundle["user_payload"]
    instructions = bundle["developer_instructions"]
    assert "stance.score 必須為" in instructions and "null" in instructions
    assert "禁止自行計算" in instructions and "資料不足" in instructions
    assert "<UNTRUSTED_SOURCE_DATA>" not in instructions
    score_schema = bundle["response_schema"]["schema"]["properties"]["stance"]["properties"]["score"]
    assert score_schema["type"] == ["integer", "null"]


def test_unknown_score_is_valid_json_and_renderable():
    obj = fx.valid_analysis()
    obj["stance"].update(score=None, label="資料不足", rationale="系統計分缺席")
    assert jc.violations(obj, schema.ANALYSIS_OUTPUT_SCHEMA) == []
    problems = schema.validate(obj, _packet({}))
    assert not any("stance.score" in p or "系統計分缺席" in p for p in problems)
    text = ar.render(obj)
    assert "立場：資料不足" in text and "淨分" not in text
    assert mr._analysis_complete_enough(text), "未知分數不可觸發無謂的截斷重試"


@pytest.mark.parametrize("score,label,sp", [
    (6, "偏多", {}), (0, "資料不足", {}), (None, "中性", {}),
    (None, "資料不足", {"total": 6, "label": "偏多"}),
])
def test_semantic_validator_rejects_fabricated_or_missing_authority(score, label, sp):
    obj = fx.valid_analysis()
    obj["stance"].update(score=score, label=label)
    assert any("stance.score" in p for p in schema.validate(obj, _packet(sp)))


@pytest.mark.parametrize("score,label", [(5, "偏多"), (6, "偏空"), (-6, "偏空")])
def test_known_python_stance_must_be_copied_exactly(score, label):
    obj = fx.valid_analysis()
    obj["stance"].update(score=score, label=label)
    problems = schema.validate(obj, _packet({"total": 6, "label": "偏多"}))
    assert any("Python 權威" in p and "不一致" in p for p in problems)
    if score != 6:
        assert any("stance.score" in p and "應為 6" in p for p in problems)
    if label != "偏多":
        assert any("stance.label" in p and "應為 偏多" in p for p in problems)


def test_oversized_stateless_repair_keeps_expected_python_stance():
    packet = _packet({"total": 6, "label": "偏多"})
    obj = fx.valid_analysis()
    obj["stance"].update(score=5, label="偏空")
    problems = schema.validate(obj, packet)
    tail = "\nREPAIR\n" + "\n".join(problems)
    payload, record = mr._repair_request_payload(
        {"model": "offline"}, "x" * 610_000, tail, packet, problems=problems)
    assert record is not None and record["mode"] in ("evidence_slice", "format_only")
    assert "應為 6" in payload["input"]
    assert "應為 偏多" in payload["input"]


def test_known_python_stance_accepts_exact_copy():
    obj = fx.valid_analysis()
    obj["stance"].update(score=6, label="偏多")
    problems = schema.validate(obj, _packet({"total": 6, "label": "偏多"}))
    assert not any("stance.score" in p or "stance.label" in p for p in problems)


@pytest.mark.parametrize("score", [5.0, 6.0, True, "6"])
def test_production_semantics_reject_non_integer_authority_copy(score):
    obj = fx.valid_analysis()
    obj["stance"].update(score=score, label="偏多")
    problems = schema.validate(obj, _packet({"total": 6, "label": "偏多"}))
    assert any("stance.score 必須為整數" in p and "應為 6" in p
               for p in problems)


@pytest.mark.parametrize("score", [None, 5, 5.0, 6.0, True, "6"])
def test_machine_owned_score_can_be_aligned_without_repair(score):
    packet = _packet({"total": 6, "label": "偏多"})
    obj = fx.valid_analysis()
    obj["stance"].update(score=score, label="偏多", rationale="以系統立場為準")
    assert align_model_score(obj, packet)
    assert obj["stance"]["score"] == 6
    assert type(obj["stance"]["score"]) is int
    assert not any("stance.score" in p for p in schema.validate(obj, packet))
    assert not align_model_score(obj, packet)


@pytest.mark.parametrize("rationale", ["今日淨分 +5", "系統分數：5", "net score=-2",
                                       "綜合得分為 5", "今日給 5 分", "11 維觀察"])
def test_wrong_score_in_public_prose_still_requires_repair(rationale):
    packet = _packet({"total": 6, "label": "偏多"})
    obj = fx.valid_analysis()
    obj["stance"].update(score=5, label="偏多", rationale=rationale)
    assert not align_model_score(obj, packet)
    assert any("stance.score" in p for p in schema.validate(obj, packet))


def test_matching_explicit_score_claim_can_be_aligned():
    packet = _packet({"total": 6, "label": "偏多"})
    obj = fx.valid_analysis()
    obj["stance"].update(score=5, label="偏多", rationale="今日淨分 +6")
    assert align_model_score(obj, packet)
    assert obj["stance"]["score"] == 6
    assert not any("stance.score" in p for p in schema.validate(obj, packet))


def test_score_cue_in_summary_prevents_hidden_numeric_contradiction():
    packet = _packet({"total": 6, "label": "偏多"})
    obj = fx.valid_analysis()
    obj["stance"].update(score=5, label="偏多", rationale="以系統立場為準")
    obj["executive_summary"] = "今日評分是 5，仍有不確定性"
    assert not align_model_score(obj, packet)
    assert any("stance.score" in p for p in schema.validate(obj, packet))


@pytest.mark.parametrize("claim", ["6.5", "6,000", "6%", "6/10"])
def test_partial_numeric_claim_is_never_accepted_as_matching_score(claim):
    packet = _packet({"total": 6, "label": "偏多"})
    obj = fx.valid_analysis()
    obj["stance"].update(score=5, label="偏多", rationale="以系統立場為準")
    obj["executive_summary"] = f"今日評分是 {claim}"
    assert not align_model_score(obj, packet)
    assert any("stance.score" in p for p in schema.validate(obj, packet))


def test_wrong_direction_still_requires_repair():
    packet = _packet({"total": 6, "label": "偏多"})
    obj = fx.valid_analysis()
    obj["stance"].update(score=5, label="偏空")
    assert not align_model_score(obj, packet)
    problems = schema.validate(obj, packet)
    assert any("stance.score" in p for p in problems)
    assert any("stance.label" in p for p in problems)


@pytest.mark.parametrize("value", [False, True, "6", 2.5, [], {}, float("nan"),
                                  float("inf"), -12, 12])
def test_nullable_score_does_not_weaken_shape_or_range_checks(value):
    obj = fx.valid_analysis()
    obj["stance"]["score"] = value
    assert any(p.startswith("stance.score:") for p in jc.violations(obj, schema.ANALYSIS_OUTPUT_SCHEMA))


@pytest.mark.parametrize("value", [None, -11, 0, 11])
def test_nullable_score_accepts_only_supported_values(value):
    field = schema.ANALYSIS_OUTPUT_SCHEMA["properties"]["stance"]["properties"]["score"]
    assert jc.violations(value, field) == []


def test_type_union_still_checks_object_children_and_array_items():
    field = {"type": ["object", "null"], "properties": {"n": {"type": "integer"}},
             "required": ["n"], "additionalProperties": False}
    assert jc.violations(None, field) == []
    assert jc.violations({}, field) and jc.violations({"n": "wrong"}, field)
    assert jc.violations(["wrong"], {"type": ["array", "null"], "items": {"type": "integer"}})


def test_unknown_union_types_fail_closed():
    with pytest.raises(NotImplementedError):
        jc.violations(None, {"type": ["null", "imaginary"]})
