"""Project cluster references onto the news actually available to consumers."""
from __future__ import annotations


def project(info: dict, selected: list) -> dict:
    """Keep cluster identity and coverage metadata; rebind missing representatives.

    Selected articles arrive in deterministic editorial order. Missing clusters
    are omitted, but required IDs remain so coverage failures are not hidden.
    """
    ordered = [row["source_item_id"] for row in selected]
    ids = set(ordered)
    clusters = []
    for cluster in info.get("clusters") or []:
        members = [sid for sid in cluster["member_source_ids"] if sid in ids]
        if not members:
            continue
        representative = cluster.get("representative_source_id")
        if representative not in members:
            representative = next(sid for sid in ordered if sid in members)
        clusters.append(dict(cluster, member_source_ids=members,
                             representative_source_id=representative))
    return dict(info, clusters=clusters)
