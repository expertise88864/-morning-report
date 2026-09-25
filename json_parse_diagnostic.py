"""Content-free shape diagnostics for rejected model JSON.

The run manifest is persisted in a public repository. Never copy response
text, keys, or values into this diagnostic; counts and closed enum labels only.
"""

from __future__ import annotations

import json


def _kind(value: object) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    return "number"


def _hint(char: str) -> str:
    if char == "{":
        return "object"
    if char == "[":
        return "array"
    if char == "`":
        return "fence"
    return "other"


def describe(text: str | None) -> dict[str, int | str]:
    """Report only the structure of a rejected response, never its content.

    This is diagnostic only: it neither chooses between drafts nor changes
    parsing, schema validation, evidence checks, or retry policy.
    """
    raw = str(text or "")
    result: dict[str, int | str] = {"chars": len(raw)}
    start = len(raw) - len(raw.lstrip())
    if start == len(raw):
        return {**result, "shape": "empty"}
    decoder = json.JSONDecoder()
    try:
        first, end = decoder.raw_decode(raw, start)
    except (ValueError, RecursionError):
        return {**result, "shape": "invalid_prefix"}
    result.update(shape="single_json", first_kind=_kind(first), first_end=end)
    cursor = end + len(raw[end:]) - len(raw[end:].lstrip())
    if cursor == len(raw):
        return result
    result.update(shape="extra_data", extra_chars=len(raw) - cursor,
                  next_hint=_hint(raw[cursor]))
    try:
        second, second_end = decoder.raw_decode(raw, cursor)
    except (ValueError, RecursionError):
        result["second_parse"] = "invalid"
        return result
    result.update(second_parse="valid", second_kind=_kind(second),
                  remaining_chars=len(raw[second_end:].strip()))
    if isinstance(first, dict) and isinstance(second, dict):
        result["overlapping_keys"] = len(first.keys() & second.keys())
    return result


def record(slot: dict, text: str | None, error: Exception,
           recovered_by: str) -> None:
    """Keep each parse failure in order; retain the latest-key compatibility."""
    item = {"error": f"{type(error).__name__}: {error}"[:80],
            "shape": describe(text), "recovered_by": recovered_by}
    slot["primary_parse_error"] = item
    slot.setdefault("primary_parse_errors", []).append(item)
