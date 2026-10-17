"""2026-10-16/17 production: a stray `}` ended the root object after ~3.8K chars.

Three rounds in a row (including a syntax repair round) had the same shape, so the
repair is deterministic and deliberately narrow; validation still judges content.
"""
import json

import analysis_schema
import deepseek_responses as dsr
import json_stray_brace as sb

GOOD = {"executive_summary": "台積電財報優於預期", "key_drivers": [{"a": 1}],
        "top_news_analysis": [{"source_item_id": "n1", "why_it_matters": "x"}]}


def _with_stray_brace(obj: dict) -> str:
    text = json.dumps(obj, ensure_ascii=False)
    cut = text.index(', "key_drivers"')
    return text[:cut] + "}" + text[cut:]          # root closes early, rest follows


def test_the_production_shape_is_recovered_whole():
    broken = _with_stray_brace(GOOD)
    try:
        json.loads(broken)
        raise AssertionError("fixture must reproduce the production JSONDecodeError")
    except json.JSONDecodeError as exc:
        assert "Extra data" in str(exc)
    assert dsr.json_object_from_text(broken) == (GOOD, sb.HOW)
    assert analysis_schema.validate(GOOD, {})  # Syntax recovery is not approval.


def test_any_other_broken_shape_stays_a_failure():
    misplaced = json.dumps(GOOD, ensure_ascii=False).replace('"a": 1}', '"a": 1}}', 1)
    for text in (
        misplaced,                                   # stray brace not at the root boundary
        '{"a":1}"b":2}',                             # no comma: not the same object
        '{"a":1},"b":',                              # truncated remainder
        '{"a":1}, {"b":2}',                          # second object, not the same root
        '{"a":1},"b":2}{"c":3}',                     # extra data after the repair
        '[{"a":1},"b":2}]',
        '說明 {"a":1},"b":2}',                        # prose before the object
    ):
        assert sb.repair(text) is None, text
        # Pre-existing recoveries may still apply to some of these; this one must not.
        assert dsr.json_object_from_text(text)[1] != sb.HOW, text
    assert dsr.json_object_from_text(misplaced) == (None, "")


def test_valid_json_never_reaches_the_repair():
    text = json.dumps(GOOD, ensure_ascii=False)
    assert dsr.json_object_from_text(text) == (GOOD, "raw")
