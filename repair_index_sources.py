"""Resolve index-only news failures to packet-backed repair evidence IDs."""
from __future__ import annotations

import json
import re


_INDEX = re.compile(r"^\s*top_news_analysis\[([0-9]{1,6})\]")


def problem_sources(packet: dict, problems: list, previous_block: str,
                    legal_ids: set) -> list[str]:
    """Prioritize the rejected row without inventing source/citation mappings.

    The caller passes the PREVIOUS_OUTPUT block up to its closing fence.
    Parse only a complete JSON object; never salvage partial output or infer
    article identity from prose. Historical candidates come from the packet's
    current-source mapping, not from the rejected model's historical citation.
    Selection is not factual validation; the existing snippet/claim gates stay.
    """
    _, fence, body = previous_block.partition("<UNTRUSTED_SOURCE_DATA>")
    if not fence:
        return []
    try:
        previous = json.loads(body)
    except (ValueError, TypeError):
        return []
    rows = previous.get("top_news_analysis") if isinstance(previous, dict) else None
    if not isinstance(rows, list):
        return []
    available = {n.get("source_item_id") for n in packet.get("news") or []
                 if isinstance(n, dict) and isinstance(n.get("source_item_id"), str)}
    research = packet.get("research") or {}
    contexts = research.get("contexts") if isinstance(research, dict) else None
    contexts = contexts if isinstance(contexts, dict) else {}
    out: list[str] = []
    for problem in problems or []:
        match = _INDEX.match(str(problem))
        if not match or (index := int(match[1])) >= len(rows):
            continue
        row = rows[index]
        sid = row.get("source_item_id") if isinstance(row, dict) else None
        if not isinstance(sid, str) or sid not in available or sid not in legal_ids:
            continue
        candidates = [sid]
        context = contexts.get(sid)
        if "歷史來源" in str(problem) and isinstance(context, dict):
            history = context.get("evidence_ids")
            if isinstance(history, list):
                candidates += [eid for eid in history if isinstance(eid, str)]
        for eid in candidates:
            if eid in legal_ids and eid not in out:
                out.append(eid)
    return out
