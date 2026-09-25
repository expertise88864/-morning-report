"""Conservative checks for numeric score claims in model-authored prose."""

import re
from collections.abc import Iterator
from typing import Any


_CUE = re.compile(r"淨分|總分|得分|評分|分數|score|\d+\s*分(?!鐘|钟)", re.IGNORECASE)
_CLAIM = re.compile(
    r"(?P<cue>淨分|總分|得分|評分|分數|score)\s*"
    r"(?:[:：=]|是|為|約|约|達|达)?\s*(?P<value>[+\-＋－]?\d+)"
    r"(?![\d０-９.,/／%％分點点])",
    re.IGNORECASE,
)
_NUMERAL = re.compile(r"[0-9０-９一二三四五六七八九十百千]")


def _texts(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


def claims_are_consistent(obj: dict[str, Any], rationale: str, total: int) -> bool:
    """Allow only explicit matching score claims; unknown wording needs repair."""
    for text in _texts(obj):
        claims = list(_CLAIM.finditer(text))
        for cue in _CUE.finditer(text):
            claim = next((c for c in claims if c.start("cue") == cue.start()), None)
            if claim is None:
                return False
            raw = claim.group("value").replace("＋", "+").replace("－", "-")
            if int(raw) != total:
                return False
    claims = list(_CLAIM.finditer(rationale))
    for numeral in _NUMERAL.finditer(rationale):
        if not any(c.start("value") <= numeral.start() < c.end("value") for c in claims):
            return False
    return True
