"""Fair ordering of packet-backed evidence for multi-problem repairs."""
from __future__ import annotations


def interleave_distinct(groups: list[list[str]]) -> list[str]:
    """Give each displayed problem one source before its second source.

    This only changes selection order. The caller's item and character limits,
    evidence visibility checks, and semantic validation remain authoritative.
    """
    out: list[str] = []
    seen: set[str] = set()
    for offset in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if offset < len(group) and group[offset] not in seen:
                out.append(group[offset])
                seen.add(group[offset])
    return out
