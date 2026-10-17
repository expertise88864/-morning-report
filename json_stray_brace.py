"""One stray closing brace that ends the root object early; nothing broader.

2026-10-16 repair round, 10-17 first and repair rounds: DeepSeek returned the
same shape three times (~58K chars, a ~3.8K root object, then the rest of that
object starting with ``,``). A syntax repair round reproduced it, so more time
does not help. Remove only the brace that closed the root, and only when the
whole text then parses as one object; a stray brace anywhere else still fails
to parse and stays a failure. The caller's full schema/evidence validation
still decides whether the content is usable.
"""
from __future__ import annotations

import json

HOW = "stray_close_brace"


def repair(text) -> tuple[dict, str] | None:
    raw = str(text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        _first, end = json.JSONDecoder().raw_decode(raw)
        if raw[end - 1] != "}" or not raw[end:].lstrip().startswith(","):
            return None
        obj = json.loads(raw[:end - 1] + raw[end:])
    except (ValueError, RecursionError):
        return None
    return (obj, HOW) if isinstance(obj, dict) else None
