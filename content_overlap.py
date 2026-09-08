"""Conservative display-only overlap checks, not semantic truth judgments.

No model calls or persisted text. Different numbers and explicit qualifications
are never collapsed. Topic/entity overlap alone is not duplicate evidence.
"""
from __future__ import annotations

from html import unescape
import re
import unicodedata

def plain(text: str) -> str:
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', str(text or ''))
    text = re.sub(r'</?(?:p|div|span|b|strong|em|i|a|h[1-6]|ul|li|ol|br)\b[^>]*>', ' ', text, flags=re.I)
    return unicodedata.normalize('NFKC', unescape(text)).replace('−', '-')


def signature(text: str) -> str:
    # Keep word boundaries, signs, units/currency and decimal separators.
    return '|'.join(re.findall(r'[^\W\d_]+|\d+(?:[.,]\d+)*|[%$€£¥+<>?!-]', plain(text).lower()))


def duplicate(left: str, right: str) -> bool:
    sa, sb = signature(left), signature(right)
    return bool(sa and sb and sa == sb)


def sentences(text: str) -> list[str]:
    # Keep decimal points; punctuation is not evidence in this display comparison.
    return [s for s in _parts(text) if len(signature(s)) >= 12][:1200]


def _parts(text: str) -> list[str]:
    return [re.sub(r'^\s*(?:[-*#]+\s+)+', '', s).strip()
            for s in re.split(r'(?<=[。！？!?])|\n+', plain(text)) if signature(s)]


def covered(text: str, references: list[str]) -> bool:
    return covered_keys(text, {signature(r) for r in references})


def covered_keys(text: str, keys: set[str]) -> bool:
    parts = _parts(text)
    # A short qualification is still meaning, not disposable punctuation.
    if any(len(signature(p)) < 12 for p in parts):
        return bool(signature(text)) and signature(text) in keys
    return bool(parts) and all(signature(p) in keys for p in parts)
