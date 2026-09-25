"""Conservative recovery of adjacent, complete top-level JSON objects."""

import json
import re


def inside_array_before(text: str, offset: int) -> bool:
    """Whether a JSON-array prefix structurally encloses the first object.

    Brackets in JSON strings are text. A stray ``[`` in prose is not an array
    unless its prefix is composed of JSON-array lexical tokens.
    """
    stack: list[int] = []
    quoted = False
    escaped = False
    for pos, ch in enumerate(text[:offset]):
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch == "[":
            stack.append(pos)
        elif ch == "]" and stack:
            stack.pop()
    if not stack or quoted:
        return False
    token_prefix = r'(?:\s|[\[\]{},:]|"(?:\\.|[^"\\])*"|[-+0-9.eE]|true|false|null)*'
    return any(re.fullmatch(token_prefix, text[start + 1:offset])
               for start in stack)


def merge_disjoint_objects(text: str) -> dict | None:
    """Merge only a pure sequence of nonempty objects with unique keys.

    This repairs JSON section chunks without choosing between conflicting
    drafts or inventing missing content. The caller still validates the full
    output schema and every evidence reference.
    """
    body = str(text or "").strip()
    if not body.startswith("{"):
        return None
    decoder = json.JSONDecoder()
    merged: dict = {}
    count = 0
    cursor = 0
    while cursor < len(body) and count < 32:
        try:
            item, cursor = decoder.raw_decode(body, cursor)
        except ValueError:
            return None
        if not isinstance(item, dict) or not item or merged.keys() & item.keys():
            return None
        merged.update(item)
        count += 1
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
    return merged if count >= 2 and cursor == len(body) else None


def has_trailing_object_start(text: str) -> bool:
    """Do not accept a valid prefix when another object starts but breaks."""
    body = str(text or "")
    start = body.find("{")
    if start < 0:
        return False
    try:
        _first, end = json.JSONDecoder().raw_decode(body, start)
    except ValueError:
        return False
    return "{" in body[end:]
