"""Offline recovery for a completed response made of adjacent JSON objects."""

import deepseek_responses as dsr
import analysis_schema


def test_disjoint_adjacent_top_level_objects_can_be_recovered():
    text = '{"executive_summary":"已驗證"}{"key_drivers":[]}'
    recovered = dsr.json_object_from_text(text)
    assert recovered == (
        {"executive_summary": "已驗證", "key_drivers": []}, "segments")
    assert analysis_schema.validate(recovered[0], {})  # Syntax repair is not approval.

    wrapped = "說明:\n" + text + "\n以上"
    assert dsr.json_object_from_text(wrapped) == recovered
    assert dsr.json_object_from_text("說明 [附註]\n" + text) == recovered
    assert dsr.json_object_from_text("請勿輸出 `[`；以下為修正內容\n" + text) == recovered
    assert dsr.json_object_from_text("說明 [不是陣列：\n" + text + "\n]") == recovered
    assert dsr.json_object_from_text(
        '先前錯誤：[{"old":1}]\n```json\n{"a":1}{"b":2}\n```'
    ) == ({"a": 1, "b": 2}, "segments")


def test_ambiguous_or_incomplete_segments_remain_invalid():
    for text in (
        '{"a":1}{"a":2}',
        '{"a":1}說明{"b":2}',
        '{"a":1}{"b":',
        '[{"a":1},{"b":2}]',
        '[{"a":1}{"b":2}]',
        '說明:\n[{"a":1}{"b":2}]\n以上',
        '["metadata", {"a":1}{"b":2}]',
        '[{"a":1}{"b":2}, 0]',
        '[[], {"a":1}{"b":2}]',
        '["]", {"a":1}{"b":2}]',
        '請勿輸出 `[`；實際輸出：[{"a":1}{"b":2}]',
        '[1{"a":1}{"b":2}]',
        '[1{"a":1}{"b":2}',
        '[\n```json\n{"a":1}{"b":2}\n```\n]',
    ):
        assert dsr.json_object_from_text(text) == (None, "")
