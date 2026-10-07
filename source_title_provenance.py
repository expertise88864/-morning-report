"""Bind Python's emergency source lines, not their truth, without storing titles."""

import hashlib
import re

from analysis_origin import EMERGENCY_FALLBACK
from reader_price_causality_guard import _SOURCE_LIST_HEADLINE

_KEY = "emergency_source_title_provenance"
_BASIS = "emergency-source-title-lines.v1"


def register_emergency_titles(top_news: str, manifest: object) -> None:
    """Called only with the actual bounded news-list block built by Python."""
    llm = manifest.get("llm") if isinstance(manifest, dict) else None
    if not isinstance(llm, dict) or llm.get("analysis_origin") != EMERGENCY_FALLBACK:
        return
    lines = top_news.splitlines() if isinstance(top_news, str) else []
    hashes = [hashlib.sha256(line.encode("utf-8")).hexdigest()
              for line in lines if _SOURCE_LIST_HEADLINE.match(line)]
    llm[_KEY] = {"basis": _BASIS, "line_sha256": hashes if len(hashes) <= 20 else []}


def is_registered_emergency_title(line: str, manifest: object) -> bool:
    """Fail closed for other origins, absent/stale metadata or altered lines."""
    llm = manifest.get("llm") if isinstance(manifest, dict) else None
    if (not isinstance(line, str) or not isinstance(llm, dict)
            or not _SOURCE_LIST_HEADLINE.match(line)):
        return False
    record = llm.get(_KEY)
    if (llm.get("analysis_origin") != EMERGENCY_FALLBACK
            or not isinstance(record, dict) or record.get("basis") != _BASIS):
        return False
    hashes = record.get("line_sha256")
    if (not isinstance(hashes, list) or len(hashes) > 20
            or not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in hashes)):
        return False
    return hashlib.sha256(line.rstrip("\r\n").encode("utf-8")).hexdigest() in hashes
