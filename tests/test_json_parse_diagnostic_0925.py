"""Malformed-response diagnostics do not persist model/source text."""

import json

from json_parse_diagnostic import describe


def test_extra_data_classifies_complete_disjoint_and_conflicting_objects():
    first = json.dumps({"secret-heading": "私人內容"})
    disjoint = first + json.dumps({"other-heading": 1})
    conflict = first + json.dumps({"secret-heading": "另一版"})

    clean = describe(disjoint)
    assert clean["shape"] == "extra_data"
    assert clean["first_kind"] == "object"
    assert clean["second_parse"] == "valid"
    assert clean["overlapping_keys"] == 0
    assert clean["remaining_chars"] == 0
    assert describe(conflict)["overlapping_keys"] == 1
    assert "私人內容" not in json.dumps(clean, ensure_ascii=False)
    assert "secret-heading" not in json.dumps(clean)


def test_extra_data_distinguishes_partial_json_and_prose_without_salvage():
    first = json.dumps({"a": 1})
    partial = describe(first + '{"b":')
    prose = describe(first + " 修正說明")
    assert partial["shape"] == prose["shape"] == "extra_data"
    assert partial["next_hint"] == "object"
    assert prose["next_hint"] == "other"
    assert partial["second_parse"] == prose["second_parse"] == "invalid"
    assert describe(first)["shape"] == "single_json"
    assert describe("   ")["shape"] == "empty"
    assert describe('{"a":')["shape"] == "invalid_prefix"


def test_large_private_value_changes_only_counts_not_reported_content():
    private = "TOP_SECRET_STOCK_123456" * 1000
    diagnostic = describe(json.dumps({"x": private}) + " trailing text")
    encoded = json.dumps(diagnostic)
    assert diagnostic["chars"] > len(private)
    assert diagnostic["shape"] == "extra_data"
    assert private[:20] not in encoded
    assert "trailing text" not in encoded
