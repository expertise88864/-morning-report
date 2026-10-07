"""An old evidence ID is not permission to rewrite an unseen audited claim.

This tracks statement, asset scope and support/counterevidence role, not truth.
Structural repairs, citation removal and edits backed by visible content remain
possible. Anchors are private in-memory values from full-context rounds only.
"""


def _rows(obj):
    value = obj.get("claim_audit") if isinstance(obj, dict) else None
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _records(row):
    assets = row.get("asset_scope")
    assets = tuple(sorted(set(asset for asset in assets if isinstance(asset, str)))) if isinstance(assets, list) else ()
    statement = row.get("statement")
    statement = statement.strip() if isinstance(statement, str) else ""
    for role in ("evidence_ids", "counterevidence_ids"):
        ids = row.get(role)
        for eid in ids if isinstance(ids, list) else ():
            if isinstance(eid, str):
                yield role, eid, statement, assets


def signatures(obj):
    return {record for row in _rows(obj) for record in _records(row)}


def repair_problems(obj, visible_ids, full_context):
    if visible_ids is None:
        return []
    return [f"claim_audit[{index}] 新增或改寫本輪不可見證據的主張／標的／證據角色；"
            "只能保留完整脈絡下的原主張，或移除引用並誠實標示推論／未知"
            for index, row in enumerate(_rows(obj))
            if any(record[1] not in visible_ids and record not in full_context
                   for record in _records(row))]
