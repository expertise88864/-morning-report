"""Find packet-backed news members for clusters named in repair failures."""
from __future__ import annotations

import re


_CLUSTER_REF = re.compile(r"(?<![A-Za-z0-9_-])cluster:[A-Za-z0-9_-]+(?![A-Za-z0-9_-])")


def problem_cluster_members(packet: dict, problems: list, legal_ids: set) -> list[str]:
    """Prioritize citeable member content over unrelated earlier citations."""
    if not isinstance(packet, dict):
        return []
    info = packet.get("news_clusters") or {}
    if not isinstance(info, dict):
        return []
    by_cluster = {
        str(c.get("cluster_id")): c
        for c in info.get("clusters") or []
        if isinstance(c, dict) and c.get("cluster_id")
        and isinstance(c.get("member_source_ids"), (list, tuple))
    }
    available = {
        str(n.get("source_item_id")): n for n in packet.get("news") or []
        if isinstance(n, dict) and n.get("source_item_id")
    }
    out: list[str] = []
    for problem in problems or []:
        for match in _CLUSTER_REF.finditer(str(problem)):
            cluster = by_cluster.get(match.group()) or {}
            members = cluster.get("member_source_ids") or []
            representative = cluster.get("representative_source_id")
            candidates = ([representative] if representative in members else []) + list(members[:1])
            candidates += [m for m in members if available.get(str(m), {}).get("official")]
            picked = 0
            for member in candidates:
                sid = str(member)
                if sid in legal_ids and sid in available and sid not in out:
                    out.append(sid)
                    picked += 1
                    if picked == 3:
                        break
    return out
