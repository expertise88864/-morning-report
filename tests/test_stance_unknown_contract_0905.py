"""CR-02 independent-review finding: structured and legacy paths share unknown semantics."""
import pytest

import analysis_render as ar
import analysis_schema as schema
import evidence_packet as ep
import fixtures_analysis as fx
import json_contract as jc
import morning_report as mr
import prompt_profiles as pp


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
